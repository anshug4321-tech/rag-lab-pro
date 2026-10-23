"""FastAPI app: API routes + static frontend. Run with: python run.py"""
import hashlib
import hmac
import json
import re
import secrets
import uuid
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config, db, importers, jobs, rag, retrieval, settings as app_settings
from backend.dsa import TokenBucket
from backend.embeddings import store
from backend.llm import LLMError
from backend.routers import lab
from backend.utils import check_url_safe
from backend.utils import get_logger

log = get_logger("main")
app = FastAPI(title="RAG Lab Pro")
app.include_router(lab.router)
SECRET = secrets.token_bytes(32)  # new every restart, so old login cookies stop working
OPEN_PATHS = {"/api/login", "/api/health"}
bucket = TokenBucket(config.RATE_CAPACITY, config.RATE_REFILL_PER_SEC)


@app.on_event("startup")
def startup() -> None:
    """Create folders/tables and start the ingestion worker."""
    for d in (config.UPLOAD_DIR, config.EXPORT_DIR, config.CHROMA_DIR):
        d.mkdir(parents=True, exist_ok=True)
    db.init_db()
    jobs.start_worker()


@app.middleware("http")
async def guard(request: Request, call_next):
    """Rate limit API calls, add request id and security headers."""
    if request.url.path.startswith("/api/") and not bucket.allow(_client_ip(request)):
        return JSONResponse({"detail": "Too many requests. Slow down a little."}, status_code=429)
    if config.APP_PASSWORD and request.url.path.startswith("/api/") and request.url.path not in OPEN_PATHS:
        if not hmac.compare_digest(request.cookies.get("rag_auth", ""), _token()):
            return JSONResponse({"detail": "Please log in."}, status_code=401)
    resp = await call_next(request)
    resp.headers["X-Request-ID"] = uuid.uuid4().hex[:8]
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _token() -> str:
    return hmac.new(SECRET, b"ok", hashlib.sha256).hexdigest()


class LoginIn(BaseModel):
    password: str


class LinkIn(BaseModel):
    type: str = Field(pattern="^(url|youtube|official)$")
    url: str = Field(min_length=8, max_length=2000)
    title: str = ""


def _client_ip(request: Request) -> str:
    """Real client IP when behind a hosting proxy (first X-Forwarded-For entry), else the socket IP."""
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "x")


class NotebookIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ChatIn(BaseModel):
    notebook_id: str
    question: str = Field(min_length=1, max_length=2000)


class FeedbackIn(BaseModel):
    message_id: str
    rating: str = Field(pattern="^(up|down)$")
    comment: str = ""


def _need_notebook(nid: str) -> None:
    if not db.one("SELECT id FROM notebooks WHERE id=?", (nid,)):
        raise HTTPException(404, "Notebook not found.")


@app.get("/api/health")
def health() -> dict:
    """Report status of DB, vector store and key presence (never the key itself)."""
    try:
        db.rows("SELECT 1")
        db_ok = True
    except Exception:  # health must never crash
        db_ok = False
    try:
        store()
        vec_ok = True
    except Exception:
        vec_ok = False
    return {"db": db_ok, "vector_store": vec_ok, "gemini_key_present": bool(config.GEMINI_API_KEY)}


@app.post("/api/login")
def login(body: LoginIn) -> JSONResponse:
    """Optional password login (only when APP_PASSWORD is set in .env)."""
    if config.APP_PASSWORD and not hmac.compare_digest(body.password, config.APP_PASSWORD):
        raise HTTPException(401, "Wrong password.")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("rag_auth", _token(), httponly=True, samesite="strict", max_age=86400)
    return resp


@app.get("/api/notebooks")
def list_notebooks() -> list[dict]:
    return db.rows("SELECT * FROM notebooks ORDER BY created_at DESC")


@app.post("/api/notebooks")
def create_notebook(body: NotebookIn) -> dict:
    nid = db.new_id()
    db.run("INSERT INTO notebooks(id,name,created_at) VALUES(?,?,?)", (nid, body.name.strip(), db.now()))
    return {"id": nid, "name": body.name.strip()}


@app.delete("/api/notebooks/{nid}")
def delete_notebook(nid: str) -> dict:
    """Delete a notebook, its sources, chunks and vectors."""
    for s in db.rows("SELECT id FROM sources WHERE notebook_id=?", (nid,)):
        store().delete_source(s["id"])
    db.run("DELETE FROM notebooks WHERE id=?", (nid,))
    retrieval.invalidate(nid)
    return {"ok": True}


@app.get("/api/notebooks/{nid}/sources")
def list_sources(nid: str) -> list[dict]:
    return db.rows("SELECT id,type,title,status,error,warning,language,origin FROM sources "
                   "WHERE notebook_id=? ORDER BY created_at", (nid,))


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name)[:100] or "file"


@app.post("/api/notebooks/{nid}/sources")
async def add_source(nid: str, file: UploadFile = File(...)) -> dict:
    """Upload a PDF/DOCX/TXT. Processing happens in the background queue."""
    _need_notebook(nid)
    name = _safe_name(file.filename or "file")
    if Path(name).suffix.lower() not in config.ALLOWED_EXT:
        raise HTTPException(400, "Only PDF, DOCX and TXT files are allowed.")
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 20 MB.")
    sid = db.new_id()
    path = config.UPLOAD_DIR / f"{sid}_{name}"
    path.write_bytes(data)
    db.run("INSERT INTO sources(id,notebook_id,type,title,origin,status,created_at) VALUES(?,?,?,?,?,?,?)",
           (sid, nid, "file", name, str(path), "queued", db.now()))
    jobs.submit(sid)
    return {"id": sid, "status": "queued"}


@app.post("/api/notebooks/{nid}/links")
def add_link(nid: str, body: LinkIn) -> dict:
    """Add a web link, YouTube video or official-source page. Checked now, imported in the background."""
    _need_notebook(nid)
    url = body.url.strip()
    try:
        if body.type == "youtube":
            importers.youtube_id(url)
        else:
            check_url_safe(url)
            if body.type == "official" and not importers.is_trusted(url):
                raise ValueError("This domain is not on your trusted list (Settings).")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    sid = db.new_id()
    db.run("INSERT INTO sources(id,notebook_id,type,title,origin,status,created_at) VALUES(?,?,?,?,?,?,?)",
           (sid, nid, body.type, body.title or "", url, "queued", db.now()))
    jobs.submit(sid)
    return {"id": sid, "status": "queued"}


@app.get("/api/official/search")
def official_search(q: str) -> list[dict]:
    """Search, showing only allowlisted domains."""
    try:
        return importers.search_official(q)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/sources/{sid}/status")
def source_status(sid: str) -> dict:
    r = db.one("SELECT id,status,error,warning FROM sources WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Source not found.")
    return r


@app.delete("/api/sources/{sid}")
def delete_source(sid: str) -> dict:
    """Delete a source: DB rows (chunks cascade), vectors and the uploaded file."""
    src = db.one("SELECT notebook_id, type, origin FROM sources WHERE id=?", (sid,))
    if not src:
        raise HTTPException(404, "Source not found.")
    store().delete_source(sid)
    db.run("DELETE FROM sources WHERE id=?", (sid,))
    if src["type"] == "file":
        Path(src["origin"]).unlink(missing_ok=True)
    retrieval.invalidate(src["notebook_id"])
    return {"ok": True}


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for ev in events:
        yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"


@app.post("/api/chat")
def chat(body: ChatIn) -> StreamingResponse:
    """Stream an answer with Server-Sent Events."""
    _need_notebook(body.notebook_id)
    return StreamingResponse(_sse(rag.answer_stream(body.notebook_id, body.question)),
                             media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/notebooks/{nid}/messages")
def messages(nid: str) -> list[dict]:
    return db.rows("SELECT id,role,text,citations_json FROM chat_messages WHERE notebook_id=? ORDER BY created_at, rowid", (nid,))


@app.get("/api/chunks/{cid}/context")
def chunk_context(cid: str) -> dict:
    """Return the cited chunk plus one chunk before and after (for the source viewer)."""
    c = db.one("SELECT * FROM chunks WHERE id=?", (cid,))
    if not c:
        raise HTTPException(404, "Passage not found.")
    near = db.rows("SELECT idx,text FROM chunks WHERE source_id=? AND chunk_config=? AND idx IN (?,?)",
                   (c["source_id"], c["chunk_config"], c["idx"] - 1, c["idx"] + 1))
    before = next((n["text"] for n in near if n["idx"] == c["idx"] - 1), "")
    after = next((n["text"] for n in near if n["idx"] == c["idx"] + 1), "")
    s = db.one("SELECT title, type, origin FROM sources WHERE id=?", (c["source_id"],))
    return {"before": before, "text": c["text"], "after": after, "page": c["page"],
            "start_seconds": c["start_seconds"], "title": s["title"], "type": s["type"],
            "url": s["origin"] if s["type"] in ("url", "youtube", "official") else None}


@app.post("/api/feedback")
def feedback(body: FeedbackIn) -> dict:
    db.run("INSERT INTO feedback(id,message_id,rating,comment,created_at) VALUES(?,?,?,?,?)",
           (db.new_id(), body.message_id, body.rating, body.comment[:500], db.now()))
    return {"ok": True}


@app.post("/api/notebooks/{nid}/study/{kind}")
def study(nid: str, kind: str) -> dict:
    """Summary, FAQ or study guide."""
    if kind not in rag.STUDY_PROMPTS:
        raise HTTPException(400, "Unknown kind.")
    try:
        return {"text": rag.study_material(nid, kind)}
    except LLMError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/settings")
def get_settings() -> dict:
    return app_settings.load()


@app.put("/api/settings")
def put_settings(body: dict) -> dict:
    return app_settings.save(body)


@app.delete("/api/data")
def delete_all() -> dict:
    """Delete ALL notebooks and data."""
    for nb in db.rows("SELECT id FROM notebooks"):
        delete_notebook(nb["id"])
    return {"ok": True}


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")

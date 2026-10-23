"""Evaluation Lab API."""
import json
from typing import Iterator

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend import db, evaluation as ev

router = APIRouter(prefix="/api/lab")


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for e in events:
        yield f"event: {e['event']}\ndata: {json.dumps(e['data'], ensure_ascii=False)}\n\n"


class GenIn(BaseModel):
    notebook_id: str
    n: int = Field(default=30, ge=1, le=100)


class QuestionIn(BaseModel):
    notebook_id: str
    question: str = Field(min_length=3)
    language: str = "en"
    expected_answer: str = ""
    gold_source_id: str | None = None
    gold_start: int | None = None
    gold_end: int | None = None


class QuestionPatch(BaseModel):
    question: str | None = None
    expected_answer: str | None = None
    verified: int | None = Field(default=None, ge=0, le=1)


class RunIn(BaseModel):
    notebook_id: str
    name: str = ""
    config: dict = {}
    experiment_id: str | None = None  # pass to resume a paused run


@router.post("/generate")
def generate(body: GenIn) -> StreamingResponse:
    """Auto-generate test questions (SSE progress)."""
    return StreamingResponse(_sse(ev.generate_testset(body.notebook_id, body.n)), media_type="text/event-stream")


@router.get("/questions")
def questions(notebook_id: str) -> list[dict]:
    return db.rows("SELECT * FROM test_questions WHERE notebook_id=? ORDER BY rowid", (notebook_id,))


@router.post("/questions")
def add_question(q: QuestionIn) -> dict:
    qid = db.new_id()
    db.run("""INSERT INTO test_questions(id,notebook_id,question,language,expected_answer,gold_source_id,gold_start,
              gold_end,verified,origin) VALUES(?,?,?,?,?,?,?,?,1,'manual')""",
           (qid, q.notebook_id, q.question, q.language, q.expected_answer, q.gold_source_id, q.gold_start, q.gold_end))
    return {"id": qid}


@router.put("/questions/{qid}")
def edit_question(qid: str, p: QuestionPatch) -> dict:
    for col, val in (("question", p.question), ("expected_answer", p.expected_answer), ("verified", p.verified)):
        if val is not None:
            db.run(f"UPDATE test_questions SET {col}=? WHERE id=?", (val, qid))  # col names are fixed above, not user input
    return {"ok": True}


@router.delete("/questions/{qid}")
def delete_question(qid: str) -> dict:
    db.run("DELETE FROM test_questions WHERE id=?", (qid,))
    return {"ok": True}


@router.post("/questions/import")
async def import_q(notebook_id: str, file: UploadFile = File(...)) -> dict:
    name = (file.filename or "").lower()
    if not name.endswith((".csv", ".json")):
        raise HTTPException(400, "Use a .csv or .json file.")
    try:
        return {"imported": ev.import_questions(notebook_id, name, await file.read(2_000_000))}
    except (ValueError, KeyError, UnicodeError) as exc:
        raise HTTPException(400, "Could not read this file. Check the column names.") from exc


@router.get("/questions/export")
def export_q(notebook_id: str, fmt: str = "json") -> PlainTextResponse:
    return PlainTextResponse(ev.export_questions(notebook_id, "json" if fmt == "json" else "csv"))


@router.post("/run")
def run(body: RunIn) -> StreamingResponse:
    """Create (or resume) an experiment and stream progress."""
    eid = body.experiment_id or ev.create_experiment(body.notebook_id, body.name, body.config)
    if not db.one("SELECT id FROM experiments WHERE id=?", (eid,)):
        raise HTTPException(404, "Experiment not found.")

    def gen() -> Iterator[dict]:
        yield {"event": "started", "data": {"experiment_id": eid}}
        yield from ev.run_experiment(eid)
    return StreamingResponse(_sse(gen()), media_type="text/event-stream")


@router.get("/experiments")
def experiments(notebook_id: str) -> list[dict]:
    return db.rows("SELECT id,name,config_json,status,created_at FROM experiments WHERE notebook_id=? ORDER BY created_at DESC", (notebook_id,))


@router.get("/summary/{eid}")
def summary(eid: str) -> dict:
    return ev.summarize(eid)


@router.get("/results/{eid}")
def results(eid: str) -> list[dict]:
    rows = ev.load_results(eid)
    return [{k: r[k] for k in ("question", "language", "expected_answer", "answer", "hit", "rr", "correct", "faithful",
                               "judge_notes", "latency_ms", "retrieved_json")} for r in rows]


@router.get("/export/{eid}")
def export(eid: str, fmt: str = "csv") -> PlainTextResponse:
    return PlainTextResponse(ev.markdown_report(eid) if fmt == "md" else ev.results_csv(eid))

"""Background ingestion: a FIFO queue and one worker thread."""
import json
import sqlite3
import threading
import time
from pathlib import Path

from backend import db, importers, retrieval, settings as app_settings
from backend.chunking import chunk_text
from backend.dsa import JobQueue, page_for_offset
from backend.embeddings import embed_passages, store
from backend.llm import LLMError
from backend.parsing import parse_file
from backend.utils import detect_language, get_logger, sha256_text

log = get_logger("jobs")
queue = JobQueue()
_lock = threading.Lock()
_started = False


def submit(source_id: str) -> None:
    """Add a source to the ingestion queue."""
    with _lock:
        queue.push(source_id)


def start_worker() -> None:
    """Start the single background worker (once)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="ingest-worker").start()


def _loop() -> None:
    while True:
        with _lock:
            job = queue.pop()
        if job is None:
            time.sleep(0.5)
            continue
        try:
            process(job)
            status = "ready"
        except (ValueError, OSError, LLMError, sqlite3.Error, RuntimeError, ImportError) as exc:
            log.error("Ingest failed (%s): %s", job, type(exc).__name__)
            db.run("UPDATE sources SET status='failed', error=? WHERE id=?", (_friendly(exc), job))
            status = "failed"
        with _lock:
            queue.set_status(job, status)


def _friendly(exc: Exception) -> str:
    return str(exc) if isinstance(exc, (ValueError, LLMError)) else \
        "Could not read this source. Check the file and the server window."


def process(source_id: str) -> None:
    """Parse (if a file) then index one source."""
    src = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
    if src is None:
        return
    db.run("UPDATE sources SET status='processing', error=NULL WHERE id=?", (source_id,))
    if src["type"] == "file":
        text, offsets, warning = parse_file(Path(src["origin"]))
        finish(source_id, text, offsets, warning)
    elif src["type"] in ("url", "official"):
        title, text = importers.fetch_url(src["origin"])
        db.run("UPDATE sources SET title=? WHERE id=? AND (title IS NULL OR title='')", (title, source_id))
        finish(source_id, text, [], None)
    elif src["type"] == "youtube":
        title, text, secs = importers.youtube_transcript(src["origin"])
        db.run("UPDATE sources SET title=? WHERE id=? AND (title IS NULL OR title='')", (title, source_id))
        finish(source_id, text, [], None, seconds_for_offset=secs)
    elif src["type"] in ("text", "link"):
        finish(source_id, src["full_text"] or "", [], None)
    else:
        raise ValueError("Unknown source type.")


def finish(source_id: str, text: str, offsets: list[int], warning: str | None = None,
           seconds_for_offset=None) -> None:
    """Hash, de-duplicate, chunk, embed, and mark ready. `seconds_for_offset` is for YouTube."""
    src = db.one("SELECT notebook_id FROM sources WHERE id=?", (source_id,))
    digest = sha256_text(text)
    dup = db.one("SELECT id FROM sources WHERE notebook_id=? AND content_hash=? AND id!=?",
                 (src["notebook_id"], digest, source_id))
    if dup:
        raise ValueError("Duplicate: this content is already in the notebook (skipped).")
    cfg = app_settings.load()
    size, backend = int(cfg["chunk_size"]), cfg["embedding_backend"]
    pieces = chunk_text(text, size, float(cfg["overlap"]))
    if not pieces:
        raise ValueError("No readable text found.")
    lang = detect_language(text[:2000])
    ids = [db.new_id() for _ in pieces]
    vecs = embed_passages([p[0] for p in pieces], backend)
    with db.connect() as c:
        c.execute("UPDATE sources SET content_hash=?, language=?, full_text=?, page_offsets_json=?, warning=? WHERE id=?",
                  (digest, lang, text, json.dumps(offsets), warning, source_id))
        c.executemany(
            "INSERT INTO chunks(id,source_id,chunk_config,idx,text,start_offset,end_offset,page,start_seconds,language) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(cid, source_id, str(size), i, t, s, e, page_for_offset(offsets, s) if offsets else None,
              seconds_for_offset(s) if seconds_for_offset else None, detect_language(t))
             for i, (cid, (t, s, e)) in enumerate(zip(ids, pieces))])
    store().add(retrieval.collection_name(str(size), backend), ids, vecs, [p[0] for p in pieces],
                [{"source_id": source_id, "notebook_id": src["notebook_id"]} for _ in pieces])
    db.run("UPDATE sources SET status='ready' WHERE id=?", (source_id,))
    retrieval.invalidate(src["notebook_id"])

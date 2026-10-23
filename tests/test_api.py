"""API smoke test with a FAKE embedder and FAKE LLM (no key, no model download needed)."""
import hashlib
import time

import pytest
from fastapi.testclient import TestClient


def fake_vec(text: str) -> list[float]:
    """Deterministic 8-number vector from text (only for tests)."""
    h = hashlib.sha256(text.encode()).digest()
    v = [b / 255 for b in h[:8]]
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from backend import config, db, embeddings, jobs, llm, main, retrieval
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "up")
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "s.json")
    monkeypatch.setattr(embeddings, "_store", None)
    monkeypatch.setattr(jobs, "embed_passages", lambda texts, backend="local": [fake_vec(t) for t in texts])
    monkeypatch.setattr(retrieval, "embed_query", lambda q, backend="local": fake_vec(q))

    def fake_stream(prompt, usage, system=None, model=None):
        usage.update(tokens_in=10, tokens_out=3)
        yield "The fee is 500 [1]."
    monkeypatch.setattr(llm, "stream", fake_stream)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: ("ok", 1, 1))
    with TestClient(main.app) as c:
        yield c


def wait_ready(c, sid):
    for _ in range(40):
        if c.get(f"/api/sources/{sid}/status").json()["status"] in ("ready", "failed"):
            return c.get(f"/api/sources/{sid}/status").json()
        time.sleep(0.25)
    raise AssertionError("ingestion timed out")


def test_full_flow(client):
    nb = client.post("/api/notebooks", json={"name": "t"}).json()["id"]
    doc = ("The annual fee is 500 rupees. Hostel is separate. " * 20).encode()
    sid = client.post(f"/api/notebooks/{nb}/sources", files={"file": ("a.txt", doc)}).json()["id"]
    assert wait_ready(client, sid)["status"] == "ready"
    # same file again -> skipped as duplicate
    sid2 = client.post(f"/api/notebooks/{nb}/sources", files={"file": ("b.txt", doc)}).json()["id"]
    assert wait_ready(client, sid2)["status"] == "failed"
    r = client.post("/api/chat", json={"notebook_id": nb, "question": "What is the fee?"})
    assert "event: done" in r.text and "The fee is 500" in r.text
    # delete removes chunks
    client.delete(f"/api/sources/{sid}")
    from backend import db
    assert db.rows("SELECT id FROM chunks WHERE source_id=?", (sid,)) == []


def test_bad_upload_type(client):
    nb = client.post("/api/notebooks", json={"name": "t"}).json()["id"]
    r = client.post(f"/api/notebooks/{nb}/sources", files={"file": ("x.exe", b"hi")})
    assert r.status_code == 400


def test_health_never_shows_key(client):
    assert "gemini_key_present" in client.get("/api/health").json()

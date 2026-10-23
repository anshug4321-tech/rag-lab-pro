"""Search: vector, keyword (our BM25), hybrid (RRF), optional MMR, LLM rerank, query rewrite."""
import json

from backend import config, db, llm
from backend.dsa import BM25, rrf, top_k
from backend.embeddings import embed_query, store
from backend.utils import get_logger

log = get_logger("retrieval")
_bm25_cache: dict[tuple[str, str], BM25] = {}


def invalidate(notebook_id: str | None = None) -> None:
    """Forget cached keyword indexes after sources change."""
    for key in [k for k in _bm25_cache if notebook_id is None or k[0] == notebook_id]:
        del _bm25_cache[key]


def collection_name(chunk_config: str, backend: str) -> str:
    """Chroma collection name for a chunk size + embedding backend."""
    return f"chunks_{chunk_config}_{backend}"


def _bm25(notebook_id: str, chunk_config: str) -> BM25:
    key = (notebook_id, chunk_config)
    if key not in _bm25_cache:
        idx = BM25()
        for r in db.rows("""SELECT c.id, c.text FROM chunks c JOIN sources s ON s.id=c.source_id
                            WHERE s.notebook_id=? AND c.chunk_config=? AND s.status='ready'""",
                         (notebook_id, chunk_config)):
            idx.add(r["id"], r["text"])
        _bm25_cache[key] = idx
    return _bm25_cache[key]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _mmr(cands: list[dict], embs: dict[str, list[float]], k: int, lam: float = 0.7) -> list[dict]:
    """Maximal Marginal Relevance: pick relevant but different chunks. O(k * n)."""
    chosen: list[dict] = []
    pool = [c for c in cands if c["chunk_id"] in embs]
    rest = [c for c in cands if c["chunk_id"] not in embs]
    while pool and len(chosen) < k:
        def value(c: dict) -> float:
            rel = c["sim"] if c["sim"] is not None else 0.5
            div = max((_dot(embs[c["chunk_id"]], embs[x["chunk_id"]]) for x in chosen), default=0.0)
            return lam * rel - (1 - lam) * div
        best = max(pool, key=value)
        chosen.append(best)
        pool.remove(best)
    return chosen + rest[: max(0, k - len(chosen))]


def llm_rerank(query: str, cands: list[dict], model: str | None = None) -> list[dict]:
    """Ask the LLM to score each passage 0-10 (one call, JSON). Falls back to input order."""
    listing = "\n".join(f"[{i}] {c['text'][:300]}" for i, c in enumerate(cands))
    prompt = (f"Question: {query}\nScore how useful each passage is for answering (0-10). "
              f"Reply ONLY with a JSON list of {len(cands)} numbers.\n{listing}")
    try:
        text, _, _ = llm.generate(prompt, model=model, json_mode=True)
        scores = json.loads(text)
        if not isinstance(scores, list) or len(scores) != len(cands):
            raise ValueError("bad length")
        scores = [float(s) for s in scores]
    except (llm.LLMError, ValueError, TypeError) as exc:
        log.info("Rerank skipped: %s", type(exc).__name__)
        return cands
    order = sorted(range(len(cands)), key=lambda i: scores[i], reverse=True)
    return [cands[i] for i in order]


def rewrite_query(question: str, history: list[dict], lang: str, model: str | None = None) -> str:
    """Make a follow-up question standalone. For Hinglish, rewrite in Hindi (Devanagari) or English."""
    if not history and lang != "hinglish":
        return question
    convo = "\n".join(f"{m['role']}: {m['text'][:300]}" for m in history[-4:])
    prompt = ("Rewrite the last question as one standalone search query. If it is Hindi written in "
              "English letters (Hinglish), write it in clear English. Reply with the query only.\n"
              f"Chat:\n{convo}\nLast question: {question}")
    try:
        text, _, _ = llm.generate(prompt, model=model)
        return text.strip().strip('"') or question
    except llm.LLMError:
        return question


def search(notebook_id: str, query: str, *, mode: str = "hybrid", k: int = 5, rerank: bool = False,
           mmr: bool = False, chunk_config: str = "500", backend: str = "local",
           collection: str | None = None, model: str | None = None) -> list[dict]:
    """Return top-k chunk dicts with rank, score, sim, text, source info."""
    col = collection or collection_name(chunk_config, backend)
    n = config.CANDIDATES
    vec_hits, embs = [], {}
    if mode in ("vector", "hybrid") or mmr:
        vec_hits = store().query(col, embed_query(query, backend), notebook_id, n)
        embs = {i: e for i, _, e in vec_hits}
    sims = {i: s for i, s, _ in vec_hits}
    kw_hits = _bm25(notebook_id, chunk_config).search(query, n) if mode in ("keyword", "hybrid") else []
    if mode == "vector":
        ranked = [(i, s) for i, s, _ in vec_hits]
    elif mode == "keyword":
        ranked = kw_hits
    else:
        ranked = rrf([[i for i, _, _ in vec_hits], [i for i, _ in kw_hits]], config.RRF_K)
    ranked = top_k(ranked, n, key=lambda x: x[1])
    if not ranked:
        return []
    ids = [i for i, _ in ranked]
    info = {r["id"]: r for r in db.rows(
        f"""SELECT c.id, c.text, c.page, c.start_seconds, c.start_offset, c.end_offset, c.source_id,
                   s.title FROM chunks c JOIN sources s ON s.id=c.source_id
            WHERE c.id IN ({','.join('?' * len(ids))})""", tuple(ids))}
    cands = [{"chunk_id": i, "score": round(sc, 4), "sim": sims.get(i), "text": info[i]["text"],
              "source_id": info[i]["source_id"], "source_title": info[i]["title"], "page": info[i]["page"],
              "start_seconds": info[i]["start_seconds"], "start": info[i]["start_offset"],
              "end": info[i]["end_offset"]} for i, sc in ranked if i in info]
    if rerank:
        cands = llm_rerank(query, cands[:n], model)
    if mmr:
        missing = [c["chunk_id"] for c in cands if c["chunk_id"] not in embs]
        if missing:
            got = store().collection(col).get(ids=missing, include=["embeddings"])
            embs.update(dict(zip(got["ids"], got["embeddings"])))
        cands = _mmr(cands, embs, k)
    out = cands[:k]
    for rank, c in enumerate(out, start=1):
        c["rank"] = rank
    return out

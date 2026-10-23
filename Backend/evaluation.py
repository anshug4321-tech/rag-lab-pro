"""Evaluation Lab engine: test sets, experiments, metrics, reports.
Honest notes: the judge is an LLM and can be wrong; the same model may write and judge questions;
samples are small. Spot-check results by hand. Nothing here invents numbers."""
import csv
import io
import json
import math
import random
import re
from typing import Iterator

from backend import config, db, llm, retrieval, settings as app_settings
from backend.chunking import chunk_text
from backend.dsa import overlaps, page_for_offset
from backend.embeddings import embed_passages, store
from backend.rag import build_prompt, verify_citations
from backend.utils import detect_language, get_logger

log = get_logger("evaluation")
REFUSE = "__REFUSE__"  # marker: this question cannot be answered from the documents
EXP_DEFAULTS = {"chunk_size": 500, "search_mode": "hybrid", "rerank": False, "mmr": False, "rewrite": False,
                "top_k": 5, "lang": "both", "include_unverified": False, "judge_model": config.JUDGE_MODEL}


# ---------- pure metric helpers (tested without any API) ----------
def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile. None for empty list."""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def covered_length(intervals: list[tuple[int, int]]) -> int:
    """Total length covered by a list of (start, end) after merging overlaps. O(n log n)."""
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if cur_e is None or s > cur_e:
            total += (cur_e - cur_s) if cur_e is not None else 0
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    return total + ((cur_e - cur_s) if cur_e is not None else 0)


def retrieval_metrics(passages: list[dict], gold_source: str, gs: int, ge: int) -> dict:
    """Hit@k (any retrieved chunk overlaps the gold span), reciprocal rank, and Recall@k
    (fraction of gold characters covered by retrieved chunks of the gold source)."""
    hit, rr, parts = 0, 0.0, []
    for p in passages:
        if p["source_id"] == gold_source and overlaps(p["start"], p["end"], gs, ge):
            hit = 1
            rr = rr or 1.0 / p["rank"]
            parts.append((max(gs, p["start"]), min(ge, p["end"])))
    return {"hit": hit, "rr": rr, "recall": covered_length(parts) / max(1, ge - gs)}


def citation_accuracy(cited: list[int], passages: list[dict], gold_source: str, gs: int, ge: int) -> float | None:
    """Share of cited passages that overlap the gold span. None if nothing was cited."""
    if not cited:
        return None
    ok = sum(1 for n in cited if passages[n - 1]["source_id"] == gold_source
             and overlaps(passages[n - 1]["start"], passages[n - 1]["end"], gs, ge))
    return ok / len(cited)


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def metrics_for(rows: list[dict]) -> dict:
    """Aggregate result rows (each has the fields saved by run_experiment)."""
    ans = [r for r in rows if not r["refuse_q"]]
    unans = [r for r in rows if r["refuse_q"]]
    lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    return {
        "n": len(rows), "n_answerable": len(ans), "n_unanswerable": len(unans),
        "hit_at_k": _mean([r["hit"] for r in ans]), "recall_at_k": _mean([r["recall"] for r in ans]),
        "mrr": _mean([r["rr"] for r in ans if r["hit"] is not None]),
        "correctness": _mean([r["correct"] for r in ans]), "faithfulness": _mean([r["faithful"] for r in ans]),
        "citation_accuracy": _mean([r["cite_acc"] for r in ans]),
        "correct_refusal_rate": _mean([r["correct"] for r in unans]),
        "latency_p50_ms": percentile(lat, 50), "latency_p95_ms": percentile(lat, 95),
        "tokens": sum(r["tokens"] or 0 for r in rows), "est_cost_usd": round(sum(r["cost"] or 0 for r in rows), 6)}


def load_results(exp_id: str) -> list[dict]:
    """Result rows joined with their question, extras flattened."""
    out = []
    for r in db.rows("""SELECT r.*, q.question, q.expected_answer, q.pair_id FROM experiment_results r
                        JOIN test_questions q ON q.id=r.question_id WHERE r.experiment_id=?""", (exp_id,)):
        ex = json.loads(r.pop("extra_json") or "{}")
        r.update(recall=ex.get("recall"), cite_acc=ex.get("citation_accuracy"), cost=ex.get("cost", 0),
                 refuse_q=r["expected_answer"] == REFUSE, refused=ex.get("refused"))
        out.append(r)
    return out


def summarize(exp_id: str) -> dict:
    """Overall metrics + by language + a plain-words confidence note."""
    rows = load_results(exp_id)
    by_lang = {lang: metrics_for([r for r in rows if r["language"] == lang]) for lang in ("en", "hi")}
    n = len(rows)
    note = (f"Based on only {n} questions. Treat differences as hints, not proof. Spot-check the judge by hand."
            if n < 100 else f"Based on {n} questions. The judge can still be wrong, so spot-check by hand.")
    return {"overall": metrics_for(rows), "by_language": by_lang, "confidence_note": note}


# ---------- JSON helper ----------
def _json(text: str) -> dict | list:
    """Parse model JSON, tolerating ``` fences."""
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except ValueError as exc:
        raise ValueError("The model did not return valid JSON.") from exc


# ---------- test set ----------
def generate_testset(nb: str, n: int, model: str | None = None) -> Iterator[dict]:
    """Yield progress events while making n question pairs (EN + faithful HI) and some out-of-scope questions."""
    size = str(app_settings.load()["chunk_size"])
    pool = db.rows("""SELECT c.text, c.source_id, c.start_offset, c.end_offset FROM chunks c
                      JOIN sources s ON s.id=c.source_id WHERE s.notebook_id=? AND s.status='ready'
                      AND c.chunk_config=? AND length(c.text)>150""", (nb, size))
    if not pool:
        yield {"event": "error", "data": "No usable passages yet. Add and process a source first."}
        return
    sample = random.sample(pool, min(n, len(pool)))
    made = 0
    for i, ch in enumerate(sample, start=1):
        prompt = ("From the passage, write ONE question that the passage answers, and a short answer. "
                  "Give both in English AND in Hindi (Devanagari); the Hindi must say the same thing. Reply ONLY JSON: "
                  '{"question_en":"","answer_en":"","question_hi":"","answer_hi":""}\n'
                  f"<PASSAGE>\n{ch['text']}\n</PASSAGE>")
        try:
            d = _json(llm.generate(prompt, model=model, json_mode=True)[0])
            pair = db.new_id()
            for lang in ("en", "hi"):
                db.run("""INSERT INTO test_questions(id,notebook_id,question,language,expected_answer,gold_source_id,
                          gold_start,gold_end,verified,origin,pair_id) VALUES(?,?,?,?,?,?,?,?,0,'auto',?)""",
                       (db.new_id(), nb, d[f"question_{lang}"], lang, d[f"answer_{lang}"], ch["source_id"],
                        ch["start_offset"], ch["end_offset"], pair))
            made += 1
        except (llm.LLMError, ValueError, KeyError, TypeError) as exc:
            log.info("Question skipped: %s", type(exc).__name__)
        yield {"event": "progress", "data": {"done": i, "total": len(sample) + 1, "made": made}}
    _out_of_scope(nb, pool, model)
    yield {"event": "done", "data": {"pairs": made}}


def _out_of_scope(nb: str, pool: list[dict], model: str | None) -> None:
    """Add questions that the documents cannot answer. The correct behaviour is to refuse."""
    snippets = "\n".join(c["text"][:200] for c in random.sample(pool, min(4, len(pool))))
    prompt = ("Write 4 questions on topics that these passages do NOT cover (2 English, 2 Hindi in Devanagari). "
              f'Reply ONLY JSON: {{"questions":["",...]}}\n<PASSAGES>\n{snippets}\n</PASSAGES>')
    try:
        for q in _json(llm.generate(prompt, model=model, json_mode=True)[0])["questions"]:
            db.run("""INSERT INTO test_questions(id,notebook_id,question,language,expected_answer,verified,origin)
                      VALUES(?,?,?,?,?,0,'auto')""", (db.new_id(), nb, q, "hi" if detect_language(q) == "hi" else "en", REFUSE))
    except (llm.LLMError, ValueError, KeyError, TypeError) as exc:
        log.info("Out-of-scope questions skipped: %s", type(exc).__name__)


def import_questions(nb: str, filename: str, data: bytes) -> int:
    """Import a CSV or JSON test set. Columns: question, language, expected_answer, gold_source_id, gold_start, gold_end."""
    text = data.decode("utf-8-sig")
    items = json.loads(text) if filename.endswith(".json") else list(csv.DictReader(io.StringIO(text)))
    count = 0
    for it in items:
        q = (it.get("question") or "").strip()
        if not q:
            continue
        db.run("""INSERT INTO test_questions(id,notebook_id,question,language,expected_answer,gold_source_id,
                  gold_start,gold_end,verified,origin) VALUES(?,?,?,?,?,?,?,?,0,'manual')""",
               (db.new_id(), nb, q, it.get("language") or detect_language(q), it.get("expected_answer") or "",
                it.get("gold_source_id") or None, int(it["gold_start"]) if it.get("gold_start") not in (None, "") else None,
                int(it["gold_end"]) if it.get("gold_end") not in (None, "") else None))
        count += 1
    return count


def export_questions(nb: str, fmt: str) -> str:
    """Export a test set as JSON or CSV text."""
    rows = db.rows("SELECT question,language,expected_answer,gold_source_id,gold_start,gold_end,verified FROM test_questions WHERE notebook_id=?", (nb,))
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2)
    return _csv(rows, ["question", "language", "expected_answer", "gold_source_id", "gold_start", "gold_end", "verified"])


def _csv(rows: list[dict], cols: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


# ---------- experiments ----------
def create_experiment(nb: str, name: str, cfg: dict) -> str:
    """Save an experiment (config merged over defaults)."""
    full = {**EXP_DEFAULTS, **cfg}
    eid = db.new_id()
    db.run("INSERT INTO experiments(id,notebook_id,name,config_json,status,created_at) VALUES(?,?,?,?,?,?)",
           (eid, nb, name or f"Run {eid[:4]}", json.dumps(full), "created", db.now()))
    return eid


def _ensure_chunks(nb: str, size: str, backend: str, col: str, overlap: float) -> list[str]:
    """Make sure every ready source has chunks for this size. Returns ids of sources we had to chunk."""
    added = []
    for s in db.rows("SELECT id, full_text, page_offsets_json FROM sources WHERE notebook_id=? AND status='ready'", (nb,)):
        if db.one("SELECT 1 AS x FROM chunks WHERE source_id=? AND chunk_config=? LIMIT 1", (s["id"], size)):
            continue
        offsets = json.loads(s["page_offsets_json"] or "[]")
        pieces = chunk_text(s["full_text"], int(size), overlap)
        if not pieces:
            continue
        ids = [db.new_id() for _ in pieces]
        vecs = embed_passages([p[0] for p in pieces], backend)
        with db.connect() as c:
            c.executemany("INSERT INTO chunks(id,source_id,chunk_config,idx,text,start_offset,end_offset,page,language) VALUES(?,?,?,?,?,?,?,?,?)",
                          [(cid, s["id"], size, i, t, st, en, page_for_offset(offsets, st) if offsets else None, detect_language(t))
                           for i, (cid, (t, st, en)) in enumerate(zip(ids, pieces))])
        store().add(col, ids, vecs, [p[0] for p in pieces], [{"source_id": s["id"], "notebook_id": nb}] * len(pieces))
        added.append(s["id"])
    return added


def _pick_questions(nb: str, cfg: dict) -> list[dict]:
    sql, args = "SELECT * FROM test_questions WHERE notebook_id=?", [nb]
    if not cfg["include_unverified"]:
        sql += " AND verified=1"
    if cfg["lang"] in ("en", "hi"):
        sql += " AND language=?"
        args.append(cfg["lang"])
    return db.rows(sql + " ORDER BY rowid", tuple(args))


def _judge(q: dict, answer: str, passages: list[dict], model: str) -> tuple[int, int, str, int, int]:
    """LLM judge: is the answer correct vs the expected answer, and is it supported by the context?"""
    ctx = "\n".join(f"[{p['rank']}] {p['text'][:600]}" for p in passages)
    prompt = ("You are a strict grader. Compare the ANSWER with the EXPECTED answer (same meaning = correct, "
              "language does not matter). Faithful = every claim in the ANSWER is supported by the CONTEXT. "
              'Reply ONLY JSON: {"correct":0 or 1,"faithful":0 or 1,"reason":"one short sentence"}\n'
              f"QUESTION: {q['question']}\nEXPECTED: {q['expected_answer']}\nANSWER: {answer}\n<CONTEXT>\n{ctx}\n</CONTEXT>")
    text, ti, to = llm.generate(prompt, model=model, json_mode=True)
    d = _json(text)
    return int(bool(d.get("correct"))), int(bool(d.get("faithful"))), str(d.get("reason", ""))[:300], ti, to


def _eval_one(q: dict, cfg: dict, nb: str, size: str, backend: str, col: str) -> dict:
    """Run one test question through retrieval, answering and judging."""
    import time
    chat_model = app_settings.load()["chat_model"]
    t0 = time.perf_counter()
    query = retrieval.rewrite_query(q["question"], [], detect_language(q["question"]), chat_model) if cfg["rewrite"] else q["question"]
    passages = retrieval.search(nb, query, mode=cfg["search_mode"], k=int(cfg["top_k"]), rerank=cfg["rerank"], mmr=cfg["mmr"],
                                chunk_config=size, backend=backend, collection=col, model=chat_model)
    ti = to = 0
    if passages:
        answer, ti, to = llm.generate(build_prompt(q["question"], passages), config.SYSTEM_PROMPT, chat_model)
    else:
        answer = config.REFUSAL_HI if q["language"] == "hi" else config.REFUSAL_EN
    latency = int((time.perf_counter() - t0) * 1000)
    refused = answer.strip().startswith((config.REFUSAL_EN, config.REFUSAL_HI))
    extra = {"refused": int(refused)}
    hit = rr = cite = None
    jt = (0, 0)
    if q["expected_answer"] == REFUSE:
        correct, faithful, reason = int(refused), 1, "Correct refusal" if refused else "Should have refused"
    else:
        if q["gold_source_id"] is not None and q["gold_start"] is not None:
            m = retrieval_metrics(passages, q["gold_source_id"], q["gold_start"], q["gold_end"])
            hit, rr, extra["recall"] = m["hit"], m["rr"], m["recall"]
            valid, _ = verify_citations(answer, len(passages))
            cite = citation_accuracy(valid, passages, q["gold_source_id"], q["gold_start"], q["gold_end"])
        correct, faithful, reason, ji, jo = _judge(q, answer, passages, cfg["judge_model"])
        jt = (ji, jo)
    extra.update(citation_accuracy=cite, cost=llm.estimate_cost(ti + jt[0], to + jt[1]), judge_tokens=sum(jt))
    shown = [{"rank": p["rank"], "source_id": p["source_id"], "start": p["start"], "end": p["end"],
              "page": p["page"], "text": p["text"][:300]} for p in passages]
    return {"retrieved": shown, "answer": answer, "hit": hit, "rr": rr, "correct": correct, "faithful": faithful,
            "notes": reason, "latency": latency, "tokens": ti + to, "extra": extra}


def run_experiment(exp_id: str) -> Iterator[dict]:
    """Run (or resume) an experiment. Yields progress events. Temporary chunks and the vector collection
    for non-live chunk sizes are deleted at the end, even if the run stops early."""
    exp = db.one("SELECT * FROM experiments WHERE id=?", (exp_id,))
    cfg, nb = {**EXP_DEFAULTS, **json.loads(exp["config_json"])}, exp["notebook_id"]
    live = app_settings.load()
    size, backend = str(cfg["chunk_size"]), live["embedding_backend"]
    use_main = size == str(live["chunk_size"])
    col = retrieval.collection_name(size, backend) if use_main else f"tmp_{exp_id}_{size}"
    status, created = "paused", []
    db.run("UPDATE experiments SET status='running' WHERE id=?", (exp_id,))
    try:
        yield {"event": "progress", "data": {"stage": "Preparing chunks", "done": 0, "total": 0}}
        created = _ensure_chunks(nb, size, backend, col, float(live["overlap"]))
        retrieval.invalidate(nb)
        questions = _pick_questions(nb, cfg)
        if not questions:
            yield {"event": "error", "data": "No questions to run. Approve some questions in the Test set tab first."}
            status = "created"
            return
        done_ids = {r["question_id"] for r in db.rows("SELECT question_id FROM experiment_results WHERE experiment_id=?", (exp_id,))}
        for i, q in enumerate(questions, start=1):
            if q["id"] in done_ids:
                continue
            try:
                r = _eval_one(q, cfg, nb, size, backend, col)
            except (llm.LLMError, ValueError) as exc:
                log.info("Question failed: %s", type(exc).__name__)
                yield {"event": "warning", "data": f"Question {i} skipped: {exc}"}
                continue
            db.run("""INSERT INTO experiment_results(id,experiment_id,question_id,retrieved_json,answer,hit,rr,correct,
                      faithful,judge_notes,latency_ms,tokens,language,extra_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (db.new_id(), exp_id, q["id"], json.dumps(r["retrieved"], ensure_ascii=False), r["answer"], r["hit"], r["rr"],
                    r["correct"], r["faithful"], r["notes"], r["latency"], r["tokens"], q["language"], json.dumps(r["extra"])))
            yield {"event": "progress", "data": {"stage": "Running", "done": i, "total": len(questions)}}
        status = "complete"
        yield {"event": "done", "data": {"experiment_id": exp_id}}
    finally:
        db.run("UPDATE experiments SET status=? WHERE id=?", (status, exp_id))
        if not use_main:
            store().drop(col)
            for sid in created:
                db.run("DELETE FROM chunks WHERE source_id=? AND chunk_config=?", (sid, size))
            retrieval.invalidate(nb)


# ---------- reports ----------
PCT = ["hit_at_k", "recall_at_k", "mrr", "correctness", "faithfulness", "citation_accuracy", "correct_refusal_rate"]


def _fmt(v) -> str:
    return "[X]" if v is None else str(v)


def markdown_report(exp_id: str) -> str:
    """Markdown report. Missing values stay [X]."""
    exp = db.one("SELECT name, config_json FROM experiments WHERE id=?", (exp_id,))
    s = summarize(exp_id)
    lines = [f"# Experiment: {exp['name']}", "", "```json", exp["config_json"], "```", "",
             f"_{s['confidence_note']}_", "", "| Metric | All | English | Hindi |", "|---|---|---|---|"]
    for k in PCT + ["latency_p50_ms", "latency_p95_ms", "tokens", "est_cost_usd", "n"]:
        lines.append(f"| {k} | {_fmt(s['overall'][k])} | {_fmt(s['by_language']['en'][k])} | {_fmt(s['by_language']['hi'][k])} |")
    lines += ["", "Cost is an estimate. LLM judge scores can be wrong: please spot-check."]
    return "\n".join(lines)


def results_csv(exp_id: str) -> str:
    """Per-question results as CSV."""
    rows = load_results(exp_id)
    cols = ["question", "language", "expected_answer", "answer", "hit", "rr", "recall", "correct", "faithful",
            "cite_acc", "latency_ms", "tokens", "judge_notes"]
    return _csv(rows, cols)

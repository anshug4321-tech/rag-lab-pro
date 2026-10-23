// Evaluation Lab UI. Five sub-tabs. Plain DOM code, no framework.
import { api, j, post, streamPost } from "./api.js";

const METRICS = [["hit_at_k", "Hit@k", 1], ["recall_at_k", "Recall@k", 1], ["mrr", "MRR", 1], ["correctness", "Answer correctness", 1],
  ["faithfulness", "Faithfulness", 1], ["citation_accuracy", "Citation accuracy", 1], ["correct_refusal_rate", "Correct refusal rate", 1],
  ["latency_p50_ms", "Latency p50 (ms)"], ["latency_p95_ms", "Latency p95 (ms)"], ["tokens", "Tokens"], ["est_cost_usd", "Est. cost (USD)"]];
const SUBS = [["test", "Test set"], ["run", "Run experiment"], ["results", "Results"], ["lang", "Hindi vs English"], ["ab", "A/B compare"]];
const REFUSE = "__REFUSE__";
let sub = "test", nb = null, toastFn = (m) => alert(m);

// h("div", {class:"x", onclick:fn}, child, "text") -> element
function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;  // skip empty props (e.g. checked: null)
    if (k.startsWith("on")) e[k] = v; else if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  kids.flat().forEach((c) => e.append(c instanceof Node ? c : document.createTextNode(c ?? "")));
  return e;
}
const fmt = (v) => (v === null || v === undefined ? "[X]" : v);
const root = () => document.getElementById("labRoot");

export function renderLab(state, toast) {
  nb = state.nb; toastFn = toast || toastFn;
  const tabs = h("div", { class: "subtabs" }, SUBS.map(([id, label]) =>
    h("button", { class: "tab" + (id === sub ? " on" : ""), onclick: () => { sub = id; renderLab(state, toast); } }, label)));
  const body = h("div");
  root().replaceChildren(tabs, body);
  ({ test: testView, run: runView, results: resultsView, lang: langView, ab: abView })[sub](body);
}

// ---------- 1. test set ----------
async function testView(box) {
  const status = h("span", { class: "muted small" });
  const list = h("div");
  const n = h("input", { type: "number", value: "30", min: "1", max: "100", style: "width:80px", "aria-label": "How many questions" });
  const gen = h("button", { class: "pill", onclick: async () => {
    gen.disabled = true;
    try {
      await streamPost("/api/lab/generate", { notebook_id: nb, n: +n.value }, (ev, d) => {
        if (ev === "progress") status.textContent = `Making questions… ${d.done}/${d.total}`;
        if (ev === "error") toastFn(d);
        if (ev === "done") status.textContent = `Made ${d.pairs} English + Hindi pairs. Review and approve them below.`;
      });
    } catch (e) { toastFn(e.message); }
    gen.disabled = false; load();
  } }, "Generate");
  const mq = h("input", { placeholder: "Add your own question", style: "flex:1" }), ma = h("input", { placeholder: "Expected answer", style: "flex:1" });
  const imp = h("input", { type: "file", accept: ".csv,.json", onchange: async (e) => {
    const f = new FormData(); f.append("file", e.target.files[0]);
    try { const r = await j(`/api/lab/questions/import?notebook_id=${nb}`, { method: "POST", body: f }); toastFn(`Imported ${r.imported}`); load(); } catch (x) { toastFn(x.message); }
  } });
  box.append(
    h("div", { class: "card" }, h("div", { class: "row" }, n, gen, status),
      h("p", { class: "muted small" }, "Each question is linked to the passage it came from (gold evidence). Approve the good ones; official runs use approved questions only.")),
    h("div", { class: "card" }, h("div", { class: "row" }, mq, ma, h("button", { class: "pill", onclick: async () => {
      if (mq.value.trim().length < 3) return toastFn("Write a question first.");
      await post("/api/lab/questions", { notebook_id: nb, question: mq.value, expected_answer: ma.value, language: /[\u0900-\u097F]/.test(mq.value) ? "hi" : "en" });
      mq.value = ma.value = ""; load();
    } }, "Add")), h("p", { class: "muted small" }, "Manual questions have no gold passage, so Hit@k and MRR skip them. Correctness is still judged.")),
    h("div", { class: "row" },
      h("a", { href: `/api/lab/questions/export?notebook_id=${nb}&fmt=json`, download: "questions.json" }, "Export JSON"),
      h("a", { href: `/api/lab/questions/export?notebook_id=${nb}&fmt=csv`, download: "questions.csv" }, "Export CSV"), h("span", { class: "muted small" }, "Import:"), imp,
      h("button", { class: "ghost", onclick: async () => { for (const q of qs.filter((x) => !x.verified)) await j(`/api/lab/questions/${q.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verified: 1 }) }); load(); } }, "Approve all")),
    list);
  let qs = [];
  async function load() {
    qs = await j(`/api/lab/questions?notebook_id=${nb}`);
    list.replaceChildren(h("p", { class: "muted" }, `Approved ${qs.filter((q) => q.verified).length} of ${qs.length}`),
      qs.length ? qs.map((q) => h("div", { class: "qrow" },
        h("input", { type: "checkbox", checked: q.verified ? "checked" : null, "aria-label": "Approve", onchange: (e) => j(`/api/lab/questions/${q.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verified: e.target.checked ? 1 : 0 }) }) }),
        h("span", { class: "badge" }, q.language),
        h("div", {}, h("div", { onclick: async () => { const t = prompt("Edit question", q.question); if (t) { await j(`/api/lab/questions/${q.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: t }) }); load(); } }, style: "cursor:text" }, q.question),
          h("div", { class: "exp" }, q.expected_answer === REFUSE ? "Out of scope: the app should refuse" : q.expected_answer)),
        h("button", { class: "ghost", "aria-label": "Delete question", onclick: async () => { await j(`/api/lab/questions/${q.id}`, { method: "DELETE" }); load(); } }, "✕")))
        : h("p", { class: "muted" }, "No questions yet. Add a source, then click Generate."));
  }
  load();
}

// ---------- 2. run ----------
async function runView(box) {
  const sel = (opts, val) => { const s = h("select", {}, opts.map((o) => h("option", { value: o }, o))); s.value = val; return s; };
  const f = { name: h("input", { placeholder: "Name (optional)" }), size: sel(["200", "500", "1000"], "500"), mode: sel(["vector", "keyword", "hybrid"], "hybrid"),
    lang: sel(["both", "en", "hi"], "both"), k: h("input", { type: "number", value: "5", min: "1", max: "10", style: "width:70px" }),
    rerank: h("input", { type: "checkbox" }), mmr: h("input", { type: "checkbox" }), rewrite: h("input", { type: "checkbox" }), unv: h("input", { type: "checkbox" }) };
  const lab = (t, el) => h("label", { class: "row small" }, t, el);
  const bar = h("i"), msg = h("p", { class: "muted small" }), old = h("div");
  async function start(resumeId) {
    msg.textContent = "Starting…";
    const body = resumeId ? { notebook_id: nb, experiment_id: resumeId } : { notebook_id: nb, name: f.name.value, config: {
      chunk_size: +f.size.value, search_mode: f.mode.value, lang: f.lang.value, top_k: +f.k.value, rerank: f.rerank.checked,
      mmr: f.mmr.checked, rewrite: f.rewrite.checked, include_unverified: f.unv.checked } };
    try {
      await streamPost("/api/lab/run", body, (ev, d) => {
        if (ev === "progress") { msg.textContent = `${d.stage} ${d.total ? d.done + "/" + d.total : ""}`; bar.style.width = d.total ? (100 * d.done / d.total) + "%" : "5%"; }
        if (ev === "warning") toastFn(d);
        if (ev === "error") msg.textContent = d;
        if (ev === "done") msg.textContent = "Finished. Open the Results tab.";
      });
    } catch (e) { msg.textContent = e.message; }
    list();
  }
  box.append(h("div", { class: "card", style: "display:grid;gap:8px" },
    f.name, lab("Chunk size", f.size), lab("Search mode", f.mode), lab("Language", f.lang), lab("Top-k", f.k),
    lab("LLM rerank", f.rerank), lab("MMR", f.mmr), lab("Query rewrite", f.rewrite), lab("Include unverified questions", f.unv),
    h("button", { class: "pill", onclick: () => start() }, "Run experiment"), h("div", { class: "prog" }, bar), msg,
    h("p", { class: "muted small" }, "Other chunk sizes build a temporary index that is deleted after the run. Free-tier limits can make long runs slow.")), old);
  async function list() {
    const ex = await j(`/api/lab/experiments?notebook_id=${nb}`);
    old.replaceChildren(h("h3", {}, "Past runs"), ex.map((e) => h("div", { class: "row small" }, h("span", {}, `${e.name} · ${e.status}`),
      e.status === "paused" ? h("button", { class: "ghost", onclick: () => start(e.id) }, "Resume") : "")));
  }
  list();
}

// ---------- shared: pick an experiment ----------
async function picker(label, onPick, keep) {
  const ex = (await j(`/api/lab/experiments?notebook_id=${nb}`)).filter((e) => e.status === "complete" || e.status === "paused");
  const s = h("select", { "aria-label": label }, ex.map((e) => h("option", { value: e.id }, `${e.name} (${e.status})`)));
  s.onchange = () => onPick(s.value);
  if (ex.length) { if (keep) s.value = keep; onPick(s.value); }
  return { el: s, empty: !ex.length };
}
const bar = (v) => h("div", { class: "bar" }, h("i", { style: `width:${Math.round((v || 0) * 100)}%` }));
const noteBox = (t) => h("div", { class: "note" }, t);

function metricTable(sum) {
  const o = sum.overall;
  return h("table", { class: "m" }, h("tr", {}, ["Metric", "Value", "", "n"].map((x) => h("th", {}, x))),
    METRICS.map(([k, label, ratio]) => h("tr", {}, h("td", {}, label), h("td", {}, fmt(o[k])), h("td", {}, ratio ? bar(o[k]) : ""),
      h("td", { class: "muted small" }, k === "correct_refusal_rate" ? o.n_unanswerable : o.n_answerable))));
}

// ---------- 3. results ----------
async function resultsView(box) {
  const out = h("div");
  const p = await picker("Experiment", async (id) => {
    const [sum, rows] = await Promise.all([j(`/api/lab/summary/${id}`), j(`/api/lab/results/${id}`)]);
    out.replaceChildren(metricTable(sum), noteBox(sum.confidence_note + " The judge is an LLM, the same model may write and grade questions, and samples are small."),
      h("div", { class: "row" }, h("a", { href: `/api/lab/export/${id}?fmt=csv`, download: "results.csv" }, "Export CSV"), h("a", { href: `/api/lab/export/${id}?fmt=md`, download: "report.md" }, "Export Markdown")),
      h("h3", {}, "Each question"), rows.map((r) => h("details", { class: "card" }, h("summary", {}, `${r.correct ? "✅" : "❌"} ${r.question}`),
        h("p", {}, h("b", {}, "Expected: "), r.expected_answer === REFUSE ? "(should refuse)" : r.expected_answer),
        h("p", {}, h("b", {}, "Answer: "), r.answer), h("p", { class: "muted small" }, `Judge: ${r.judge_notes} · hit=${fmt(r.hit)} · ${r.latency_ms} ms`),
        JSON.parse(r.retrieved_json || "[]").map((c) => h("div", { class: "small muted" }, `#${c.rank}${c.page ? " p." + c.page : ""}: ${c.text}`)))));
  });
  box.append(p.empty ? h("p", { class: "muted" }, "No finished experiments yet. Use Run experiment.") : p.el, out);
}

// ---------- 4. Hindi vs English ----------
async function langView(box) {
  const out = h("div");
  const p = await picker("Experiment", async (id) => {
    const sum = await j(`/api/lab/summary/${id}`), en = sum.by_language.en, hi = sum.by_language.hi;
    out.replaceChildren(h("table", { class: "m" }, h("tr", {}, ["Metric", "English", "Hindi", "Gap (HI − EN)"].map((x) => h("th", {}, x))),
      METRICS.map(([k, label]) => h("tr", {}, h("td", {}, label), h("td", {}, fmt(en[k])), h("td", {}, fmt(hi[k])), h("td", {}, gap(hi[k], en[k], k))))),
      noteBox(`English n=${en.n}, Hindi n=${hi.n}. ${sum.confidence_note} Hindi questions here are faithful translations of the English ones, so the pairs are comparable.`));
  });
  box.append(p.empty ? h("p", { class: "muted" }, "No finished experiments yet.") : p.el, out);
}
function gap(a, b, key) {
  if (a == null || b == null) return "[X]";
  const d = Math.round((a - b) * 1000) / 1000, bad = key.startsWith("latency") || key === "est_cost_usd" || key === "tokens" ? d > 0 : d < 0;
  return h("span", { class: d === 0 ? "" : bad ? "dn" : "up" }, (d > 0 ? "+" : "") + d);
}

// ---------- 5. A/B ----------
async function abView(box) {
  const out = h("div"); let ida = null, idb = null;
  async function draw() {
    if (!ida || !idb) return;
    const [a, b] = await Promise.all([j(`/api/lab/summary/${ida}`), j(`/api/lab/summary/${idb}`)]);
    out.replaceChildren(h("table", { class: "m" }, h("tr", {}, ["Metric", "A", "B", "Delta (B − A)"].map((x) => h("th", {}, x))),
      METRICS.map(([k, label]) => h("tr", {}, h("td", {}, label), h("td", {}, fmt(a.overall[k])), h("td", {}, fmt(b.overall[k])), h("td", {}, gap(b.overall[k], a.overall[k], k))))),
      noteBox(`A: n=${a.overall.n}, B: n=${b.overall.n}. Small differences can be noise.`));
  }
  const pa = await picker("Experiment A", (id) => { ida = id; draw(); });
  const pb = await picker("Experiment B", (id) => { idb = id; draw(); });
  box.append(pa.empty ? h("p", { class: "muted" }, "Run at least two experiments to compare.") : h("div", { class: "row" }, "A", pa.el, "B", pb.el), out);
}

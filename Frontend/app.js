// RAG Lab Pro frontend. Plain JavaScript, no build step. Read top to bottom.
import { api, streamChat } from "./api.js";
import { LABELS } from "./i18n.js";
import { renderLab } from "./lab.js";

const $ = (s) => document.querySelector(s);
const state = { nb: null, lang: localStorage.getItem("lang") || "en", busy: false, last: "" };

// ---------- small helpers ----------
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3500);
}
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
function setLang(l) {
  state.lang = l; localStorage.setItem("lang", l);
  const L = LABELS[l];
  document.documentElement.lang = l;
  document.querySelectorAll("[data-i18n]").forEach((n) => { if (L[n.dataset.i18n]) n.textContent = L[n.dataset.i18n]; });
  $("#heroTitle").textContent = L.heroTitle; $("#heroSub").textContent = L.heroSub; $("#q").placeholder = L.ask;
  renderEmpty();
}
function showApp() { $("#landing").hidden = true; $("#app").hidden = false; $("#tabs").hidden = false; $("#openBtn").hidden = true; setTab("chat"); }
function setTab(t) {
  document.body.className = "t-" + t;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  if (t === "settings") renderSettings();
  if (t === "lab") renderLab(state, toast);
}

// ---------- notebooks ----------
async function loadNotebooks(selectId) {
  let list = await api.notebooks();
  if (!list.length) { await api.newNotebook("My notebook"); list = await api.notebooks(); }
  const sel = $("#nbSelect"); sel.replaceChildren();
  list.forEach((n) => { const o = el("option", "", n.name); o.value = n.id; sel.append(o); });
  state.nb = selectId || state.nb || list[0].id; sel.value = state.nb;
  await Promise.all([loadSources(), loadHistory()]);
}
$("#nbSelect").onchange = (e) => { state.nb = e.target.value; loadSources(); loadHistory(); };
$("#nbNew").onclick = async () => {
  const name = prompt("Notebook name"); if (!name) return;
  const nb = await api.newNotebook(name); loadNotebooks(nb.id);
};

// ---------- sources ----------
let pollTimer = null;
async function loadSources() {
  const list = await api.sources(state.nb), ul = $("#srcList"); ul.replaceChildren();
  if (!list.length) ul.append(el("li", "muted", LABELS[state.lang].noSources));
  list.forEach((s) => {
    const li = el("li"); li.append(el("span", "name", s.title || s.origin)); li.title = s.error || s.title;
    li.append(el("span", "badge " + s.status, s.status));
    if (s.warning) li.append(el("span", "badge warn", "!")), (li.title = s.warning);
    const del = el("button", "ghost", "✕"); del.setAttribute("aria-label", "Delete source");
    del.onclick = async () => { await api.delSource(s.id); loadSources(); };
    li.append(del); ul.append(li);
    if (s.status === "failed" && s.error) li.append(el("span", "small err", s.error));
  });
  clearTimeout(pollTimer);
  if (list.some((s) => ["queued", "processing"].includes(s.status))) pollTimer = setTimeout(loadSources, 2000);
}
async function uploadFiles(files) {
  for (const f of files) {
    try { await api.upload(state.nb, f); toast("Uploaded " + f.name); } catch (e) { toast(e.message); }
  }
  loadSources();
}
$("#file").onchange = (e) => uploadFiles(e.target.files);
const drop = $("#drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadFiles(e.dataTransfer.files); });
drop.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#file").click(); });

// add-source tabs: Upload | Link | YouTube | Official
let addMode = "upload";
document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
  addMode = b.dataset.add;
  document.querySelectorAll("[data-add]").forEach((x) => x.classList.toggle("on", x === b));
  $("#drop").hidden = addMode !== "upload"; $("#linkForm").hidden = !["url", "youtube"].includes(addMode); $("#officialBox").hidden = addMode !== "official";
  $("#linkUrl").placeholder = addMode === "youtube" ? "https://www.youtube.com/watch?v=…" : "https://";
}));
$("#linkForm").onsubmit = async (e) => {
  e.preventDefault(); const url = $("#linkUrl").value.trim(); if (!url) return;
  try { await api.addLink(state.nb, addMode, url); $("#linkUrl").value = ""; toast("Added. Processing…"); loadSources(); } catch (x) { toast(x.message); }
};
$("#offForm").onsubmit = async (e) => {
  e.preventDefault(); const ul = $("#offRes"); ul.replaceChildren(el("li", "muted", "Searching…"));
  try {
    const res = await api.officialSearch($("#offQ").value);
    ul.replaceChildren(...(res.length ? res.map((r) => {
      const li = el("li"), a = el("div", "official-item"); a.append(el("div", "", r.title), el("span", "muted", r.domain + (r.date ? " · " + r.date.slice(0, 10) : "")), el("span", "badge ready", "Official domain"));
      const add = el("button", "ghost", "Import"); add.onclick = async () => { try { await api.addLink(state.nb, "official", r.url); toast("Added"); loadSources(); } catch (x) { toast(x.message); } };
      li.append(a, add); return li; }) : [el("li", "muted", "No results on your trusted domains.")]));
  } catch (x) { ul.replaceChildren(el("li", "err", x.message)); }
};

// ---------- chat ----------
function renderEmpty() {
  const log = $("#log"); if (log.children.length && !log.querySelector(".empty")) return;
  log.replaceChildren(el("p", "empty", LABELS[state.lang].empty));
}
async function loadHistory() {
  const log = $("#log"); log.replaceChildren();
  const msgs = await api.messages(state.nb);
  if (!msgs.length) return renderEmpty();
  msgs.forEach((m) => addMsg(m.role === "user" ? "user" : "bot", m.text, m.citations_json ? JSON.parse(m.citations_json) : [], m.id));
}
// Put text in a node and turn [1] [2] into clickable citation chips (safe: no innerHTML).
function fillWithChips(node, text, cites) {
  node.replaceChildren();
  text.split(/(\[\d+\])/).forEach((part) => {
    const m = /^\[(\d+)\]$/.exec(part), c = m && cites.find((x) => x.n === +m[1]);
    if (c) { const b = el("button", "chip", m[1]); b.title = c.title; b.onclick = () => openViewer(c); node.append(b); }
    else node.append(document.createTextNode(part));
  });
}
function addMsg(kind, text, cites = [], id = null) {
  const log = $("#log"); log.querySelector(".empty")?.remove();
  const box = el("div", "msg " + kind), body = el("div");
  fillWithChips(body, text, cites); box.append(body);
  if (kind === "bot") box.append(actions(box, body, id));
  log.append(box); log.scrollTop = log.scrollHeight; return { box, body };
}
function actions(box, body, id) {
  const a = el("div", "actions");
  const btn = (label, fn, aria) => { const b = el("button", "ghost", label); b.setAttribute("aria-label", aria); b.onclick = fn; a.append(b); return b; };
  btn("📋", () => navigator.clipboard.writeText(body.textContent).then(() => toast("Copied")), "Copy answer");
  btn("🔊", () => speak(body.textContent), "Read aloud");
  btn("🔁", () => send(state.last), "Regenerate");
  btn("👍", () => id && api.feedback(id, "up").then(() => toast("Thanks!")), "Good answer").dataset.fb = "up";
  btn("👎", () => id && api.feedback(id, "down").then(() => toast("Thanks, noted.")), "Bad answer");
  return a;
}
async function send(question) {
  question = (question || "").trim(); if (!question || state.busy) return;
  state.busy = true; state.last = question; $("#send").disabled = true; $("#q").value = "";
  addMsg("user", question);
  const { box, body } = addMsg("bot", ""); body.append(el("div", "skeleton"));
  let text = "", retrieval = null;
  try {
    await streamChat({ notebook_id: state.nb, question }, (ev, d) => {
      if (ev === "retrieval") retrieval = d;
      if (ev === "token") { text += d; body.textContent = text; $("#log").scrollTop = 1e9; }
      if (ev === "error") { body.replaceChildren(el("span", "err", d)); }
      if (ev === "done") finishMsg(box, body, text, d, retrieval);
    });
  } catch (e) { body.replaceChildren(el("span", "err", e.message)); }
  state.busy = false; $("#send").disabled = false;
}
function finishMsg(box, body, text, d, retrieval) {
  fillWithChips(body, text, d.citations);
  const acts = box.querySelector(".actions");
  const info = el("div", "small muted", `${d.latency_ms} ms · ${d.tokens_in}+${d.tokens_out} tokens · ~$${d.est_cost_usd} (estimate)`);
  box.insertBefore(info, acts);
  if (d.low_confidence) box.insertBefore(el("div", "warnbadge", "⚠ Low confidence: the match was weak. Check the sources."), acts);
  if (d.invalid_citations.length) box.insertBefore(el("div", "warnbadge", "⚠ Some citation numbers were invalid: " + d.invalid_citations.join(", ")), acts);
  if (retrieval) {
    const det = el("details", "rd"); det.append(el("summary", "", LABELS[state.lang].details));
    retrieval.passages.forEach((p) => det.append(el("div", "", `#${p.rank} · score ${p.score} · ${p.source_title}${p.page ? " · p." + p.page : ""}`)));
    box.insertBefore(det, acts);
  }
  acts.querySelector("[data-fb]").onclick = () => api.feedback(d.message_id, "up").then(() => toast("Thanks!"));
  acts.lastChild.onclick = () => api.feedback(d.message_id, "down").then(() => toast("Thanks, noted."));
}
$("#send").onclick = () => send($("#q").value);
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#q").value); } });
document.addEventListener("keydown", (e) => { if (e.ctrlKey && e.key.toLowerCase() === "k") { e.preventDefault(); $("#q").focus(); } });

// ---------- source viewer ----------
async function openViewer(c) {
  const v = $("#viewer"); v.replaceChildren(el("div", "skeleton"));
  try {
    const d = await api.context(c.chunk_id);
    v.replaceChildren(el("h4", "", d.title));
    const meta = [];
    if (d.page) meta.push("Page " + d.page);
    if (d.start_seconds != null) { const a = el("a", "", "▶ " + Math.floor(d.start_seconds) + "s"); a.href = `${d.url}${d.url.includes("?") ? "&" : "?"}t=${Math.floor(d.start_seconds)}s`; a.target = "_blank"; a.rel = "noopener"; v.append(a); }
    if (meta.length) v.append(el("p", "small muted", meta.join(" · ")));
    v.append(el("div", "ctx", d.before), Object.assign(el("div", "cur"), {}), el("div", "ctx", d.after));
    const cur = v.querySelector(".cur"); cur.append(el("mark", "", d.text));
  } catch (e) { v.replaceChildren(el("p", "err", e.message)); }
}

// ---------- study material, export, voice ----------
document.querySelectorAll("[data-study]").forEach((b) => (b.onclick = async () => {
  const { box, body } = addMsg("bot", ""); body.append(el("div", "skeleton"));
  try { body.textContent = (await api.study(state.nb, b.dataset.study)).text; } catch (e) { body.replaceChildren(el("span", "err", e.message)); }
  box.querySelector(".actions")?.remove();
}));
$("#export").onclick = () => {
  const lines = [...document.querySelectorAll(".msg")].map((m) => (m.classList.contains("user") ? "**You:** " : "**Answer:** ") + m.firstChild.textContent);
  const a = el("a"); a.href = URL.createObjectURL(new Blob([lines.join("\n\n")], { type: "text/markdown" })); a.download = "chat.md"; a.click();
};
function speak(text) {
  if (!("speechSynthesis" in window)) return toast("Read aloud is not supported in this browser.");
  const u = new SpeechSynthesisUtterance(text); u.lang = /[\u0900-\u097F]/.test(text) ? "hi-IN" : "en-IN"; speechSynthesis.speak(u);
}
$("#mic").onclick = () => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return toast("Voice input is not supported in this browser. Try Chrome.");
  const r = new SR(); r.lang = state.lang === "hi" ? "hi-IN" : "en-IN";
  r.onresult = (e) => { $("#q").value = e.results[0][0].transcript; };
  r.onerror = () => toast("Could not hear you. Check the microphone permission.");
  r.start(); toast("Listening…");
};

// ---------- settings ----------
async function renderSettings() {
  const s = await api.getSettings(), f = $("#form"); f.replaceChildren(el("h2", "", "Settings"));
  const field = (key, label, kind, opts) => {
    const wrap = el("label", "", label); let input;
    if (kind === "select") { input = el("select"); opts.forEach((o) => { const op = el("option", "", o); op.value = o; input.append(op); }); input.value = s[key]; }
    else if (kind === "check") { input = el("input"); input.type = "checkbox"; input.checked = !!s[key]; }
    else { input = el("input"); input.type = "number"; input.value = s[key]; }
    input.name = key; input.dataset.kind = kind; wrap.append(input); f.append(wrap);
  };
  field("chunk_size", "Chunk size (new sources only)", "select", ["200", "500", "1000"]);
  field("search_mode", "Search mode", "select", ["vector", "keyword", "hybrid"]);
  field("top_k", "Passages to use (top-k)", "number");
  field("rerank", "LLM rerank", "check"); field("mmr", "MMR (more variety)", "check"); field("rewrite", "Rewrite follow-up questions", "check");
  field("embedding_backend", "Embedding backend", "select", ["local", "gemini"]);
  const td = el("label", "", "Trusted domains (comma separated)"), ti = el("input"); ti.name = "trusted_domains"; ti.dataset.kind = "list"; ti.value = (s.trusted_domains || []).join(", "); td.append(ti); f.append(td);
  const b = el("button", "pill", LABELS[state.lang].save); b.type = "button";
  b.onclick = async () => {
    const out = {};
    f.querySelectorAll("[name]").forEach((i) => { out[i.name] = i.dataset.kind === "list" ? i.value.split(",").map((x) => x.trim()).filter(Boolean) : i.dataset.kind === "check" ? i.checked : i.dataset.kind === "number" || i.name === "chunk_size" ? +i.value : i.value; });
    await api.putSettings(out); toast("Settings saved");
  };
  f.append(b);
}
$("#wipe").onclick = async () => { if (confirm("Delete ALL notebooks and data?")) { await api.deleteAll(); state.nb = null; await loadNotebooks(); toast("All data deleted"); } };

// ---------- start ----------
document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
$("#lang").onclick = () => setLang(state.lang === "en" ? "hi" : "en");
const open = async () => { showApp(); try { await loadNotebooks(); } catch (e) { toast(e.message); } };
$("#openBtn").onclick = open; $("#heroBtn").onclick = open;
setLang(state.lang);

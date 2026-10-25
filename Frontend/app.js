// RAG Lab Pro v2 (server version): chatbot UI. Chats live in this browser; notebooks and sources live on the server.
import { api, j, streamPost } from "./api.js";
import { renderLab } from "./lab.js";

const $ = (s) => document.querySelector(s) || (console.warn("Missing element " + s), document.createElement("div")), esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const uid = () => Math.random().toString(36).slice(2, 9);
const L = { en: { newc: "New chat", ph: "Message RAG Lab… (Hindi, English or Hinglish)", h1: "Ask your documents.", h2: "Verify every answer.", priv: "Your text is sent to Google Gemini to answer. Do not upload confidential documents to a public demo." },
  hi: { newc: "नई चैट", ph: "RAG Lab को लिखें… (हिंदी, English या Hinglish)", h1: "अपने दस्तावेज़ों से पूछें।", h2: "हर जवाब जाँचें।", priv: "आपका टेक्स्ट जवाब के लिए Google Gemini को भेजा जाता है। पब्लिक डेमो में गोपनीय दस्तावेज़ न डालें।" } };
const toast = (m) => { const t = $("#toast"); t.textContent = m; t.classList.add("s"); setTimeout(() => t.classList.remove("s"), 3200); };
window.onerror = (m) => toast("Error: " + m);
window.onunhandledrejection = (e) => toast("Error: " + (e.reason?.message || e.reason));
window.askPassword = () => new Promise((res) => { $("#loginM").hidden = false; $("#pw").focus();
  $("#loginF").onsubmit = (e) => { e.preventDefault(); const v = $("#pw").value; $("#pw").value = ""; $("#loginM").hidden = true; res(v); }; });

/* ---------- state ---------- */
let C = { chats: {}, cur: null, chat: null, lang: "en", theme: "" }, NBS = [], SRC = [], cfg = { answer_mode: "auto" }, busy = false, run = 0, ctrl = null, pollT = null;
try { const x = JSON.parse(localStorage.getItem("ragpro-srv2") || "null"); if (x) C = { ...C, ...x }; } catch (e) { /* first visit */ }
const save = () => { try { localStorage.setItem("ragpro-srv2", JSON.stringify(C)); } catch (e) { toast("Could not save chats in this browser."); } };
const chatsOf = () => (C.chats[C.cur] ||= []), chat = () => chatsOf().find((c) => c.id == C.chat);
// two-click confirm (browser confirm() boxes are blocked in shared pages)
function arm(b,label,fn){let t;b.onclick=e=>{e.stopPropagation();if(b.dataset.arm){clearTimeout(t);fn();return}b.dataset.arm=1;const o=b.textContent;b.textContent=label;b.classList.add("danger");t=setTimeout(()=>{delete b.dataset.arm;b.textContent=o;b.classList.remove("danger")},3000)}}
function edit(el,val,cb){const i=document.createElement("input");i.value=val;el.replaceWith(i);i.focus();i.select();let d=0;const end=ok=>{if(d)return;d=1;cb(ok&&i.value.trim()?i.value.trim():null)};i.onblur=()=>end(true);i.onkeydown=e=>{if(e.key=="Enter")end(true);if(e.key=="Escape")end(false)}}
/* ---------- notebooks ---------- */
async function loadNbs(select) {
  NBS = await api.notebooks();
  if (!NBS.length) { await api.newNotebook("My notebook"); NBS = await api.notebooks(); }
  C.cur = select || (NBS.some((n) => n.id == C.cur) ? C.cur : NBS[0].id);
  if (!chat()) C.chat = null;
  save(); drawAll(); loadSources();
}
function drawNbs() {
  const r = $("#nbRow"); r.innerHTML = ""; const s = document.createElement("select"); s.setAttribute("aria-label", "Notebook");
  NBS.forEach((n) => { const o = document.createElement("option"); o.value = n.id; o.textContent = n.name; s.append(o); }); s.value = C.cur;
  s.onchange = () => { C.cur = s.value; C.chat = null; save(); drawAll(); loadSources(); }; r.append(s);
  const b = (t, l) => { const x = document.createElement("button"); x.className = "gh"; x.textContent = t; x.title = l; x.setAttribute("aria-label", l); r.append(x); return x; };
  const ren = () => edit($("#nbRow select"), NBS.find((n) => n.id == C.cur).name, async (v) => { if (v) { try { await api.renameNotebook(C.cur, v); } catch (e) { toast(e.message); } } loadNbs(C.cur); });
  b("＋", "New notebook").onclick = async () => { const n = await api.newNotebook("Notebook " + (NBS.length + 1)); await loadNbs(n.id); ren(); };
  b("✎", "Rename notebook").onclick = ren;
  arm(b("🗑", "Remove notebook"), "Sure?", async () => { try { await api.delNotebook(C.cur); delete C.chats[C.cur]; C.chat = null; C.cur = null; toast("Notebook removed"); loadNbs(); } catch (e) { toast(e.message); } });
}
/* ---------- sources ---------- */
async function loadSources() {
  try { SRC = await j(`/api/notebooks/${C.cur}/sources`); } catch (e) { return; }
  drawSrc(); draw(); clearTimeout(pollT);
  if (SRC.some((s) => ["queued", "processing"].includes(s.status))) pollT = setTimeout(loadSources, 2000);
}
function drawSrc() {
  const ul = $("#sl"); ul.innerHTML = ""; $("#sc").textContent = SRC.length; $("#rall").hidden = !SRC.length;
  if (!SRC.length) ul.innerHTML = '<li class="mu sm">No sources in this notebook.</li>';
  SRC.forEach((s) => {
    const li = document.createElement("li"), c = document.createElement("input"); c.type = "checkbox"; c.checked = !!s.enabled; c.title = "Use this source in answers";
    c.onchange = () => api.setEnabled(s.id, c.checked).catch((e) => toast(e.message));
    const n = document.createElement("span"); n.className = "n"; n.textContent = ({ file: "📄", text: "📝", link: "🔗", url: "🌐", youtube: "▶️", official: "🏛" }[s.type] || "📄") + " " + (s.title || s.origin); n.title = s.error || s.warning || s.title || "";
    const b = document.createElement("span"); b.className = "badge " + (s.warning && s.status == "ready" ? "warn" : s.status); b.textContent = s.warning && s.status == "ready" ? "warning" : s.status; b.title = s.error || s.warning || "";
    const x = document.createElement("button"); x.className = "gh"; x.textContent = "✕"; x.title = "Remove source"; x.setAttribute("aria-label", "Remove source");
    li.append(c, n, b, x); ul.append(li);
    if (s.status == "failed" && s.error) { const e = document.createElement("div"); e.className = "er sm"; e.textContent = s.error; li.style.flexWrap = "wrap"; li.append(e); }
    arm(x, "Sure?", async () => { try { await api.delSource(s.id); toast("Source removed"); loadSources(); } catch (e) { toast(e.message); } });
  });
}
arm($("#rall"), "Sure?", async () => { for (const s of SRC) { try { await api.delSource(s.id); } catch (e) { /* keep going */ } } toast("All sources removed"); loadSources(); });
async function files(list) { for (const f of list) { try { await api.upload(C.cur, f); toast("Uploaded " + f.name); } catch (e) { toast(e.message); } } loadSources(); }
$("#sf").onchange = (e) => { files(e.target.files); e.target.value = ""; $("#dr").hidden = false; };
$("#sf2").onchange = (e) => { files(e.target.files); e.target.value = ""; };
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { files(e.dataTransfer.files); $("#dr").hidden = false; } });
const guard = async (fn) => { try { await fn(); loadSources(); } catch (e) { toast(e.message); } };
$("#sa").onclick = () => guard(async () => { await api.addText(C.cur, $("#st").value, $("#sx").value); $("#sx").value = $("#st").value = ""; toast("Added. Processing…"); });
$("#wa").onclick = () => guard(async () => { await api.addLink(C.cur, "url", $("#wu").value.trim()); $("#wu").value = ""; toast("Added. Processing…"); });
$("#ya").onclick = () => guard(async () => { await api.addLink(C.cur, "youtube", $("#yu").value.trim()); $("#yu").value = ""; toast("Added. Processing…"); });
document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => { document.querySelectorAll("[data-add]").forEach((x) => x.classList.toggle("on", x == b)); document.querySelectorAll("[data-ap]").forEach((p) => (p.hidden = p.dataset.ap != b.dataset.add)); }));
/* ---------- chats ---------- */
function drawChats() {
  const f = $("#cf").value.toLowerCase(), box = $("#chats"); box.innerHTML = "";
  const A = chatsOf().filter((c) => c.title.toLowerCase().includes(f)).sort((a, b) => (b.pin | 0) - (a.pin | 0) || b.t - a.t);
  if (!A.length) box.innerHTML = '<p class="mu sm" style="padding:6px 8px">No chats yet.</p>';
  A.forEach((c) => {
    const r = document.createElement("div"); r.className = "ci" + (c.id == C.chat ? " on" : ""); const t = document.createElement("span"); t.className = "n"; t.textContent = (c.pin ? "📌 " : "") + c.title; t.title = "Double-click to rename";
    r.onclick = () => { C.chat = c.id; save(); drawAll(); $("#sb").classList.remove("open"); };
    t.ondblclick = (e) => { e.stopPropagation(); edit(t, c.title, (v) => { if (v) c.title = v; save(); drawChats(); draw(); }); };
    const p = document.createElement("button"); p.className = "gh"; p.textContent = "📌"; p.title = "Pin"; p.onclick = (e) => { e.stopPropagation(); c.pin = c.pin ? 0 : 1; save(); drawChats(); };
    const x = document.createElement("button"); x.className = "gh"; x.textContent = "✕"; x.title = "Delete chat";
    arm(x, "Sure?", () => { C.chats[C.cur] = chatsOf().filter((y) => y.id != c.id); if (C.chat == c.id) C.chat = null; save(); drawAll(); });
    r.append(t, p, x); box.append(r);
  });
}
$("#cf").oninput = drawChats;
$("#newChat").onclick = () => { C.chat = null; save(); drawAll(); $("#q").focus(); $("#sb").classList.remove("open"); };
const SUG=[["💬","What can you help me with?",""],["📝","Summarize my sources","summary"],["💡","Explain the key ideas in simple words",""],["❓","Make an FAQ","faq"]];
function emptyEl(){const w=document.createElement("div");w.className="empty";w.innerHTML=`<h1>${L[C.lang].h1}<br><span class="ac">${L[C.lang].h2}</span></h1><p class="mu">${SRC.length?"Chat with me normally, or ask about your sources. I answer from them and show where each answer came from.":"Chat with me normally, or add a source (file, text or link) and ask about it."}</p>`;
 const g=document.createElement("div");g.className="sug";SUG.forEach(([i,t,k])=>{const b=document.createElement("button");b.textContent=i+" "+t;b.onclick=()=>k?tool(k):ask(t);g.append(b)});w.append(g);
 if(!SRC.length){const b=document.createElement("button");b.className="pill";b.style.marginTop="18px";b.textContent="Add a source";b.onclick=()=>{$("#dr").hidden=false};w.append(b)}return w}
// inline text: citation chips + bold / italic / code (text is escaped first, so it is safe)
function inl(p,s,m){s.split(/(\[\d+\])/).forEach(x=>{const n=/^\[(\d+)\]$/.exec(x),c=n&&m.p?.[n[1]-1];
 if(c){const b=document.createElement("button");b.className="chip";b.textContent=n[1];b.title=c.title;b.onclick=()=>view(c);p.append(b)}
 else if(x){const sp=document.createElement("span");sp.innerHTML=esc(x).replace(/\*\*(.+?)\*\*/g,"<b>$1</b>").replace(/`(.+?)`/g,"<code>$1</code>").replace(/(^|[\s(])\*(?!\s)(.+?)\*(?=[\s).,!?]|$)/g,"$1<i>$2</i>");p.append(sp)}})}
// small Markdown renderer: headings, bullet and numbered lists, code blocks, paragraphs
function fillT(b,m){b.textContent="";if(m.r=="u"){b.textContent=m.t;return}let list=null,code=null;
 for(const ln of m.t.split("\n")){let g;
  if(/^```/.test(ln)){if(code)code=null;else{code=document.createElement("pre");b.append(code);list=null}continue}
  if(code){code.textContent+=(code.textContent?"\n":"")+ln;continue}
  if(g=/^(#{1,3})\s+(.*)/.exec(ln)){const h=document.createElement("h"+(g[1].length+2));inl(h,g[2],m);b.append(h);list=null}
  else if(g=/^\s*([-*•]|\d+[.)])\s+(.*)/.exec(ln)){const tag=/\d/.test(g[1])?"ol":"ul";if(!list||list.tagName.toLowerCase()!=tag){list=document.createElement(tag);b.append(list)}const li=document.createElement("li");inl(li,g[2],m);list.append(li)}
  else if(!ln.trim())list=null;
  else{list=null;const p=document.createElement("p");inl(p,ln,m);b.append(p)}}}

function msgEl(m,i,c){const w=document.createElement("div");w.className="mrow "+m.r;const last=i==c.msgs.length-1;
 if(m.r=="a"){const a=document.createElement("div");a.className="av";a.textContent="R";w.append(a)}
 const box=document.createElement("div");box.className="mb";w.append(box);
 if(m.r=="a"&&!m.t)box.innerHTML='<span class="mu">'+esc(m.st||"Thinking…")+'</span> <span class="dots"><i></i><i></i><i></i></span>';else fillT(box,m);
 if(m.r=="a"&&m.t){
  if(m.tag){const x=document.createElement("div");x.className="tagl";x.textContent=m.tag;box.prepend(x)}
  const nums=[...new Set([...m.t.matchAll(/\[(\d+)\]/g)].map(x=>+x[1]))].filter(n=>m.p?.[n-1]);
  if(nums.length){const u=document.createElement("div");u.className="used";nums.forEach(n=>{const x=document.createElement("button");x.className="chip";x.textContent=n+" · "+m.p[n-1].title.slice(0,24);x.onclick=()=>view(m.p[n-1]);u.append(x)});box.append(u)}
  if(m.low&&m.route=="docs"){const x=document.createElement("div");x.className="wa";x.textContent="⚠ Weak match: no keyword overlap. Check the sources.";box.append(x)}
  if(m.route=="docs"&&m.p?.length){const d=document.createElement("details");d.innerHTML="<summary>Retrieval details</summary><div>Searched for: "+esc((m.qs||[]).join("  |  "))+"</div>"+m.p.map(q=>`<div>#${q.rank} · score ${q.score} · ${esc(q.title)}</div>`).join("");box.append(d)}
  if(!(busy&&last)){const a=document.createElement("div");a.className="acts";const g=(t,l,f)=>{const x=document.createElement("button");x.className="gh";x.textContent=t;x.title=l;x.setAttribute("aria-label",l);x.onclick=f;a.append(x)};
   g("📋","Copy",()=>navigator.clipboard?.writeText(m.t).then(()=>toast("Copied")));g("🔊","Read aloud",()=>{const u=new SpeechSynthesisUtterance(m.t);u.lang=/[\u0900-\u097F]/.test(m.t)?"hi-IN":"en-IN";speechSynthesis.cancel();speechSynthesis.speak(u)});
   if(last)g("🔁","Regenerate",regen);g(m.fb=="up"?"👍✓":"👍","Good answer",()=>{m.fb="up";if(m.id)api.feedback(m.id,"up").catch(()=>{});save();draw()});g(m.fb=="dn"?"👎✓":"👎","Bad answer",()=>{m.fb="dn";if(m.id)api.feedback(m.id,"down").catch(()=>{});save();draw()});box.append(a)}
  if(last&&!busy&&m.fu?.length){const f=document.createElement("div");f.className="fus";m.fu.forEach(t=>{const x=document.createElement("button");x.className="fu";x.textContent="↳ "+t;x.onclick=()=>ask(t);f.append(x)});box.append(f)}}
 if(m.r=="u"&&i==c.msgs.length-2&&!busy){const a=document.createElement("div");a.className="acts";const x=document.createElement("button");x.className="gh";x.textContent="✎ Edit";x.onclick=()=>{c.msgs=c.msgs.slice(0,i);$("#q").value=m.t;save();draw();grow();$("#q").focus()};a.append(x);box.append(a)}
 return w}


function draw() {
  const c = chat(), col = $("#col"); col.innerHTML = ""; $("#ct").textContent = c ? c.title : "New chat"; $("#send").textContent = busy ? "■" : "↑";
  if (!c || !c.msgs.length) { col.append(emptyEl()); return; }
  c.msgs.forEach((m, i) => col.append(msgEl(m, i, c))); $("#thread").scrollTop = 1e9;
}
function drawAll() { drawNbs(); drawChats(); drawSrc(); draw(); }
async function view(c) {
  $("#dr").hidden = false; const v = $("#vw"); v.innerHTML = '<span class="mu sm">Loading…</span>';
  try {
    const d = await api.context(c.chunk_id), t = d.start_seconds != null && d.url ? `<p><a href="${esc(d.url + (d.url.includes("?") ? "&" : "?") + "t=" + Math.floor(d.start_seconds) + "s")}" target="_blank" rel="noopener">▶ Open video at ${Math.floor(d.start_seconds)}s</a></p>` : "";
    v.innerHTML = `<b>${esc(d.title)}</b>${d.page ? `<p class="mu sm">Page ${d.page}</p>` : ""}<p class="mu sm" style="white-space:pre-wrap">${esc(d.before)}</p><p style="white-space:pre-wrap"><mark>${esc(d.text)}</mark></p><p class="mu sm" style="white-space:pre-wrap">${esc(d.after)}</p>${t}`;
  } catch (e) { v.innerHTML = '<span class="er">' + esc(e.message) + "</span>"; }
}
/* ---------- asking: the server routes, searches and streams ---------- */
function ensureChat(title) { let c = chat(); if (!c) { c = { id: uid(), title: title.slice(0, 40), pin: 0, t: Date.now(), msgs: [] }; chatsOf().push(c); C.chat = c.id; } c.t = Date.now(); return c; }
async function ask(q) {
  q = q.trim(); if (busy || !q) return;
  const c = ensureChat(q), my = ++run; busy = true; c.msgs.push({ r: "u", t: q }); const m = { r: "a", t: "", p: [], st: "Thinking…" }; c.msgs.push(m); ctrl = new AbortController(); drawAll();
  const history = c.msgs.slice(0, -2).slice(-8).map((x) => ({ role: x.r == "u" ? "user" : "assistant", text: x.t }));
  try {
    await streamPost("/api/chat", { notebook_id: C.cur, question: q, history, mode: cfg.answer_mode }, (ev, d) => {
      if (my != run) return;
      if (ev == "status") { m.st = d; draw(); }
      if (ev == "retrieval") { m.p = d.passages.map((p) => ({ ...p, title: p.source_title })); m.qs = d.queries; m.route = "docs"; }
      if (ev == "token") { m.raw = (m.raw || "") + d; m.t = m.raw.replace(/\n?\s*FOLLOWUPS:[\s\S]*$/, ""); draw(); }
      if (ev == "error") { m.t = "⚠ " + d; }
      if (ev == "done") { Object.assign(m, { t: d.answer, fu: d.followups, tag: d.tag, route: d.route, qs: d.queries, low: d.low_confidence, id: d.message_id }); if (d.invalid_citations.length) m.t += "\n⚠ Invalid citation number(s): " + d.invalid_citations.join(", "); }
    }, ctrl.signal);
  } catch (e) { if (e.name != "AbortError" && my == run) m.t = "⚠ " + e.message; }
  if (my == run) busy = false; save(); drawAll();
}
const TP = { summary: "Summary", faq: "FAQ", guide: "Study guide" };
async function tool(k) {
  if (busy) return; if (!SRC.some((s) => s.status == "ready")) return toast("Add a source and wait until it is ready.");
  const c = ensureChat(TP[k]), my = ++run; busy = true; c.msgs.push({ r: "u", t: TP[k] }); const m = { r: "a", t: "", p: [], st: "Reading your sources…" }; c.msgs.push(m); drawAll();
  try { m.t = (await api.study(C.cur, k)).text; } catch (e) { m.t = "⚠ " + e.message; }
  if (my == run) busy = false; save(); drawAll();
}
function regen() { const c = chat(); if (!c || busy) return; const i = c.msgs.map((x) => x.r).lastIndexOf("u"); if (i < 0) return; const q = c.msgs[i].t; c.msgs = c.msgs.slice(0, i); ask(q); }
const grow = () => { const q = $("#q"); q.style.height = "auto"; q.style.height = Math.min(180, q.scrollHeight) + "px"; };
$("#q").oninput = grow; $("#q").onkeydown = (e) => { if (e.key == "Enter" && !e.shiftKey) { e.preventDefault(); $("#send").click(); } };
$("#send").onclick = () => {
  if (busy) { run++; ctrl?.abort(); busy = false; const c = chat(), m = c?.msgs[c.msgs.length - 1]; if (m && m.r == "a" && !m.t) m.t = "(stopped)"; save(); draw(); return; }
  const v = $("#q").value; $("#q").value = ""; grow(); ask(v);
};
$("#mic").onclick = () => { const R = window.SpeechRecognition || window.webkitSpeechRecognition; if (!R) return toast("Voice input is not supported here. Try Chrome."); const r = new R(); r.lang = C.lang == "hi" ? "hi-IN" : "en-IN"; r.onresult = (e) => { $("#q").value = e.results[0][0].transcript; grow(); }; r.start(); toast("Listening…"); };
$("#ex").onclick = () => { const c = chat(); if (!c) return toast("Nothing to export yet.");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["# " + c.title + "\n\n" + c.msgs.map((m) => (m.r == "u" ? "**You:** " : "**Answer:** ") + m.t).join("\n\n")], { type: "text/markdown" })); a.download = "chat.md"; a.click(); };
/* ---------- answer mode, settings, chrome ---------- */
const MODES = { auto: ["✨ Smart", "Smart: I chat normally and answer from your documents when you ask about them."], docs: ["📚 Sources only", "Sources only: answers come strictly from your documents."], chat: ["💬 Chat", "General chat: documents are not used."] };
const drawMode = () => { $("#mb").textContent = (MODES[cfg.answer_mode] || MODES.auto)[0]; };
async function putCfg(patch) { try { cfg = await api.putSettings(patch); drawMode(); toast("Saved"); } catch (e) { toast(e.message); } }
$("#mb").onclick = () => { const k = Object.keys(MODES); const n = k[(k.indexOf(cfg.answer_mode) + 1) % k.length]; putCfg({ answer_mode: n }).then(() => toast(MODES[n][1])); };
async function drawSet() {
  const s = (cfg = await api.getSettings()), f = $("#setForm"); f.innerHTML = "";
  const add = (key, label, kind, opts) => { const w = document.createElement("label"); w.className = "sm"; w.textContent = label; let i;
    if (kind == "select") { i = document.createElement("select"); opts.forEach(([v, t]) => { const o = document.createElement("option"); o.value = v; o.textContent = t; i.append(o); }); i.value = s[key]; }
    else if (kind == "check") { i = document.createElement("input"); i.type = "checkbox"; i.checked = !!s[key]; i.style.width = "auto"; w.style.cssText = "display:flex;gap:8px;align-items:center"; }
    else { i = document.createElement("input"); i.type = "number"; i.min = 1; i.max = 10; i.value = s[key]; }
    i.onchange = () => putCfg({ [key]: kind == "check" ? i.checked : kind == "number" || key == "chunk_size" ? +i.value : i.value }); w.append(i); f.append(w); };
  add("answer_mode", "Answer mode", "select", [["auto", "Smart: chat + your documents"], ["docs", "Sources only (strict)"], ["chat", "General chat"]]);
  add("search_mode", "Search mode", "select", [["hybrid", "hybrid"], ["keyword", "keyword"], ["vector", "vector"]]);
  add("top_k", "Passages (top-k)", "number"); add("chunk_size", "Chunk size (new sources only)", "select", [["200", "200"], ["500", "500"], ["1000", "1000"]]);
  add("rerank", "LLM rerank", "check"); add("mmr", "MMR (more variety)", "check");
}
arm($("#wipe"), "Click again to delete everything", async () => { try { await api.deleteAll(); C.chats = {}; C.chat = null; C.cur = null; $("#setM").hidden = true; toast("All data deleted"); loadNbs(); } catch (e) { toast(e.message); } });
const effTheme = () => C.theme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
$("#thm").onclick = () => { C.theme = effTheme() == "dark" ? "light" : "dark"; document.documentElement.dataset.theme = C.theme; save(); };
function setLang(l) { C.lang = l; document.documentElement.lang = l; document.querySelectorAll("[data-i]").forEach((n) => (n.textContent = L[l][n.dataset.i])); $("#q").placeholder = L[l].ph; save(); draw(); }
$("#lg").onclick = () => setLang(C.lang == "en" ? "hi" : "en");
$("#hb").onclick = () => $("#sb").classList.add("open"); $("#sbx").onclick = () => $("#sb").classList.remove("open");
$("#sb2").onclick = () => { $("#dr").hidden = !$("#dr").hidden; }; $("#dx").onclick = () => ($("#dr").hidden = true);
$("#lb").onclick = () => { $("#labM").hidden = false; renderLab({ nb: C.cur }, toast); }; $("#stg").onclick = () => { $("#setM").hidden = false; drawSet(); };
document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => ($("#" + b.dataset.close).hidden = true)));
document.addEventListener("keydown", (e) => { if (e.key == "Escape") ["labM", "setM"].forEach((x) => ($("#" + x).hidden = true)); if (e.ctrlKey && e.key.toLowerCase() == "k") { e.preventDefault(); $("#cf").focus(); } });
/* ---------- start ---------- */
if (C.theme) document.documentElement.dataset.theme = C.theme;
(async () => {
  setLang(C.lang); $("#ver").textContent = "App version 2.3"; document.title = "RAG Lab Pro v2.3";
  try { const h = await (await fetch("/api/health")).json(); if (!h.gemini_key_present) $("#hs").textContent = "⚠ GEMINI_API_KEY is missing on the server."; } catch (e) { /* ignore */ }
  try { cfg = await api.getSettings(); drawMode(); await loadNbs(); } catch (e) { toast(e.message); }
})();

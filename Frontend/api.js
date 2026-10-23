// Small fetch helpers. All server calls go through here.
export async function j(url, opts = {}, retried = false) {
  const res = await fetch(url, opts);
  if (res.status === 401 && !retried) {  // APP_PASSWORD is set: ask once, then retry
    const password = prompt("Password");
    if (password) { await post("/api/login", { password }); return j(url, opts, true); }
  }
  if (!res.ok) {
    let msg = "Something went wrong.";
    try { msg = (await res.json()).detail || msg; } catch (_) { /* keep default */ }
    throw new Error(msg);
  }
  return res.json();
}
export const post = (url, body) => j(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  notebooks: () => j("/api/notebooks"),
  newNotebook: (name) => post("/api/notebooks", { name }),
  delNotebook: (id) => j(`/api/notebooks/${id}`, { method: "DELETE" }),
  sources: (id) => j(`/api/notebooks/${id}/sources`),
  addLink: (id, type, url) => post(`/api/notebooks/${id}/links`, { type, url }),
  officialSearch: (q) => j("/api/official/search?q=" + encodeURIComponent(q)),
  upload: (id, file) => { const f = new FormData(); f.append("file", file); return j(`/api/notebooks/${id}/sources`, { method: "POST", body: f }); },
  delSource: (id) => j(`/api/sources/${id}`, { method: "DELETE" }),
  messages: (id) => j(`/api/notebooks/${id}/messages`),
  context: (cid) => j(`/api/chunks/${cid}/context`),
  feedback: (message_id, rating) => post("/api/feedback", { message_id, rating }),
  study: (id, kind) => post(`/api/notebooks/${id}/study/${kind}`, {}),
  getSettings: () => j("/api/settings"),
  putSettings: (s) => j("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s) }),
  deleteAll: () => j("/api/data", { method: "DELETE" }),
};

// Read a Server-Sent Events stream from a POST request. Calls onEvent(name, data) for each event.
export const streamChat = (body, onEvent) => streamPost("/api/chat", body, onEvent);
export async function streamPost(url, body, onEvent) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Could not start the chat.");
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = /^event: (.*)$/m.exec(block), data = /^data: (.*)$/m.exec(block);
      if (ev && data) onEvent(ev[1], JSON.parse(data[1]));
    }
  }
}

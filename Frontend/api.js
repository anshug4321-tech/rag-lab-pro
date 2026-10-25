// Server calls. All fetch code lives here. If the server asks for a password (APP_PASSWORD), we show the login box once.
async function needLogin() {
  const pw = window.askPassword ? await window.askPassword() : prompt("Password");
  if (!pw) return false;
  await post("/api/login", { password: pw });
  return true;
}
export async function j(url, opts = {}, retried = false) {
  const res = await fetch(url, opts);
  if (res.status === 401 && !retried && (await needLogin())) return j(url, opts, true);
  if (!res.ok) {
    let msg = "Something went wrong.";
    try { msg = (await res.json()).detail || msg; } catch (_) { /* keep default */ }
    throw new Error(msg);
  }
  return res.json();
}
const jsonOpts = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const post = (url, body) => j(url, jsonOpts("POST", body));

export const api = {
  notebooks: () => j("/api/notebooks"),
  newNotebook: (name) => post("/api/notebooks", { name }),
  renameNotebook: (id, name) => j(`/api/notebooks/${id}`, jsonOpts("PUT", { name })),
  delNotebook: (id) => j(`/api/notebooks/${id}`, { method: "DELETE" }),
  sources: (id) => j(`/api/notebooks/${id}/sources`),
  upload: (id, file) => { const f = new FormData(); f.append("file", file); return j(`/api/notebooks/${id}/sources`, { method: "POST", body: f }); },
  addText: (id, title, text, url = "") => post(`/api/notebooks/${id}/text`, { title, text, url }),
  addLink: (id, type, url) => post(`/api/notebooks/${id}/links`, { type, url }),
  setEnabled: (sid, enabled) => j(`/api/sources/${sid}`, jsonOpts("PUT", { enabled })),
  delSource: (sid) => j(`/api/sources/${sid}`, { method: "DELETE" }),
  context: (cid) => j(`/api/chunks/${cid}/context`),
  feedback: (message_id, rating) => post("/api/feedback", { message_id, rating }),
  study: (id, kind) => post(`/api/notebooks/${id}/study/${kind}`, {}),
  getSettings: () => j("/api/settings"),
  putSettings: (s) => j("/api/settings", jsonOpts("PUT", s)),
  deleteAll: () => j("/api/data", { method: "DELETE" }),
};

// Read a Server-Sent Events stream from a POST request. onEvent(name, data) is called for each event.
export async function streamPost(url, body, onEvent, signal, retried = false) {
  const res = await fetch(url, { ...jsonOpts("POST", body), signal });
  if (res.status === 401 && !retried && (await needLogin())) return streamPost(url, body, onEvent, signal, true);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Could not start the request.");
  const reader = res.body.getReader(), dec = new TextDecoder();
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

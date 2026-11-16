(function () {
"use strict";

// ── data and indexes ───────────────────────────────────────────────────
const D = JSON.parse(document.getElementById("trace-data").textContent);
const META = D.meta || {};
const NODES = D.nodes || [];
const EDGES = D.edges || [];
const FILES = D.files || { dirs: [], rows: [] };
const byId = new Map(), outE = new Map(), inE = new Map();
for (const n of NODES) { byId.set(n.id, n); n._t0 = tparse(n.ts_start || n.ts); n._t1 = tparse(n.ts_end) ?? n._t0; }
for (const e of EDGES) { push(outE, e.from, e); push(inE, e.to, e); }
function push(m, k, v) { let l = m.get(k); if (!l) m.set(k, l = []); l.push(v); }
const agents = NODES.filter(n => n.kind === "agent");
const acts = NODES.filter(n => n.kind === "call" || n.kind === "command").sort(byTime);
const msgs = NODES.filter(n => n.kind === "message").sort(byTime);
const childCalls = new Map();
for (const n of acts) if (n.kind === "call" && n.parent && byId.has(n.parent)) push(childCalls, n.parent, n);
// file rows: [dir, name, bytes, sha256, mtime, origin, generated_by, basis, node, copied, present, before_trace]
const FROWS = FILES.rows.map((r, i) => ({ i, path: (FILES.dirs[r[0]] ? FILES.dirs[r[0]] + "/" : "") + r[1], bytes: r[2], sha: r[3], mtime: r[4], origin: r[5], gen: r[6], basis: r[7], node: r[8], copied: !!r[9], present: !!r[10], before: !!r[11] }));
const rowsByNode = new Map(), rowsByGen = new Map(), rowByPath = new Map();
for (const r of FROWS) { if (r.node) push(rowsByNode, r.node, r); if (r.gen) push(rowsByGen, r.gen, r); if (r.present) rowByPath.set(r.path, r); }

let T0 = null, T1 = null;
for (const n of NODES) if (n._t0 != null) { T0 = T0 == null ? n._t0 : Math.min(T0, n._t0); T1 = T1 == null ? n._t1 : Math.max(T1, n._t1); }
if (T0 == null) { T0 = 0; T1 = 1; }
if (T1 <= T0) T1 = T0 + 1;

// agent tree (spawn order), work per agent
const kids = new Map(), depth = new Map(), order = [];
const visible = agents.filter(a => a.id !== "@system");
for (const a of visible) { const p = a.parent && byId.has(a.parent) ? a.parent : null; push(kids, p, a); }
for (const l of kids.values()) l.sort((x, y) => (x.external ? -1 : 0) - (y.external ? -1 : 0) || (x._t0 ?? 9e15) - (y._t0 ?? 9e15));
(function walk(p, d) { for (const a of kids.get(p) || []) { if (depth.has(a.id)) continue; depth.set(a.id, d); order.push(a); walk(a.id, d + 1); } })(null, 0);
for (const a of visible) if (!depth.has(a.id)) { depth.set(a.id, 0); order.push(a); }
const stats = new Map(agents.map(a => [a.id, { calls: 0, cmds: 0, failed: 0, inN: 0, outN: 0 }]));
for (const n of acts) { const s = stats.get(n.agent); if (!s) continue; n.kind === "call" ? s.calls++ : s.cmds++; if (n.ok === false) s.failed++; }
for (const m of msgs) { if (m.event === "spawn") continue; stats.get(m.from) && stats.get(m.from).outN++; stats.get(m.to) && stats.get(m.to).inN++; }

// ── helpers ────────────────────────────────────────────────────────────
function byTime(a, b) { return (a._t0 ?? 0) - (b._t0 ?? 0); }
function tparse(s) { if (s == null) return null; if (typeof s === "number") return s; const v = Date.parse(s); return isNaN(v) ? null : v / 1000; }
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    const v = attrs[k];
    if (v == null || v === false) continue;
    if (k === "class") el.className = v; else if (k === "text") el.textContent = v; else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) { const v = attrs[k]; if (v == null) continue; if (k === "text") el.textContent = v; else el.setAttribute(k, v); }
  for (const c of children.flat()) if (c) el.append(c);
  return el;
}
const $ = id => document.getElementById(id);
function txt(v) { return v == null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2); }
function oneLine(v) { return txt(v).replace(/\s+/g, " ").trim(); }
function clip(v, n) { v = String(v ?? ""); return v.length > n ? v.slice(0, n - 1) + "…" : v; }
function base(p) { return String(p || "").split("/").pop(); }
function fmtBytes(b) { if (b == null) return "—"; const u = ["B", "kB", "MB", "GB", "TB"]; let i = 0, v = b; while (v >= 1000 && i < 4) { v /= 1000; i++; } return (i ? v.toFixed(v < 10 ? 1 : 0) : v) + " " + u[i]; }
function fmtDur(x) { if (x == null || isNaN(x)) return ""; if (x < 1) return Math.round(x * 1000) + " ms"; if (x < 60) return x.toFixed(x < 10 ? 1 : 0) + " s"; const m = Math.floor(x / 60), r = Math.round(x % 60); if (m < 60) return m + " min " + String(r).padStart(2, "0") + " s"; return Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0") + " min"; }
function fmtShort(x) { if (x < 60) return Math.round(x) + " s"; const m = Math.round(x / 60); return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0"); }
function fmtOff(t, from) { if (t == null) return ""; const d = Math.max(0, t - (from ?? T0)); const hh = Math.floor(d / 3600), mm = Math.floor(d % 3600 / 60), ss = Math.floor(d % 60); return "+" + (hh ? hh + ":" + String(mm).padStart(2, "0") : mm) + ":" + String(ss).padStart(2, "0"); }
function fmtAbs(t) { return t == null ? "" : new Date(t * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC"); }
function fmtClock(t) { return t == null ? "" : new Date(t * 1000).toISOString().slice(11, 16) + " UTC"; }
function label(id) { const a = byId.get(id); return a ? (a.label || id) : (id || "?"); }
function shortModel(m) { return String(m || "").replace(/^claude-/, "").replace(/^gpt-/, "gpt-"); }
function isFail(n) { return n.ok === false; }
function cmdText(c) { let t = String(c || "").trim(); for (let i = 0; i < 3; i++) { const m = t.match(/^cd\s+("[^"]*"|'[^']*'|\S+)\s*(&&|;)\s*/); if (!m) break; t = t.slice(m[0].length); } return t; }
function midPath(p, n) { p = String(p || ""); n = n || 90; return p.length > n ? "…" + p.slice(-(n - 1)) : p; }
function sha(x, n) { return x ? x.slice(0, n || 8) : "no hash"; }
function agentStart(id) { const a = byId.get(id); return a && a._t0 != null ? a._t0 : T0; }

// tooltip
const tip = $("tip");
function showTip(ev, text) { tip.textContent = text; tip.style.display = "block"; tip.style.left = Math.min(ev.clientX + 14, innerWidth - 400) + "px"; tip.style.top = Math.min(ev.clientY + 16, innerHeight - 80) + "px"; }
function hideTip() { tip.style.display = "none"; }

// ── header ─────────────────────────────────────────────────────────────
function header() {
  const c = META.counts || {};
  const title = META.title || META.session || "trace";
  $("run-title").textContent = title;
  document.title = title + " · provenance trace";
  const harness = [...new Set(agents.map(a => a.harness).filter(Boolean))].join(", ") || "—";
  const models = [...new Set(agents.map(a => a.model).filter(Boolean))].map(shortModel).join(", ") || "—";
  const kits = new Map(); for (const n of acts) if (n.kind === "call" && n.toolkit) kits.set(n.toolkit, n.version);
  const kitStr = [...kits].map(([k, v]) => v ? `${k} ${v}` : k).join(", ") || "—";
  const facts = [
    ["Harness", harness], ["Models", models], ["Toolkits", kitStr],
    ["Span", `${fmtAbs(T0).slice(0, 16)} · ${fmtDur(T1 - T0)}`],
    ["Agents", String(agents.filter(a => !a.external).length)],
    ["Tool calls", `${c.calls ?? 0}${c.failed_calls ? ` · ${c.failed_calls} failed` : ""}`],
    ["Shell commands", String(c.commands ?? 0)],
    ["Files", `${(c.files ?? FROWS.length).toLocaleString()}${c.unknown_origin ? ` · ${c.unknown_origin.toLocaleString()} unknown origin` : ""}`],
  ];
  $("facts").replaceChildren(...facts.map(([k, v]) => h("div", null, h("dt", { text: k }), h("dd", { text: v, title: v }))));
  const w = META.warnings || [];
  if (w.length || META.complete === false) {
    const btn = $("notes-btn");
    btn.hidden = false;
    btn.textContent = (META.complete === false ? "Incomplete trace · " : "") + `${w.length} note${w.length === 1 ? "" : "s"}`;
    $("notes-list").replaceChildren(...w.map(x => h("li", { text: x })));
    btn.onclick = () => { $("notes").hidden = !$("notes").hidden; };
    document.addEventListener("click", e => { if (!$("notes").hidden && !e.target.closest("#notes,#notes-btn")) $("notes").hidden = true; });
  }
  const saved = (() => { try { return localStorage.getItem("tb-trace-theme"); } catch (_) { return null; } })();
  if (saved === "dark") document.documentElement.dataset.theme = "dark";
  $("theme-btn").onclick = () => {
    const r = document.documentElement, next = r.dataset.theme === "dark" ? "light" : "dark";
    r.dataset.theme = next; try { localStorage.setItem("tb-trace-theme", next); } catch (_) { }
  };
}

// ── pan/zoom canvas (agent graph and provenance) ───────────────────────
function panzoom(canvas, world, onChange) {
  const v = { x: 20, y: 20, k: 1, w: 100, h: 100 };
  const apply = () => { world.style.transform = `translate(${v.x}px,${v.y}px) scale(${v.k})`; onChange && onChange(v); };
  let drag = null;
  canvas.addEventListener("mousedown", e => { if (e.button !== 0) return; drag = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y, moved: false }; });
  addEventListener("mousemove", e => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return; drag.moved = true; canvas.classList.add("drag"); v.x = drag.vx + dx; v.y = drag.vy + dy; apply(); });
  addEventListener("mouseup", () => { if (drag && drag.moved) { canvas.dataset.dragged = "1"; setTimeout(() => delete canvas.dataset.dragged, 0); } drag = null; canvas.classList.remove("drag"); });
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    if (e.shiftKey) { v.x -= e.deltaY; v.y -= e.deltaX; apply(); return; }
    const k2 = Math.min(2.5, Math.max(0.2, v.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016))));
    v.x = mx - (mx - v.x) * (k2 / v.k); v.y = my - (my - v.y) * (k2 / v.k); v.k = k2;
    apply();
  }, { passive: false });
  return {
    v, apply,
    fit(maxK) {
      const cw = canvas.clientWidth || 600, ch = canvas.clientHeight || 400;
      v.k = Math.min(maxK || 1.1, (cw - 40) / v.w, (ch - 40) / v.h); v.k = Math.max(0.25, v.k);
      v.x = Math.max(20, (cw - v.w * v.k) / 2); v.y = Math.max(20, (ch - v.h * v.k) / 2) * (v.h * v.k < ch ? 0.6 : 1);
      apply();
    },
    zoom(f) { const cw = canvas.clientWidth / 2, ch = canvas.clientHeight / 2; const k2 = Math.min(2.5, Math.max(0.2, v.k * f)); v.x = cw - (cw - v.x) * (k2 / v.k); v.y = ch - (ch - v.y) * (k2 / v.k); v.k = k2; apply(); },
    center(x, y) { const cw = canvas.clientWidth, ch = canvas.clientHeight; v.x = cw / 2 - x * v.k; v.y = ch / 2 - y * v.k; apply(); },
  };
}

// ── agent graph ────────────────────────────────────────────────────────
const AW = 272, AH = 84, INDENT = 44, VGAP = 22;
const G = { pos: new Map(), pz: null, fitted: false };
function layoutAgents() {
  G.pos.clear();
  let y = 0;
  for (const a of order) {
    const d = depth.get(a.id);
    G.pos.set(a.id, { x: d * INDENT + (d ? 20 : 0), y });
    y += AH + VGAP + (a.external ? 10 : 0);
  }
  let maxX = 0; for (const p of G.pos.values()) maxX = Math.max(maxX, p.x + AW);
  return { w: maxX + 60 + 18 * 8, h: Math.max(AH, y - VGAP) };
}
function renderGraph() {
  const dims = layoutAgents();
  const cards = $("graph-cards"), svg = $("graph-edges");
  cards.textContent = ""; svg.textContent = "";
  svg.setAttribute("width", dims.w); svg.setAttribute("height", dims.h);
  // hierarchy elbows
  if ($("e-tree").checked) {
    for (const a of order) {
      const p = a.parent && G.pos.get(a.parent), q = G.pos.get(a.id);
      if (!p || !q) continue;
      const x = p.x + 18, y1 = p.y + AH, y2 = q.y + AH / 2;
      svg.append(s("path", { class: "e-tree", d: `M${x},${y1} L${x},${y2 - 8} Q${x},${y2} ${x + 8},${y2} L${q.x},${y2}` }));
    }
    const user = order.find(a => a.id === "@user"), root = order.find(a => !a.external && !a.parent);
    if (user && root) { const p = G.pos.get(user.id), q = G.pos.get(root.id); svg.append(s("path", { class: "e-tree", d: `M${p.x + 18},${p.y + AH} L${q.x + 18},${q.y}` })); }
  }
  // message arcs on the right, one per pair, width by count
  if ($("e-msg").checked) {
    const pairs = new Map();
    for (const m of msgs) {
      if (m.event === "spawn" || !G.pos.has(m.from) || !G.pos.has(m.to) || m.from === m.to) continue;
      const k = [m.from, m.to].sort().join("|"); const v = pairs.get(k) || { a: m.from, b: m.to, n: 0 }; v.n++; pairs.set(k, v);
    }
    const list = [...pairs.values()].sort((u, w) => Math.abs(G.pos.get(u.a).y - G.pos.get(u.b).y) - Math.abs(G.pos.get(w.a).y - G.pos.get(w.b).y));
    let right = 0; for (const p of G.pos.values()) right = Math.max(right, p.x + AW);
    list.forEach((pr, lane) => {
      const p = G.pos.get(pr.a), q = G.pos.get(pr.b);
      const ya = p.y + AH / 2 + 6, yb = q.y + AH / 2 - 6;
      const bx = right + 22 + lane * 16, r = 8;
      const top = Math.min(ya, yb), bot = Math.max(ya, yb), xa = p.x + AW, xb = q.x + AW;
      const [x1, y1, x2, y2] = ya < yb ? [xa, ya, xb, yb] : [xb, yb, xa, ya];
      const d = `M${x1},${y1} L${bx - r},${y1} Q${bx},${y1} ${bx},${y1 + r} L${bx},${y2 - r} Q${bx},${y2} ${bx - r},${y2} L${x2},${y2}`;
      const path = s("path", { class: "e-msg", "stroke-width": Math.min(2.6, 1 + Math.log2(pr.n) * 0.4), d });
      path.append(s("title", { text: `${label(pr.a)} ↔ ${label(pr.b)}: ${pr.n} message${pr.n === 1 ? "" : "s"}` }));
      svg.append(path);
      const ly = (top + bot) / 2;
      svg.append(s("rect", { class: "e-msg-bg", x: bx - 9, y: ly - 8, width: 18, height: 16, rx: 8 }));
      svg.append(s("text", { class: "e-msg-label", x: bx, y: ly + 3.5, "text-anchor": "middle", text: String(pr.n) }));
    });
  }
  for (const a of order) {
    const p = G.pos.get(a.id), st = stats.get(a.id) || {};
    const ended = a.ts_end || a.terminated;
    const state = a.external ? "human" : ended ? "ended" : "no end recorded";
    const sub = a.external ? "the request" : [shortModel(a.model), a.engine].filter(Boolean).join(" · ") || a.id;
    const card = h("div", { class: "acard" + (a.external ? " human" : "") + (S.agent === a.id ? " sel" : ""), style: `left:${p.x}px;top:${p.y}px`, "data-id": a.id },
      h("div", { class: "a-top" }, h("span", { class: "dot" + (!ended && !a.external ? " live" : "") }), h("span", { class: "a-name", text: a.label || a.id }), h("span", { class: "a-state", text: state })),
      h("div", { class: "a-sub", text: sub, title: a.id }),
      a.external ? h("div", { class: "a-stats" }, h("span", { text: `${st.outN} message${st.outN === 1 ? "" : "s"}` })) :
        h("div", { class: "a-stats" },
          h("span", null, h("i", { style: "background:var(--tool)" }), `${st.calls} calls`),
          h("span", null, h("i", { style: "background:var(--shell)" }), `${st.cmds} shell`),
          st.failed ? h("span", { class: "bad", text: `${st.failed} failed` }) : null,
          a._t0 != null && a._t1 > a._t0 ? h("span", { class: "muted", style: "margin-left:auto", text: fmtShort(a._t1 - a._t0) }) : null),
      st.inN + st.outN ? h("span", { class: "badge-msg", title: `${st.inN} received, ${st.outN} sent`, text: `↓${st.inN} ↑${st.outN}` }) : null);
    card.addEventListener("click", () => { if ($("graph-canvas").dataset.dragged) return; openAgent(a.id); });
    cards.append(card);
  }
  if (G.pz) { G.pz.v.w = dims.w; G.pz.v.h = dims.h; if (!G.fitted) { G.fitted = true; G.pz.fit(1); } }
}
function zoomLabel(v) { $("z-pct").textContent = Math.round(v.k * 100) + "%"; }

// ── the work sequence of one agent ─────────────────────────────────────
const S = { tab: "agents", agent: null, kinds: new Set(["call", "command", "message"]), q: "", tool: "", failed: false, open: new Set(), hl: null, provFocus: null, fileSel: null };
let SEQ = { items: [], rendered: 0, obs: null };
const CHUNK = 60;

function agentItems(aid) {
  const items = [];
  const a = byId.get(aid);
  for (const n of acts) if (n.agent === aid && !(n.kind === "call" && n.parent && byId.has(n.parent))) items.push(n);
  for (const m of msgs) {
    if (m.event === "spawn") { if (m.from === aid) items.push(m); continue; }
    if (m.from === aid || m.to === aid) items.push(m);
  }
  items.sort(byTime);
  const brief = a && a.spawn && byId.get(a.spawn);
  // Conjure also delivers the brief as the child's first message: show it once.
  if (brief && brief.initial_message) {
    const i = items.findIndex(m => m.kind === "message" && m.event === "send" && m.to === aid && m.from === brief.from && txt(m.body).trim() === String(brief.initial_message).trim());
    if (i >= 0) items.splice(i, 1);
  }
  return { brief: brief && (brief.initial_message || brief.role_prompt || brief.toolkits) ? brief : null, items };
}
function passesSeq(n) {
  const k = n.kind === "message" ? "message" : n.kind;
  if (!S.kinds.has(k)) return false;
  if (S.failed && !isFail(n)) return false;
  if (S.tool && n.kind !== "message" && toolKey(n) !== S.tool) return false;
  if (S.tool && n.kind === "message") return false;
  if (S.q && !hay(n).includes(S.q)) return false;
  return true;
}
function toolKey(n) { return n.kind === "call" ? n.tool : n.kind === "command" ? (n.tool === "Bash" || n.tool === "exec_command" || n.tool === "shell" ? "shell" : n.tool) : ""; }
const hayCache = new Map();
function hay(n) {
  let v = hayCache.get(n.id);
  if (v === undefined) {
    v = [n.id, n.tool, n.toolkit, n.command, txt(n.body), n.error, n.initial_message, (n.paths || []).join(" "),
      n.args ? JSON.stringify(n.args) : "", n.result ? n.result.head : "", n.output ? n.output.head : ""].filter(Boolean).join("\n").toLowerCase();
    hayCache.set(n.id, v);
  }
  return v;
}

function openAgent(aid, opts) {
  opts = opts || {};
  if (S.tab !== "agents") setTab("agents", true);
  const same = S.agent === aid;
  S.agent = aid;
  $("v-agents").classList.remove("solo");
  for (const c of document.querySelectorAll(".acard")) c.classList.toggle("sel", c.dataset.id === aid);
  if (!same || opts.reset) { S.q = ""; $("s-q").value = ""; S.tool = ""; S.failed = false; $("s-failed").checked = false; }
  renderSeqHead(aid);
  fillToolSelect(aid);
  renderSeq(opts.target);
  if (!opts.noHash) setHash(aid);
  refitGraph();
}
function closeAgent() {
  S.agent = null; $("v-agents").classList.add("solo");
  for (const c of document.querySelectorAll(".acard")) c.classList.remove("sel");
  setHash("");
  refitGraph();
}
let refitT = 0;
function refitGraph() { clearTimeout(refitT); requestAnimationFrame(() => G.pz.fit(1)); refitT = setTimeout(() => G.pz.fit(1), 240); }
function renderSeqHead(aid) {
  const a = byId.get(aid), st = stats.get(aid) || {};
  const head = $("seq-head");
  const ended = a.ts_end || a.terminated;
  head.replaceChildren(
    h("h2", null, a.label || a.id, h("span", { class: "tag", text: a.external ? "human" : ended ? "ended" : "no end recorded" }),
      h("button", { class: "btn ghost small close", text: "Close", title: "Back to the full graph", onclick: closeAgent })),
    h("div", { class: "sub" }, [shortModel(a.model), a.engine, a.harness].filter(Boolean).join(" · "), a.parent ? [" · spawned by ", h("a", { href: "#" + a.parent, text: label(a.parent) })] : null, h("span", { class: "faint mono", text: "  " + a.id })),
    a.external ? null : h("div", { class: "facts" },
      h("span", null, h("b", { text: st.calls }), " tool calls"), h("span", null, h("b", { text: st.cmds }), " shell commands"),
      st.failed ? h("span", null, h("b", { text: st.failed, style: "color:var(--bad)" }), " failed") : null,
      h("span", null, h("b", { text: st.inN }), " received · ", h("b", { text: st.outN }), " sent"),
      a._t0 != null ? h("span", null, fmtAbs(a._t0).slice(0, 16), " · ", h("b", { text: fmtDur((a._t1 ?? a._t0) - a._t0) || "—" })) : null));
}
function fillToolSelect(aid) {
  const sel = $("s-tool"), cnt = new Map();
  for (const n of acts) if (n.agent === aid) { const k = toolKey(n); cnt.set(k, (cnt.get(k) || 0) + 1); }
  sel.replaceChildren(h("option", { value: "", text: "All tools" }), ...[...cnt].sort((x, y) => y[1] - x[1]).map(([k, c]) => h("option", { value: k, text: `${k} (${c})` })));
  sel.value = S.tool;
}
function renderSeq(target) {
  const box = $("seq"), scroll = $("seq-scroll");
  box.textContent = "";
  if (SEQ.obs) SEQ.obs.disconnect();
  const aid = S.agent; if (!aid) return;
  const { brief, items } = agentItems(aid);
  const list = items.filter(passesSeq);
  SEQ = { items: list, rendered: 0, obs: null, from: agentStart(aid) };
  if (brief && S.kinds.has("message") && !S.q && !S.tool && !S.failed) box.append(messageCard(brief, aid, true));
  if (!list.length) box.append(h("div", { class: "seq-empty", text: items.length ? "Nothing matches these filters." : "No recorded work for this agent." }));
  const sentinel = h("div", { class: "more-sentinel" });
  box.append(sentinel);
  SEQ.sentinel = sentinel;
  const upto = target ? list.findIndex(n => n.id === target) : -1;
  appendChunk(Math.max(CHUNK, upto + 10));
  SEQ.obs = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) appendChunk(CHUNK); }, { root: scroll, rootMargin: "800px" });
  SEQ.obs.observe(sentinel);
  if (!target) scroll.scrollTop = 0;
}
function appendChunk(n) {
  const end = Math.min(SEQ.items.length, SEQ.rendered + n);
  const frag = document.createDocumentFragment();
  for (let i = SEQ.rendered; i < end; i++) frag.append(itemEl(SEQ.items[i]));
  SEQ.sentinel.before(frag);
  SEQ.rendered = end;
  if (end >= SEQ.items.length) {
    SEQ.sentinel.textContent = "";
    const a = byId.get(S.agent);
    if (a && a.terminated && !SEQ.sentinel.previousSibling?.classList?.contains("endmark"))
      SEQ.sentinel.before(h("div", { class: "endmark", text: `Terminated ${fmtOff(a._t1, SEQ.from)}${a.terminated.by ? " by " + label(a.terminated.by) : ""}` }));
    if (SEQ.obs) SEQ.obs.disconnect();
  } else SEQ.sentinel.textContent = `${SEQ.items.length - end} more…`;
}
function itemEl(n) {
  if (n.kind === "message") return messageCard(n, S.agent, false);
  return n.kind === "call" ? callBox(n) : shellBox(n);
}

// ── boxes ──────────────────────────────────────────────────────────────
function toggleBox(el, n, build) {
  const open = !el.classList.contains("open");
  el.classList.toggle("open", open);
  open ? S.open.add(n.id) : S.open.delete(n.id);
  const body = el.querySelector(":scope > .t-body");
  body.replaceChildren(...build(open));
}
function callBox(n) {
  const fail = isFail(n);
  const el = h("div", { class: "tbox" + (fail ? " fail" : ""), id: "box-" + n.id, "data-id": n.id });
  const from = agentStart(n.agent);
  el.append(
    h("div", { class: "t-title", title: "Expand or collapse" }, h("span", { class: "chev", text: "▶" }), n.tool || "call", fail ? h("span", { class: "tag bad", text: "failed" }) : null),
    h("div", { class: "t-kit", text: `${n.toolkit || "?"}${n.version ? "@" + n.version : ""}` }),
    h("div", { class: "t-meta" }, h("span", { title: fmtAbs(n._t0), text: `${fmtOff(n._t0, from)}  ·  ${fmtClock(n._t0)}` }), n._t1 > n._t0 ? h("span", { text: fmtDur(n._t1 - n._t0) }) : null,
      h("span", { class: "id" }, h("a", { href: "#" + n.id, title: "Link to this call", text: n.id }))),
    h("div", { class: "t-body" }));
  const build = open => open ? callFull(n) : callSummary(n);
  el.querySelector(".t-body").append(...build(S.open.has(n.id)));
  if (S.open.has(n.id)) el.classList.add("open");
  el.querySelector(".t-title").addEventListener("click", () => toggleBox(el, n, build));
  return el;
}
function argPairs(args) { return Object.entries(args || {}).filter(([, v]) => v != null); }
function valStr(v) { return typeof v === "string" ? v : JSON.stringify(v); }
function callSummary(n) {
  const pairs = argPairs(n.args);
  const out = [];
  if (pairs.length) out.push(h("div", { class: "t-row" }, h("span", { class: "k", text: "args" }), h("span", { class: "v", title: pairs.map(([k, v]) => `${k} = ${valStr(v)}`).join("\n"), text: pairs.map(([k, v]) => `${k} = ${clip(oneLine(valStr(v)), 70)}`).join(";  ") })));
  if (isFail(n)) out.push(h("div", { class: "t-row" }, h("span", { class: "k", text: "result" }), h("span", { class: "v", style: "color:var(--bad)", text: "failed: " + oneLine(n.error || (n.result && n.result.head) || "") })));
  else if (n.result && n.result.head) out.push(h("div", { class: "t-row" }, h("span", { class: "k", text: "result" }), h("span", { class: "v", text: oneLine(n.result.head) })));
  const files = fileEdges(n);
  const outs = files.filter(e => e.kind === "generated");
  if (outs.length) out.push(h("div", { class: "t-row" }, h("span", { class: "k", text: "output" }), h("span", { class: "v", text: outs.slice(0, 3).map(e => { const f = byId.get(e.to); return `${base((f.paths || [])[0])} (${sha(f.sha256, 6)}, ${fmtBytes(f.bytes)})`; }).join(";  ") + (outs.length > 3 ? `;  +${outs.length - 3} more` : "") })));
  const kidsN = (childCalls.get(n.id) || []).length;
  if (kidsN) out.push(h("div", { class: "t-row" }, h("span", { class: "k", text: "jobs" }), h("span", { class: "v", text: `${kidsN} inner call${kidsN === 1 ? "" : "s"}` })));
  return out;
}
function argsGrid(args) {
  const g = h("div", { class: "args" });
  for (const [k, v] of argPairs(args)) {
    const full = valStr(v);
    const long = full.length > 180 || full.split("\n").length > 4;
    const vEl = h("div", { class: "v" });
    const setV = open => { vEl.replaceChildren(open || !long ? full : clip(full.split("\n").slice(0, 3).join("\n"), 180)); if (long) vEl.append(h("a", { class: "tog-more", text: open ? "less" : `more (${full.length.toLocaleString()} chars)`, onclick: ev => { ev.stopPropagation(); setV(!open); } })); };
    setV(false);
    g.append(h("div", { class: "k", text: k }), vEl);
  }
  return g;
}
function prettyHead(head) {
  if (!head) return "";
  const t = head.trim();
  if ((t.startsWith("{") || t.startsWith("[")) && t.length < 200000) { try { return JSON.stringify(JSON.parse(t), null, 2); } catch (_) { } }
  return head;
}
function sec(title, ...body) { return h("div", { class: "t-sec" }, h("h4", { text: title }), ...body); }
function callFull(n) {
  const out = [];
  if (isFail(n) && n.error) out.push(sec("error", h("div", { class: "err", text: n.error })));
  if (argPairs(n.args).length) out.push(sec("arguments", argsGrid(n.args)));
  if (n.result && !(isFail(n) && n.error && n.result.head && n.result.head.trim() === n.error.trim())) out.push(sec("result" + (n.result.head && n.result.head.length >= 2048 ? " (first 2,048 characters)" : ""), h("pre", { class: "res", text: prettyHead(n.result.head) || "(empty)" }), h("div", { class: "faint mono", style: "font-size:11px;margin-top:3px", text: "sha256 " + n.result.sha256 })));
  out.push(...filesSection(n));
  const kc = childCalls.get(n.id) || [];
  if (kc.length) out.push(sec(`inner calls (${kc.length})`, h("div", { class: "kids" }, kc.map(callBox))));
  if (n.parent && byId.has(n.parent)) out.push(h("div", { class: "actions" }, "launched by ", h("a", { href: "#" + n.parent, text: byId.get(n.parent).tool + " " + n.parent })));
  out.push(actions(n));
  return out;
}
function actions(n) {
  return h("div", { class: "actions" },
    h("a", { onclick: () => openProv(n.id), text: "Trace provenance" }),
    h("a", { onclick: () => copyLink(n.id), text: "Copy link" }),
    h("span", { class: "faint", text: fmtAbs(n._t0) }));
}
function copyLink(id) {
  const url = location.href.split("#")[0] + "#" + id;
  try { navigator.clipboard.writeText(url); } catch (_) { }
  history.replaceState(null, "", "#" + id);
}
function fileEdges(n) { return (outE.get(n.id) || []).filter(e => (e.kind === "used" || e.kind === "generated") && byId.has(e.to)); }
function filesSection(n) {
  const fe = fileEdges(n);
  const outs = fe.filter(e => e.kind === "generated"), ins = fe.filter(e => e.kind === "used");
  const res = [];
  const li = e => { const f = byId.get(e.to); const p = (f.paths || [])[0] || f.id; return h("li", null, h("span", { class: "io" + (e.kind === "generated" ? " out" : ""), text: e.kind === "generated" ? "out" : "in" }), h("a", { class: "p", title: (f.paths || []).join("\n"), onclick: () => openProv(f.id), text: midPath(p) }), h("span", { class: "h", text: sha(f.sha256) }), h("span", { class: "s" }, fmtBytes(f.bytes), e.inferred ? h("span", { class: "tag inf", style: "margin-left:6px", title: "inferred: " + (e.basis || ""), text: "inferred" }) : null)); };
  if (outs.length) res.push(sec(`files written (${outs.length})`, h("ul", { class: "files" }, outs.slice(0, 200).map(li))));
  if (ins.length) res.push(sec(`files read (${ins.length})`, h("ul", { class: "files" }, ins.slice(0, 200).map(li))));
  const other = (rowsByGen.get(n.id) || []).filter(r => !r.node);
  if (other.length) res.push(sec(`other files written, inferred (${other.length.toLocaleString()})`, h("ul", { class: "files" }, other.slice(0, 12).map(r => h("li", null, h("span", { class: "io out", text: "out" }), h("a", { class: "p", title: r.path, onclick: () => openFile(r), text: midPath(r.path) }), h("span", { class: "h", text: sha(r.sha) }), h("span", { class: "s", text: fmtBytes(r.bytes) })))),
    other.length > 12 ? h("a", { class: "small", onclick: () => { setTab("files"); $("f-q").value = n.id; renderFiles(); }, text: `All ${other.length.toLocaleString()} in Files` }) : null));
  return res;
}
function shellBox(n) {
  const fail = isFail(n);
  const isShell = ["Bash", "shell", "exec_command", "local_shell", "shell_command"].includes(n.tool);
  const title = isShell ? "shell" : n.tool;
  const el = h("div", { class: "tbox shell" + (fail ? " fail" : ""), id: "box-" + n.id, "data-id": n.id });
  const from = agentStart(n.agent);
  const status = n.ok == null ? "no result" : n.exit_code != null ? `exit ${n.exit_code}` : "";
  el.append(
    h("div", { class: "t-title" }, h("span", { class: "chev", text: "▶" }), title, fail ? h("span", { class: "tag bad", text: "failed" }) : null),
    h("div", { class: "t-kit", text: [n.source, status].filter(Boolean).join(" · ") }),
    h("div", { class: "t-meta" }, h("span", { title: fmtAbs(n._t0), text: `${fmtOff(n._t0, from)}  ·  ${fmtClock(n._t0)}` }), n.ts_end && n._t1 > n._t0 ? h("span", { text: fmtDur(n._t1 - n._t0) }) : null,
      n.background ? h("span", { class: "tag", text: "background" }) : null,
      h("span", { class: "id" }, h("a", { href: "#" + n.id, text: n.id }))),
    h("div", { class: "cmdline", text: isShell ? n.command : (n.writes || []).join(", ") || n.command }),
    h("div", { class: "t-body" }));
  const build = open => open ? shellFull(n) : shellSummary(n);
  el.querySelector(".t-body").append(...build(S.open.has(n.id)));
  if (S.open.has(n.id)) el.classList.add("open");
  el.querySelector(".t-title").addEventListener("click", () => toggleBox(el, n, build));
  el.querySelector(".cmdline").addEventListener("click", () => { if (!el.classList.contains("open")) toggleBox(el, n, build); });
  return el;
}
function shellSummary(n) {
  const outs = fileEdges(n).filter(e => e.kind === "generated");
  const other = (rowsByGen.get(n.id) || []).filter(r => !r.node).length;
  const r = [];
  if (outs.length || other) r.push(h("div", { class: "t-row" }, h("span", { class: "k", text: "wrote" }), h("span", { class: "v", text: outs.slice(0, 3).map(e => base((byId.get(e.to).paths || [])[0])).join(", ") + (outs.length > 3 || other ? `  +${outs.length - Math.min(3, outs.length) + other} more` : "") + (outs.some(e => e.inferred) ? "  (inferred)" : "") })));
  return r;
}
function shellFull(n) {
  const out = [];
  if (n.description) out.push(h("div", { class: "muted small", text: n.description }));
  if (n.script) out.push(sec("script", h("ul", { class: "files" }, h("li", null, h("span", { class: "io", text: "run" }), h("a", { class: "p", onclick: () => { const r = rowByPath.get(n.script.path); r && openFile(r); }, text: n.script.path }), h("span", { class: "h", text: sha(n.script.sha256) }), h("span", { class: "s" }, fmtBytes(n.script.bytes), n.script.changed_since ? h("span", { class: "tag", style: "margin-left:6px", title: "hashed at export; the script changed after this command", text: "changed since" }) : null)))));
  if (n.output) out.push(sec("output" + (n.output.head && n.output.head.length >= 2048 ? " (first 2,048 characters)" : ""), h("pre", { class: "res", text: n.output.head || "(empty)" })));
  out.push(...filesSection(n));
  if (n.writes_unmatched) out.push(sec("wrote, since rewritten", h("ul", { class: "files" }, n.writes_unmatched.map(p => h("li", null, h("span", { class: "io out", text: "out" }), h("span", { class: "p", text: p }), h("span"), h("span"))))));
  out.push(h("div", { class: "actions" }, h("a", { onclick: () => openProv(n.id), text: "Trace provenance" }), h("a", { onclick: () => copyLink(n.id), text: "Copy link" }),
    h("span", { class: "faint", text: [n.cwd ? "cwd " + n.cwd : null, n.sources ? "seen by " + n.sources.join(", ") : null, n.attributed_by ? "agent by " + n.attributed_by : null].filter(Boolean).join(" · ") })));
  return out;
}

// messages
function richText(str) {
  const el = h("div", { class: "m-body" });
  const parts = String(str).split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g);
  for (const p of parts) {
    if (!p) continue;
    if (p.startsWith("`") && p.endsWith("`") && p.length > 2) el.append(h("code", { text: p.slice(1, -1) }));
    else if (p.startsWith("**") && p.endsWith("**") && p.length > 4) el.append(h("b", { text: p.slice(2, -2) }));
    else el.append(p);
  }
  return el;
}
function bodyText(m) {
  if (m.event === "spawn") return m.initial_message || "";
  const b = m.body;
  if (b && typeof b === "object") return Object.entries(b).map(([k, v]) => `${k}: ${valStr(v)}`).join("\n");
  return txt(b);
}
function messageCard(m, aid, isBrief) {
  const spawn = m.event === "spawn";
  const sys = m.from === "@system";
  const dir = isBrief ? "brief" : m.from === aid ? "out" : "in";
  const el = h("div", { class: "mcard " + dir + (sys ? " sys" : ""), id: "box-" + m.id, "data-id": m.id });
  const from = agentStart(aid);
  const kind = isBrief ? (m.initial_message ? "Brief" : "Role") : spawn ? "Spawned" : sys ? "Notice" : dir === "out" ? "Sent" : "Received";
  el.append(h("div", { class: "m-head" }, h("span", { class: "m-kind", text: kind }),
    isBrief ? (m.from ? h("span", null, "from ", h("b", { text: label(m.from) })) : h("span", { text: "set by the harness" })) :
      spawn ? h("span", null, h("a", { href: "#" + m.to, text: label(m.to) }), m.model ? " · " + shortModel(m.model) : "") :
        dir === "out" ? h("span", null, "to ", h("a", { href: "#" + m.to }, h("b", { text: label(m.to) }))) : h("span", null, "from ", h("a", { href: "#" + m.from }, h("b", { text: label(m.from) }))),
    h("span", { class: "when", title: fmtAbs(m._t0), text: fmtOff(m._t0, from) })));
  const text = bodyText(m);
  if (text) {
    const body = richText(text);
    const long = text.length > 600 || text.split("\n").length > 7;
    el.append(body);
    if (long) {
      body.classList.add("clip");
      const more = h("a", { class: "m-more", text: "Show all" });
      more.onclick = () => { const c = body.classList.toggle("clip"); more.textContent = c ? "Show all" : "Show less"; };
      el.append(more);
    }
  }
  if (spawn || isBrief) {
    if (m.role_prompt) {
      const rp = h("a", { class: "m-more", style: "margin-left:14px", text: "Role prompt" });
      let shown = null;
      rp.onclick = () => { if (shown) { shown.remove(); shown = null; return; } shown = richText(m.role_prompt); shown.style.cssText = "margin-top:8px;padding-top:8px;border-top:1px solid var(--rule);font-size:12.5px;color:var(--ink-2)"; el.append(shown); };
      el.append(rp);
    }
    if (m.toolkits) el.append(h("div", { class: "muted small", style: "margin-top:4px", text: "Toolkits: " + toolkitText(m.toolkits) }));
  }
  return el;
}
function toolkitText(t) {
  if (typeof t === "string") return t;
  if (t && typeof t === "object") return Object.entries(t).map(([k, v]) => v && v.bundles ? `${k} (${v.bundles.join(", ")})` : k).join("; ");
  return "";
}

// sequence controls
function wireSeq() {
  let tmr = 0;
  $("s-q").addEventListener("input", e => { clearTimeout(tmr); tmr = setTimeout(() => { S.q = e.target.value.trim().toLowerCase(); renderSeq(); }, 150); });
  for (const b of $("s-kinds").querySelectorAll("button")) b.addEventListener("click", () => { b.classList.toggle("on"); b.classList.contains("on") ? S.kinds.add(b.dataset.k) : S.kinds.delete(b.dataset.k); renderSeq(); });
  $("s-tool").addEventListener("change", e => { S.tool = e.target.value; renderSeq(); });
  $("s-failed").addEventListener("change", e => { S.failed = e.target.checked; renderSeq(); });
  $("s-expand").addEventListener("click", () => {
    const opening = $("s-expand").textContent === "Expand all";
    for (const n of SEQ.items) if (n.kind !== "message") opening ? S.open.add(n.id) : S.open.delete(n.id);
    $("s-expand").textContent = opening ? "Collapse all" : "Expand all";
    const top = $("seq-scroll").scrollTop; renderSeq(); $("seq-scroll").scrollTop = top;
  });
}

function focusItem(id) {
  const n = byId.get(id); if (!n) return;
  let aid = n.kind === "message" ? (byId.has(n.to) && n.event !== "spawn" ? n.to : n.from) : n.agent;
  let target = id;
  if (n.kind === "call" && n.parent && byId.has(n.parent)) { target = n.parent; S.open.add(n.parent); }
  if (n.kind === "message" && n.event === "spawn") { aid = n.to; target = null; }
  if (!aid || !byId.has(aid)) return;
  S.kinds = new Set(["call", "command", "message"]); for (const b of $("s-kinds").querySelectorAll("button")) b.classList.add("on");
  if (n.kind !== "message") S.open.add(id);
  openAgent(aid, { target, noHash: true, reset: true });
  requestAnimationFrame(() => {
    const el = document.getElementById("box-" + id) || (target && document.getElementById("box-" + target)) || document.querySelector("#seq .mcard.brief");
    if (!el) return;
    el.scrollIntoView({ block: "center" });
    el.classList.add("hl"); setTimeout(() => el.classList.remove("hl"), 2400);
  });
}

// ── provenance ─────────────────────────────────────────────────────────
const P = { pz: null };
function upstream(id) {
  const n = byId.get(id); if (!n) return [];
  if (n.kind === "file") return (inE.get(id) || []).filter(e => e.kind === "generated").map(e => [e.from, e]);
  if (n.kind === "call" || n.kind === "command") return (outE.get(id) || []).filter(e => e.kind === "used" || e.kind === "child_of").map(e => [e.to, e]);
  if (n.kind === "message") return (inE.get(id) || []).filter(e => e.kind === "sent").map(e => [e.from, e]);
  return (inE.get(id) || []).filter(e => e.kind === "spawned").map(e => [e.from, e]);
}
function downstream(id) {
  const n = byId.get(id); if (!n) return [];
  if (n.kind === "file") return (inE.get(id) || []).filter(e => e.kind === "used").map(e => [e.from, e]);
  if (n.kind === "call" || n.kind === "command") return (outE.get(id) || []).filter(e => e.kind === "generated").map(e => [e.to, e]).concat((inE.get(id) || []).filter(e => e.kind === "child_of").map(e => [e.from, e]));
  if (n.kind === "message") return (outE.get(id) || []).filter(e => e.kind === "received").map(e => [e.to, e]);
  return (outE.get(id) || []).filter(e => e.kind === "spawned").map(e => [e.to, e]);
}
function openProv(id) { S.provFocus = id; setTab("prov", true); setHash(id); }
const PW = 250, PH = 58, PGX = 70, PGY = 10, CAP = 40;
function renderProv() {
  const cards = $("p-cards"), svg = $("p-edges");
  cards.textContent = ""; svg.textContent = "";
  const f = S.provFocus && byId.get(S.provFocus);
  if (!f) {
    $("p-info").textContent = "";
    cards.append(h("div", { class: "empty", style: "position:absolute;left:0;top:0;width:520px", text: "Choose a file, call or command (Trace provenance in a box, or a file in the Files tab) to see what it came from and what used it." }));
    $("p-side").replaceChildren(h("div", { class: "empty", text: "Nothing selected." }));
    return;
  }
  const up = +$("p-up").value, down = +$("p-down").value;
  const layer = new Map([[f.id, 0]]), edges = [];
  let fr = [f.id];
  for (let d = 1; d <= up && fr.length; d++) { const nx = []; for (const id of fr) for (const [o, e] of upstream(id)) { edges.push(e); if (!layer.has(o)) { layer.set(o, -d); nx.push(o); } } fr = nx; }
  fr = [f.id];
  for (let d = 1; d <= down && fr.length; d++) { const nx = []; for (const id of fr) for (const [o, e] of downstream(id)) { edges.push(e); if (!layer.has(o)) { layer.set(o, d); nx.push(o); } } fr = nx; }
  const cols = new Map(); for (const [id, l] of layer) push(cols, l, byId.get(id));
  const keys = [...cols.keys()].sort((a, b) => a - b);
  const pos = new Map(); let rows = 0, hidden = 0;
  keys.forEach((k, ci) => {
    const list = cols.get(k).sort((a, b) => a.kind.localeCompare(b.kind) || byTime(a, b));
    list.slice(0, CAP).forEach((n, ri) => pos.set(n.id, { x: ci * (PW + PGX), y: 26 + ri * (PH + PGY), n }));
    hidden += Math.max(0, list.length - CAP); rows = Math.max(rows, Math.min(CAP, list.length));
    svg.append(s("text", { class: "p-col", x: ci * (PW + PGX) + 2, y: 12, text: k < 0 ? (k === -1 ? "inputs" : `inputs · ${-k}`) : k === 0 ? "selected" : (k === 1 ? "used by" : `used by · ${k}`) }));
  });
  const w = keys.length * (PW + PGX), hgt = 40 + rows * (PH + PGY);
  svg.setAttribute("width", w); svg.setAttribute("height", hgt);
  const seen = new Set();
  for (const e of edges) {
    const key = e.kind + e.from + e.to; if (seen.has(key)) continue; seen.add(key);
    let a = pos.get(e.from), b = pos.get(e.to); if (!a || !b) continue;
    if (layer.get(e.from) > layer.get(e.to)) [a, b] = [b, a];
    const x1 = a.x + PW, y1 = a.y + PH / 2, x2 = b.x, y2 = b.y + PH / 2;
    svg.append(s("path", { class: "p-edge" + (e.inferred ? " inf" : "") + (e.from === f.id || e.to === f.id ? " hl" : ""), d: `M${x1},${y1} C${x1 + PGX / 2},${y1} ${x2 - PGX / 2},${y2} ${x2},${y2}` }));
  }
  for (const [id, p] of pos) {
    const n = p.n;
    const [kind, t, sub] = provText(n);
    const c = h("div", { class: `pcard ${n.kind}${isFail(n) ? " fail" : ""}${id === f.id ? " sel" : ""}`, style: `left:${p.x}px;top:${p.y}px` }, h("div", { class: "pk", text: kind }), h("div", { class: "pt", text: t, title: t }), h("div", { class: "ps", text: sub, title: sub }));
    c.addEventListener("click", () => { if ($("p-canvas").dataset.dragged) return; S.provFocus = id; setHash(id); renderProv(); });
    cards.append(c);
  }
  $("p-info").textContent = `${layer.size} nodes${hidden ? ` · ${hidden} not shown` : ""} · dashed links are inferred`;
  P.pz.v.w = w; P.pz.v.h = hgt;
  const fp = pos.get(f.id);
  P.pz.v.k = Math.min(1, Math.max(0.5, (($("p-canvas").clientWidth || 800) - 40) / w));
  if (w * P.pz.v.k < $("p-canvas").clientWidth) P.pz.fit(1); else P.pz.center(fp.x + PW / 2, fp.y + PH / 2 + 120);
  renderSide($("p-side"), f);
}
function provText(n) {
  const ag = n.agent ? label(n.agent) : "";
  if (n.kind === "call") return [`tool call · ${n.toolkit}`, n.tool, `${ag} · ${fmtOff(n._t0)}${isFail(n) ? " · failed" : ""}`];
  if (n.kind === "command") return [n.tool === "Bash" || n.tool === "exec_command" || n.tool === "shell" ? "shell" : n.tool, oneLine(cmdText(n.command)), `${ag} · ${fmtOff(n._t0)}${isFail(n) ? " · failed" : ""}`];
  if (n.kind === "file") { const p = (n.paths || [])[0] || n.id; const d = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : ""; return ["file", base(p), `${d ? midPath(d, 34) + " · " : ""}${fmtBytes(n.bytes)} · ${sha(n.sha256, 7)}`]; }
  if (n.kind === "agent") return ["agent", n.label || n.id, shortModel(n.model) || ""];
  return [n.event === "spawn" ? "spawn" : "message", `${label(n.from)} → ${label(n.to)}`, clip(oneLine(bodyText(n)), 80)];
}
function renderSide(side, n) {
  side.textContent = "";
  if (n.kind === "call" || n.kind === "command") {
    S.open.add(n.id);
    side.append(h("h4", { style: "margin-top:0", text: label(n.agent) }), n.kind === "call" ? callBox(n) : shellBox(n), h("a", { class: "small", onclick: () => focusItem(n.id), text: "Show in the agent's sequence" }));
    return;
  }
  if (n.kind === "file") return fileSide(side, n, null);
  if (n.kind === "message") { side.append(messageCard(n, n.to, false), h("a", { class: "small", onclick: () => focusItem(n.id), text: "Show in sequence" })); return; }
  side.append(h("h2", { text: n.label || n.id }), h("div", { class: "sub", text: [shortModel(n.model), n.engine, n.id].filter(Boolean).join(" · ") }), h("a", { onclick: () => openAgent(n.id), text: "Open this agent" }));
}
function fileSide(side, n, row) {
  const rows = n ? (rowsByNode.get(n.id) || []) : [row];
  const r0 = row || rows.find(r => r.present) || rows[0];
  const path = n ? (n.paths || [])[0] : row.path;
  const shaFull = n ? n.sha256 : row.sha;
  side.append(h("div", { class: "eyebrow", style: "color:var(--muted)", text: "file" }), h("h2", { text: base(path) }), h("div", { class: "sub mono", text: n && n.paths && n.paths.length > 1 ? n.paths.join("\n") : path }));
  const copyPath = r0 && r0.copied ? "files/" + r0.path : null;
  const origin = n ? n.origin : row.origin;
  side.append(h("dl", { class: "kv" },
    h("dt", { text: "sha256" }), h("dd", { class: "mono", style: "font-size:11.5px", text: shaFull || "not hashed" }),
    h("dt", { text: "size" }), h("dd", { text: fmtBytes(n ? n.bytes : row.bytes) }),
    h("dt", { text: "modified" }), h("dd", { text: (n ? n.mtime : row.mtime) || "—" }),
    h("dt", { text: "origin" }), h("dd", { class: "o-" + origin, text: origin + (r0 && r0.basis ? ` (${r0.basis})` : "") + (r0 && r0.before ? ", present before the trace began" : "") }),
    h("dt", { text: "in sandbox" }), h("dd", { text: (n ? n.present : row.present) ? "yes, this version" : "no, not at export" }),
    h("dt", { text: "copy" }), h("dd", null, copyPath ? h("a", { href: encodeURI(copyPath), target: "_blank", text: copyPath }) : h("span", { class: "muted", text: (n ? n.present : row.present) ? "not copied (only files named by a call or command, up to the size limit, are copied)" : "not copied (not in the sandbox at export)" }))));
  const gens = n ? (inE.get(n.id) || []).filter(e => e.kind === "generated").map(e => [e.from, e]) : (row.gen ? [[row.gen, { inferred: true, basis: row.basis }]] : []);
  side.append(h("h4", { text: "generated by" }), gens.length ? h("ul", { class: "linklist" }, gens.map(([id, e]) => actLi(id, e))) : h("div", { class: "muted small", text: "Nothing in the trace." }));
  const users = n ? (inE.get(n.id) || []).filter(e => e.kind === "used") : [];
  if (users.length) side.append(h("h4", { text: `used by (${users.length})` }), h("ul", { class: "linklist" }, users.slice(0, 200).map(e => actLi(e.from, e))));
  if (n) side.append(h("div", { style: "margin-top:14px" }, h("a", { class: "small", onclick: () => openProv(n.id), text: "Trace provenance" })));
}
function actLi(id, e) {
  const a = byId.get(id); if (!a) return h("li", { text: id });
  return h("li", null, h("a", { href: "#" + id, text: a.kind === "call" ? a.tool : clip(oneLine(cmdText(a.command)), 90) }), h("span", { class: "muted", text: `  ${label(a.agent)} · ${fmtOff(a._t0)}` }), e && e.inferred ? h("span", { class: "tag inf", style: "margin-left:6px", text: "inferred" }) : null);
}

// ── timeline ───────────────────────────────────────────────────────────
const TL = { view: [T0 - (T1 - T0) * 0.01, T1 + (T1 - T0) * 0.01], kinds: new Set(["call", "command", "message"]), failed: false, w: 800 };
const LBL = 170, LH = 44, AX = 30;
function tx(t) { return LBL + (t - TL.view[0]) / (TL.view[1] - TL.view[0]) * (TL.w - LBL - 16); }
function renderTimeline() {
  const svg = $("tl"); TL.w = Math.max(500, $("tl-scroll").clientWidth);
  const lanes = order, idx = new Map(lanes.map((a, i) => [a.id, i]));
  const H = AX + lanes.length * LH + 12;
  svg.setAttribute("width", TL.w); svg.setAttribute("height", H); svg.textContent = "";
  lanes.forEach((a, i) => svg.append(s("rect", { class: "lane" + (i % 2 ? " alt" : ""), x: 0, y: AX + i * LH, width: TL.w, height: LH })));
  const ax = s("g", { class: "ax" });
  const span = TL.view[1] - TL.view[0], target = span / Math.max(2, (TL.w - LBL) / 120);
  const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200].find(x => x >= target) || 14400;
  for (let t = Math.ceil((TL.view[0] - T0) / step) * step + T0; t <= TL.view[1]; t += step) { const x = tx(t); if (x < LBL) continue; ax.append(s("line", { x1: x, x2: x, y1: AX - 6, y2: H }), s("text", { x: x + 4, y: 17, text: fmtOff(t) })); }
  svg.append(ax);
  lanes.forEach((a, i) => {
    const y = AX + i * LH;
    if (a._t0 != null && !a.external) { const x0 = Math.max(LBL, tx(a._t0)), x1 = Math.min(TL.w, tx(a.ts_end || a.terminated ? a._t1 : T1)); if (x1 > x0) svg.append(s("rect", { class: "alive", x: x0, y: y + 6, width: x1 - x0, height: LH - 12, rx: 4 })); }
    const g = s("g", { "data-agent": a.id });
    g.append(s("rect", { class: "lanehit", x: 0, y, width: LBL - 6, height: LH }), s("text", { class: "lname", x: 14 + depth.get(a.id) * 10, y: y + 19, text: clip(a.label || a.id, 20) }), s("text", { class: "lsub", x: 14 + depth.get(a.id) * 10, y: y + 33, text: clip(shortModel(a.model) || (a.external ? "human" : ""), 24) }));
    svg.append(g);
  });
  const marks = s("g");
  for (const n of acts) {
    if (!TL.kinds.has(n.kind) || (TL.failed && !isFail(n)) || !idx.has(n.agent) || n._t0 == null || n._t1 < TL.view[0] || n._t0 > TL.view[1]) continue;
    const y = AX + idx.get(n.agent) * LH, call = n.kind === "call";
    const x0 = Math.max(LBL, tx(n._t0)), x1 = Math.min(TL.w, tx(n._t1));
    marks.append(s("rect", { class: (call ? "m-call" : "m-cmd") + (isFail(n) ? " m-bad" : ""), x: x0, y: y + (call ? 11 : 25), width: Math.max(2, x1 - x0), height: call ? 11 : 7, rx: 1.5, "data-id": n.id }));
  }
  svg.append(marks);
  if (TL.kinds.has("message")) {
    for (const m of msgs) {
      if (m._t0 == null || m._t0 < TL.view[0] || m._t0 > TL.view[1]) continue;
      const a = idx.get(m.from), b = idx.get(m.to); if (a == null || b == null || a === b) continue;
      const x = tx(m._t0), y1 = AX + a * LH + LH / 2, y2 = AX + b * LH + LH / 2;
      svg.append(s("path", { class: "m-msg", d: `M${x},${y1} C${x + 14},${y1} ${x + 14},${y2} ${x},${y2}` }), s("circle", { class: "m-dot", cx: x, cy: y2, r: 3, "data-id": m.id }));
    }
  }
}
function wireTimeline() {
  const svg = $("tl");
  let drag = null;
  svg.addEventListener("mousedown", e => { drag = { x: e.clientX, v: TL.view.slice(), moved: false }; });
  addEventListener("mousemove", e => { if (!drag) return; const dx = e.clientX - drag.x; if (Math.abs(dx) > 3) drag.moved = true; const dt = dx / (TL.w - LBL - 16) * (drag.v[1] - drag.v[0]); TL.view = [drag.v[0] - dt, drag.v[1] - dt]; schedule(renderTimeline); });
  addEventListener("mouseup", () => setTimeout(() => { drag = null; }, 0));
  svg.addEventListener("click", e => {
    if (drag && drag.moved) return;
    const id = e.target.getAttribute && e.target.getAttribute("data-id"); if (id) { location.hash = "#" + id; return; }
    const ag = e.target.closest && e.target.closest("[data-agent]"); if (ag) location.hash = "#" + ag.getAttribute("data-agent");
  });
  svg.addEventListener("mousemove", e => { const id = e.target.getAttribute && e.target.getAttribute("data-id"); if (!id) return hideTip(); const n = byId.get(id); showTip(e, n.kind === "message" ? `${label(n.from)} → ${label(n.to)}\n${clip(oneLine(bodyText(n)), 200)}` : `${n.kind === "call" ? n.toolkit + " · " + n.tool : "shell"}${isFail(n) ? " · failed" : ""}\n${clip(n.kind === "call" ? callArgsLine(n) : oneLine(n.command), 200)}\n${label(n.agent)} · ${fmtOff(n._t0)} · ${fmtDur(n._t1 - n._t0)}`); });
  svg.addEventListener("mouseleave", hideTip);
  svg.addEventListener("wheel", e => { e.preventDefault(); const r = svg.getBoundingClientRect(), x = e.clientX - r.left; const t = x > LBL ? TL.view[0] + (x - LBL) / (TL.w - LBL - 16) * (TL.view[1] - TL.view[0]) : (TL.view[0] + TL.view[1]) / 2; zoomT(t, Math.exp(e.deltaY * 0.0015)); }, { passive: false });
  $("t-fit").onclick = () => { TL.view = [T0 - (T1 - T0) * 0.01, T1 + (T1 - T0) * 0.01]; renderTimeline(); };
  $("t-in").onclick = () => zoomT((TL.view[0] + TL.view[1]) / 2, 0.6);
  $("t-out").onclick = () => zoomT((TL.view[0] + TL.view[1]) / 2, 1 / 0.6);
  for (const b of $("t-kinds").querySelectorAll("button")) b.addEventListener("click", () => { b.classList.toggle("on"); b.classList.contains("on") ? TL.kinds.add(b.dataset.k) : TL.kinds.delete(b.dataset.k); renderTimeline(); });
  $("t-failed").addEventListener("change", e => { TL.failed = e.target.checked; renderTimeline(); });
}
function callArgsLine(n) { return argPairs(n.args).map(([k, v]) => `${k} = ${clip(oneLine(valStr(v)), 50)}`).join("; "); }
function zoomT(t, f) { const span = Math.min((T1 - T0) * 1.5, Math.max(1, (TL.view[1] - TL.view[0]) * f)); const fr = (t - TL.view[0]) / (TL.view[1] - TL.view[0]); TL.view = [t - fr * span, t - fr * span + span]; schedule(renderTimeline); }
let raf = 0, pend = new Set();
function schedule(fn) { pend.add(fn); if (!raf) raf = requestAnimationFrame(() => { raf = 0; const p = [...pend]; pend.clear(); p.forEach(f => f()); }); }

// ── files ──────────────────────────────────────────────────────────────
let fileSort = "path";
function renderFiles() {
  const q = $("f-q").value.trim().toLowerCase(), og = $("f-origin").value, onlyG = $("f-graph").checked;
  let rows = FROWS.filter(r => (!q || r.path.toLowerCase().includes(q) || (r.sha && r.sha.startsWith(q)) || (r.gen && r.gen === q)) && (!onlyG || r.node) && (!og || (og === "absent" ? !r.present : r.present && r.origin === og)));
  if (fileSort === "size") rows = rows.slice().sort((a, b) => (b.bytes || 0) - (a.bytes || 0));
  $("f-info").textContent = `${rows.length.toLocaleString()} of ${FROWS.length.toLocaleString()} files`;
  const cols = "minmax(0,1fr) 76px 84px 92px minmax(0,220px)";
  const box = $("f-scroll"); box.textContent = "";
  const head = h("div", { class: "vhead", style: "grid-template-columns:" + cols }, h("span", { text: "path", onclick: () => { fileSort = "path"; renderFiles(); } }), h("span", { text: "size", onclick: () => { fileSort = "size"; renderFiles(); } }), h("span", { text: "sha256" }), h("span", { text: "origin" }), h("span", { text: "generated by" }));
  const body = h("div", { style: `position:relative;height:${rows.length * 26}px` });
  box.append(head, body);
  let last = "";
  const draw = () => {
    const top = Math.max(0, box.scrollTop - 30), a = Math.max(0, Math.floor(top / 26) - 10), b = Math.min(rows.length, Math.ceil((top + box.clientHeight) / 26) + 10);
    if (last === a + ":" + b) return; last = a + ":" + b; body.textContent = "";
    for (let i = a; i < b; i++) {
      const r = rows[i], g = r.gen && byId.get(r.gen);
      const o = r.present ? r.origin : "absent";
      const el = h("div", { class: "vrow" + (S.fileSel === r.i ? " sel" : ""), style: `top:${i * 26}px;grid-template-columns:${cols}` },
        h("span", { class: "mono", title: r.path, text: r.path }), h("span", { class: "muted", style: "text-align:right", text: fmtBytes(r.bytes) }), h("span", { class: "mono faint", text: sha(r.sha) }),
        h("span", { class: "o-" + o, text: o }), h("span", { class: "muted", text: g ? (g.kind === "call" ? g.tool : clip(oneLine(cmdText(g.command)), 60)) + " · " + label(g.agent) : r.before ? "before the trace" : "" }));
      el.addEventListener("click", () => openFile(r));
      body.append(el);
    }
  };
  box.onscroll = draw; draw();
}
function openFile(r) {
  if (S.tab !== "files") setTab("files", true);
  S.fileSel = r.i;
  if (r.sha) history.replaceState(null, "", "#f-" + r.sha.slice(0, 12));
  const side = $("f-side"); side.textContent = "";
  fileSide(side, r.node ? byId.get(r.node) : null, r.node ? null : r);
  for (const el of document.querySelectorAll("#f-scroll .vrow")) el.classList.remove("sel");
}

// ── tabs, routing ──────────────────────────────────────────────────────
function setTab(t, quiet) {
  S.tab = t;
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === t);
  for (const v of document.querySelectorAll(".view")) v.classList.toggle("on", v.id === "v-" + t);
  if (t === "prov") renderProv();
  else if (t === "timeline") renderTimeline();
  else if (t === "files") { renderFiles(); if (S.fileSel == null) $("f-side").replaceChildren(h("div", { class: "empty", text: "Select a file to see its hash, origin and the calls that wrote and read it." })); }
  else if (t === "agents" && !S.agent) requestAnimationFrame(() => G.pz.fit(1));
  if (!quiet && t !== "agents") setHash("");
}
let squelch = false;
function setHash(id) { squelch = true; history.replaceState(null, "", id ? "#" + id : location.pathname + location.search); setTimeout(() => { squelch = false; }, 0); }
function resolveAnchor(id) {
  if (byId.has(id)) return { id };
  if (/^f-[0-9a-f]{4,}$/i.test(id)) {
    const p = id.slice(2).toLowerCase();
    for (const n of NODES) if (n.kind === "file" && n.sha256 && n.sha256.startsWith(p)) return { id: n.id };
    const r = FROWS.find(r => r.sha && r.sha.startsWith(p)); if (r) return { row: r };
  }
  if (/^(c|cmd)-/.test(id)) for (const n of acts) if (n.id.startsWith(id)) return { id: n.id };
  if (/^m-/.test(id)) { const k = "m-" + id.slice(2).replace(/^msg-/, ""); if (byId.has(k)) return { id: k }; }
  return null;
}
function route() {
  if (squelch) return;
  const id = decodeURIComponent(location.hash.slice(1));
  if (!id) return;
  const r = resolveAnchor(id);
  if (!r) { setTab("agents", true); closeAgent(); toast(`No ${id} in this trace.`); return; }
  if (r.row) { openFile(r.row); return; }
  const n = byId.get(r.id);
  if (n.kind === "agent") { if (n.id === "@system") return; openAgent(n.id, { noHash: true }); }
  else if (n.kind === "file") { S.provFocus = n.id; setTab("prov", true); }
  else focusItem(n.id);
}
function toast(msg) {
  const t = h("div", { class: "popover", style: "top:auto;bottom:24px;right:24px;width:auto", text: msg });
  t.id = "toast"; document.body.append(t); setTimeout(() => t.remove(), 3000);
}
addEventListener("hashchange", route);

// ── boot ───────────────────────────────────────────────────────────────
header();
G.pz = panzoom($("graph-canvas"), $("graph-world"), zoomLabel);
P.pz = panzoom($("p-canvas"), $("p-world"));
renderGraph();
for (const id of ["e-tree", "e-msg"]) $(id).addEventListener("change", renderGraph);
$("z-in").onclick = () => G.pz.zoom(1.2); $("z-out").onclick = () => G.pz.zoom(1 / 1.2); $("z-fit").onclick = () => G.pz.fit(1.1);
wireSeq(); wireTimeline();
for (const id of ["p-up", "p-down"]) $(id).addEventListener("change", renderProv);
$("p-fit").onclick = () => P.pz.fit(1);
for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => setTab(b.dataset.tab));
let ft = 0; $("f-q").addEventListener("input", () => { clearTimeout(ft); ft = setTimeout(renderFiles, 120); });
for (const id of ["f-origin", "f-graph"]) $(id).addEventListener("change", renderFiles);
addEventListener("resize", () => schedule(() => { if (S.tab === "timeline") renderTimeline(); }));
$("v-agents").classList.add("solo");
requestAnimationFrame(() => G.pz.fit(1));
if (location.hash.length > 1) route();
window.__trace = { nodes: NODES.length, edges: EDGES.length, files: FROWS.length, S, openAgent, focusItem, setTab, openProv };
})();

(function () {
"use strict";

// ── data ──────────────────────────────────────────────────────────────
const D = JSON.parse(document.getElementById("trace-data").textContent);
const META = D.meta || {};
const NODES = D.nodes || [];
const EDGES = D.edges || [];
const FILES = D.files || { dirs: [], rows: [] };
const byId = new Map();
const outE = new Map(), inE = new Map();
for (const n of NODES) {
  byId.set(n.id, n);
  n._t0 = tparse(n.ts_start || n.ts);
  n._t1 = tparse(n.ts_end) ?? n._t0;
}
for (const e of EDGES) {
  if (!outE.has(e.from)) outE.set(e.from, []);
  if (!inE.has(e.to)) inE.set(e.to, []);
  outE.get(e.from).push(e); inE.get(e.to).push(e);
}
const agents = NODES.filter(n => n.kind === "agent");
const acts = NODES.filter(n => n.kind === "call" || n.kind === "command").sort((a, b) => (a._t0 ?? 0) - (b._t0 ?? 0));
const msgs = NODES.filter(n => n.kind === "message").sort((a, b) => (a._t0 ?? 0) - (b._t0 ?? 0));
// file rows: [dir, name, bytes, sha256, mtime, origin, generated_by, basis, node, copied, present, before_trace]
const FROWS = FILES.rows.map((r, i) => ({ i, path: (FILES.dirs[r[0]] ? FILES.dirs[r[0]] + "/" : "") + r[1], bytes: r[2], sha: r[3], mtime: r[4], origin: r[5], gen: r[6], basis: r[7], node: r[8], copied: r[9], present: !!r[10], before: !!r[11] }));
const rowsByNode = new Map();
for (const r of FROWS) if (r.node) { if (!rowsByNode.has(r.node)) rowsByNode.set(r.node, []); rowsByNode.get(r.node).push(r); }

let T0 = tparse(META.start), T1 = tparse(META.end);
for (const n of NODES) { if (n._t0 != null) { T0 = T0 == null ? n._t0 : Math.min(T0, n._t0); T1 = T1 == null ? n._t1 : Math.max(T1, n._t1); } }
if (T0 == null) { T0 = 0; T1 = 1; }
if (T1 <= T0) T1 = T0 + 1;

// agent order: spawn tree, depth first, children by start time
const kids = new Map();
for (const a of agents) { const p = a.parent && byId.has(a.parent) ? a.parent : null; if (!kids.has(p)) kids.set(p, []); kids.get(p).push(a); }
for (const l of kids.values()) l.sort((x, y) => (x.external ? -1 : 0) - (y.external ? -1 : 0) || (x._t0 ?? 1e18) - (y._t0 ?? 1e18));
const order = []; const depth = new Map();
(function walk(p, d) { for (const a of kids.get(p) || []) { if (depth.has(a.id)) continue; depth.set(a.id, d); order.push(a); walk(a.id, d + 1); } })(null, 0);
for (const a of agents) if (!depth.has(a.id)) { depth.set(a.id, 0); order.push(a); }
const PALETTE = ["#3b73d9", "#d9822b", "#2f9e6a", "#c2417d", "#7a5cd1", "#1f9bb4", "#a3803a", "#d14b4b", "#5b8a2f", "#8a6a5c"];
const agentColor = new Map(order.map((a, i) => [a.id, PALETTE[i % PALETTE.length]]));
const toolkits = [...new Set(acts.filter(n => n.kind === "call").map(n => n.toolkit || "?"))].sort();
const tkColor = new Map(toolkits.map((t, i) => [t, PALETTE[(i * 3 + 1) % PALETTE.length]]));
const tools = new Map();
for (const n of acts) { const k = toolKey(n); tools.set(k, (tools.get(k) || 0) + 1); }

// ── helpers ───────────────────────────────────────────────────────────
function tparse(s) { if (s == null) return null; if (typeof s === "number") return s; const v = Date.parse(s); return isNaN(v) ? null : v / 1000; }
function toolKey(n) { return n.kind === "call" ? (n.toolkit || "?") + "." + n.tool : "shell." + (n.tool || "cmd"); }
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v; else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) { if (c == null || c === false) continue; el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c); }
  return el;
}
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) { if (v == null) continue; if (k === "text") el.textContent = v; else el.setAttribute(k, v); }
  for (const c of kids.flat()) if (c) el.appendChild(c);
  return el;
}
function fmtBytes(b) { if (b == null) return "?"; const u = ["B", "kB", "MB", "GB", "TB"]; let i = 0, v = b; while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; } return (i ? v.toFixed(v < 10 ? 1 : 0) : v) + " " + u[i]; }
function fmtDur(sec) { if (sec == null || isNaN(sec)) return ""; if (sec < 1) return Math.round(sec * 1000) + " ms"; if (sec < 60) return sec.toFixed(sec < 10 ? 1 : 0) + " s"; const m = Math.floor(sec / 60), r = Math.round(sec % 60); if (m < 60) return m + "m" + String(r).padStart(2, "0") + "s"; return Math.floor(m / 60) + "h" + String(m % 60).padStart(2, "0") + "m"; }
function fmtOff(t) { if (t == null) return ""; const d = Math.max(0, t - T0); const hh = Math.floor(d / 3600), mm = Math.floor(d % 3600 / 60), ss = Math.floor(d % 60); return "+" + (hh ? hh + ":" + String(mm).padStart(2, "0") : mm) + ":" + String(ss).padStart(2, "0"); }
function fmtAbs(t) { if (t == null) return ""; return new Date(t * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC"); }
function parseOff(str) { str = (str || "").trim().replace(/^\+/, ""); if (!str) return null; const p = str.split(":").map(Number); if (p.some(isNaN)) return null; let s = 0; for (const x of p) s = s * 60 + x; if (p.length === 1) s *= 60; return T0 + s; }
function agentLabel(id) { const a = byId.get(id); return a ? (a.label || id) : (id || "?"); }
function short(str, n) { str = String(str ?? ""); return str.length > n ? str.slice(0, n - 1) + "…" : str; }
function txt(v) { return v == null ? "" : typeof v === "string" ? v : JSON.stringify(v, null, 2); }
function oneLine(str) { return txt(str).replace(/\s+/g, " ").trim(); }
function base(p) { return String(p || "").split("/").pop(); }
function nodeTitle(n) {
  if (!n) return "?";
  switch (n.kind) {
    case "call": return n.tool || n.id;
    case "command": return short(oneLine(n.command), 80);
    case "agent": return n.label || n.id;
    case "file": return base((n.paths || [])[0]) || n.id;
    case "message": return n.event === "spawn" ? "spawn " + agentLabel(n.to) : "message to " + agentLabel(n.to);
  }
  return n.id;
}
function argSummary(n) {
  if (n.kind === "command") return oneLine(n.command);
  const a = n.args || {}; const parts = [];
  for (const [k, v] of Object.entries(a)) { if (v == null) continue; const sv = typeof v === "object" ? JSON.stringify(v) : String(v); parts.push(k + "=" + short(oneLine(sv), 60)); if (parts.length >= 4) break; }
  return parts.join("  ");
}
function isFailed(n) { return n.kind === "call" ? n.ok === false : n.kind === "command" ? n.ok === false : false; }

// ── state and filters ─────────────────────────────────────────────────
const st = {
  tab: "timeline", sel: null, selRow: null, q: "",
  agents: new Set(agents.map(a => a.id)), toolkits: new Set(toolkits.concat(["shell"])), tools: new Set(tools.keys()),
  calls: true, cmds: true, msgs: true, failed: false, t0: null, t1: null,
  view: [T0 - (T1 - T0) * 0.01, T1 + (T1 - T0) * 0.01],
};
function passes(n) {
  if (n.kind === "call" && !st.calls) return false;
  if (n.kind === "command" && !st.cmds) return false;
  if (n.kind === "message") { if (!st.msgs) return false; if (!(st.agents.has(n.from) || st.agents.has(n.to))) return false; }
  if (n.kind === "call" || n.kind === "command") {
    if (!st.agents.has(n.agent)) return false;
    if (!st.toolkits.has(n.kind === "call" ? (n.toolkit || "?") : "shell")) return false;
    if (!st.tools.has(toolKey(n))) return false;
    if (st.failed && !isFailed(n)) return false;
  }
  if (st.t0 != null && (n._t1 ?? n._t0) != null && (n._t1 ?? n._t0) < st.t0) return false;
  if (st.t1 != null && n._t0 != null && n._t0 > st.t1) return false;
  if (st.q && !matchQ(n, st.q)) return false;
  return true;
}
const qcache = new Map();
function matchQ(n, q) {
  let hay = qcache.get(n.id);
  if (hay === undefined) {
    hay = [n.id, n.tool, n.toolkit, n.command, n.label, txt(n.body), n.error, n.event, (n.paths || []).join(" "), n.sha256,
      n.args ? JSON.stringify(n.args) : "", n.result ? n.result.head : "", n.output ? n.output.head : "", n.initial_message].filter(Boolean).join("\n").toLowerCase();
    qcache.set(n.id, hay);
  }
  return hay.includes(q);
}

// ── filters panel ─────────────────────────────────────────────────────
function buildFilters() {
  const fa = document.getElementById("f-agents"); fa.textContent = "";
  const cnt = new Map(); for (const n of acts) cnt.set(n.agent, (cnt.get(n.agent) || 0) + 1);
  for (const a of order) {
    const cb = h("input", { type: "checkbox", checked: st.agents.has(a.id), onchange: e => { e.target.checked ? st.agents.add(a.id) : st.agents.delete(a.id); refresh(); } });
    fa.appendChild(h("label", { title: a.id, style: "padding-left:" + (depth.get(a.id) * 10) + "px" }, cb, h("span", { class: "sw", style: "background:" + agentColor.get(a.id) }), a.label || a.id, h("span", { class: "n", text: String(cnt.get(a.id) || 0) })));
  }
  const ft = document.getElementById("f-toolkits"); ft.textContent = "";
  const tkc = new Map(); for (const n of acts) { const k = n.kind === "call" ? (n.toolkit || "?") : "shell"; tkc.set(k, (tkc.get(k) || 0) + 1); }
  for (const t of [...toolkits, ...(tkc.has("shell") ? ["shell"] : [])]) {
    const cb = h("input", { type: "checkbox", checked: st.toolkits.has(t), onchange: e => { e.target.checked ? st.toolkits.add(t) : st.toolkits.delete(t); refresh(); } });
    ft.appendChild(h("label", null, cb, h("span", { class: "sw", style: "background:" + (t === "shell" ? "var(--cmd)" : tkColor.get(t)) }), t === "shell" ? "shell (commands)" : t, h("span", { class: "n", text: String(tkc.get(t) || 0) })));
  }
  renderToolFilter();
}
function renderToolFilter() {
  const fl = document.getElementById("f-tools"); fl.textContent = "";
  const q = document.getElementById("f-tools-q").value.toLowerCase();
  const keys = [...tools.keys()].sort((a, b) => tools.get(b) - tools.get(a) || a.localeCompare(b));
  for (const k of keys) {
    if (q && !k.toLowerCase().includes(q)) continue;
    const cb = h("input", { type: "checkbox", checked: st.tools.has(k), onchange: e => { e.target.checked ? st.tools.add(k) : st.tools.delete(k); refresh(); } });
    fl.appendChild(h("label", { title: k }, cb, k.replace(/^[^.]*\./, ""), h("span", { class: "n", text: String(tools.get(k)) })));
  }
}
function wireFilters() {
  const on = (id, ev, fn) => document.getElementById(id).addEventListener(ev, fn);
  on("q", "input", e => { st.q = e.target.value.trim().toLowerCase(); refresh(); });
  on("f-calls", "change", e => { st.calls = e.target.checked; refresh(); });
  on("f-cmds", "change", e => { st.cmds = e.target.checked; refresh(); });
  on("f-msgs", "change", e => { st.msgs = e.target.checked; refresh(); });
  on("f-failed", "change", e => { st.failed = e.target.checked; refresh(); });
  const tchange = () => { st.t0 = parseOff(document.getElementById("f-t0").value); st.t1 = parseOff(document.getElementById("f-t1").value); refresh(); };
  on("f-t0", "change", tchange); on("f-t1", "change", tchange);
  on("f-time-view", "click", () => { document.getElementById("f-t0").value = fmtOff(Math.max(T0, st.view[0])); document.getElementById("f-t1").value = fmtOff(Math.min(T1, st.view[1])); tchange(); });
  on("f-time-clear", "click", () => { document.getElementById("f-t0").value = ""; document.getElementById("f-t1").value = ""; tchange(); });
  on("f-agents-all", "click", () => { st.agents = new Set(agents.map(a => a.id)); buildFilters(); refresh(); });
  on("f-agents-none", "click", () => { st.agents = new Set(); buildFilters(); refresh(); });
  on("f-tools-all", "click", () => { st.tools = new Set(tools.keys()); renderToolFilter(); refresh(); });
  on("f-tools-none", "click", () => { st.tools = new Set(); renderToolFilter(); refresh(); });
  on("f-tools-q", "input", renderToolFilter);
}

// ── tooltip ───────────────────────────────────────────────────────────
const tip = document.getElementById("tip");
function showTip(ev, text) { tip.textContent = text; tip.style.display = "block"; const x = Math.min(ev.clientX + 14, innerWidth - 440), y = Math.min(ev.clientY + 14, innerHeight - 120); tip.style.left = x + "px"; tip.style.top = y + "px"; }
function hideTip() { tip.style.display = "none"; }
function tipText(n) {
  if (!n) return "";
  const bits = [];
  if (n.kind === "call") bits.push(`${n.toolkit}.${n.tool}${n.version ? " @" + n.version : ""}${n.ok === false ? "  FAILED" : ""}`, argSummary(n));
  else if (n.kind === "command") bits.push(`${n.tool} (${n.source})${n.ok === false ? "  FAILED" : ""}`, short(n.command, 300));
  else if (n.kind === "message") bits.push(n.event === "spawn" ? `spawn: ${agentLabel(n.from)} → ${agentLabel(n.to)}` : `${agentLabel(n.from)} → ${agentLabel(n.to)}`, short(oneLine(n.event === "spawn" ? n.initial_message : n.body), 300));
  else if (n.kind === "agent") bits.push(`${n.label || n.id}  ${n.model || ""}`);
  bits.push(`${agentLabel(n.agent || n.from)}  ${fmtOff(n._t0)}  ${n._t1 != null && n._t0 != null && n._t1 > n._t0 ? fmtDur(n._t1 - n._t0) : ""}`);
  return bits.filter(Boolean).join("\n");
}

// ── timeline ──────────────────────────────────────────────────────────
const LANE_H = 40, LABEL_W = 150, AXIS_H = 26;
const tl = document.getElementById("tl"), tlScroll = document.getElementById("tl-scroll");
let tlW = 800;
function tx(t) { return LABEL_W + (t - st.view[0]) / (st.view[1] - st.view[0]) * (tlW - LABEL_W - 12); }
function tinv(x) { return st.view[0] + (x - LABEL_W) / (tlW - LABEL_W - 12) * (st.view[1] - st.view[0]); }
function niceStep(span, px) {
  const target = span / Math.max(2, px / 110);
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
  for (const x of steps) if (x >= target) return x; return 86400 * Math.ceil(target / 86400);
}
let relSet = new Set();
function renderTimeline() {
  tlW = Math.max(400, tlScroll.clientWidth);
  const lanes = order.filter(a => st.agents.has(a.id));
  const laneOf = new Map(lanes.map((a, i) => [a.id, i]));
  const H = AXIS_H + lanes.length * LANE_H + 10;
  tl.setAttribute("width", tlW); tl.setAttribute("height", H);
  tl.textContent = "";
  const g = s("g");
  // axis
  const ax = s("g", { class: "axis" });
  const span = st.view[1] - st.view[0], step = niceStep(span, tlW - LABEL_W);
  const first = Math.ceil((st.view[0] - T0) / step) * step + T0;
  for (let t = first; t <= st.view[1]; t += step) {
    const x = tx(t); if (x < LABEL_W) continue;
    ax.appendChild(s("line", { x1: x, x2: x, y1: AXIS_H - 6, y2: H }));
    ax.appendChild(s("text", { x: x + 3, y: 14, class: "muted", text: fmtOff(t) }));
  }
  g.appendChild(ax);
  // lanes
  lanes.forEach((a, i) => {
    const y = AXIS_H + i * LANE_H;
    g.appendChild(s("rect", { x: 0, y, width: tlW, height: LANE_H, class: "lane" + (i % 2 ? " alt" : "") }));
    if (a._t0 != null) {
      const x0 = Math.max(LABEL_W, tx(a._t0)), x1 = Math.min(tlW, tx(a.ts_end ? a._t1 : T1));
      if (x1 > x0) g.appendChild(s("rect", { x: x0, y: y + 4, width: x1 - x0, height: LANE_H - 8, rx: 3, class: "alive" }));
    }
    if (a.terminated && a._t1 != null) { const x = tx(a._t1); if (x > LABEL_W) g.appendChild(s("line", { x1: x, x2: x, y1: y + 6, y2: y + LANE_H - 6, class: "term" })); }
    const lab = s("g", { class: "lanelabel", "data-id": a.id });
    lab.appendChild(s("rect", { x: 0, y, width: LABEL_W - 4, height: LANE_H, fill: "transparent" }));
    lab.appendChild(s("rect", { x: 6 + depth.get(a.id) * 8, y: y + 12, width: 4, height: 16, rx: 2, fill: agentColor.get(a.id) }));
    lab.appendChild(s("text", { x: 14 + depth.get(a.id) * 8, y: y + 18, text: short(a.label || a.id, 18 - depth.get(a.id)), "font-weight": "600" }));
    lab.appendChild(s("text", { x: 14 + depth.get(a.id) * 8, y: y + 31, class: "muted", "font-size": "10", text: short(a.model || a.engine || a.id, 22 - depth.get(a.id)) }));
    g.appendChild(lab);
  });
  const sel = st.sel;
  // marks
  const marks = s("g");
  for (const n of acts) {
    if (!laneOf.has(n.agent) || n._t0 == null) continue;
    if (n._t1 < st.view[0] || n._t0 > st.view[1]) continue;
    const ok = passes(n);
    if (!ok && !st.q) continue;
    const y = AXIS_H + laneOf.get(n.agent) * LANE_H;
    const x0 = Math.max(LABEL_W, tx(n._t0)), x1 = Math.min(tlW, tx(n._t1));
    const w = Math.max(2, x1 - x0);
    const isCall = n.kind === "call";
    const cls = "mark" + (isCall ? "" : " cmd") + (isFailed(n) ? " bad" : "") + (ok ? "" : " dim") + (n.id === sel ? " sel" : relSet.has(n.id) ? " rel" : "");
    marks.appendChild(s("rect", { x: x0, y: y + (isCall ? 7 : 24), width: w, height: isCall ? 14 : 9, rx: 1.5, class: cls, "data-id": n.id, fill: isCall ? tkColor.get(n.toolkit || "?") : null }));
  }
  g.appendChild(marks);
  // spawns and messages
  if (st.msgs) {
    const links = s("g");
    for (const m of msgs) {
      if (m._t0 == null || m._t0 < st.view[0] || m._t0 > st.view[1]) continue;
      if (!passes(m)) continue;
      const a = laneOf.get(m.from), b = laneOf.get(m.to);
      const x = tx(m._t0);
      const spawn = m.event === "spawn";
      if (a != null && b != null && a !== b) {
        const y1 = AXIS_H + a * LANE_H + LANE_H / 2, y2 = AXIS_H + b * LANE_H + LANE_H / 2;
        const bend = Math.min(40, Math.abs(y2 - y1) / 3);
        links.appendChild(s("path", { d: `M${x},${y1} C${x + bend},${y1} ${x + bend},${y2} ${x},${y2}`, class: spawn ? "link-spawn" : "link-msg", "data-id": m.id }));
        links.appendChild(s("circle", { cx: x, cy: y2, r: m.id === sel ? 5 : 3, class: spawn ? "dot-spawn" : "dot-msg", "data-id": m.id }));
      } else if (b != null || a != null) {
        const y = AXIS_H + (b ?? a) * LANE_H + 4;
        links.appendChild(s("path", { d: `M${x - 4},${y} L${x + 4},${y} L${x},${y + 6} Z`, class: spawn ? "dot-spawn" : "dot-msg", "data-id": m.id }));
      }
    }
    g.appendChild(links);
  }
  tl.appendChild(g);
}
function wireTimeline() {
  let drag = null;
  tl.addEventListener("mousedown", e => { drag = { x: e.clientX, v: st.view.slice(), moved: false }; });
  addEventListener("mousemove", e => {
    if (drag) {
      const dx = e.clientX - drag.x; if (Math.abs(dx) > 3) drag.moved = true;
      const dt = dx / (tlW - LABEL_W - 12) * (drag.v[1] - drag.v[0]);
      st.view = [drag.v[0] - dt, drag.v[1] - dt]; schedule(renderTimeline); return;
    }
  });
  addEventListener("mouseup", () => { setTimeout(() => { drag = null; }, 0); });
  tl.addEventListener("click", e => {
    if (drag && drag.moved) return;
    const id = e.target.closest("[data-id]")?.getAttribute("data-id");
    if (id) select(id);
  });
  tl.addEventListener("mousemove", e => {
    if (drag) return;
    const id = e.target.closest("[data-id]")?.getAttribute("data-id");
    if (id) showTip(e, tipText(byId.get(id))); else hideTip();
  });
  tl.addEventListener("mouseleave", hideTip);
  tl.addEventListener("wheel", e => {
    e.preventDefault();
    const r = tl.getBoundingClientRect(); const x = e.clientX - r.left;
    const t = x > LABEL_W ? tinv(x) : (st.view[0] + st.view[1]) / 2;
    const f = Math.exp((e.deltaY || e.deltaX) * 0.0015);
    zoomAround(t, f);
  }, { passive: false });
  document.getElementById("tl-fit").onclick = () => { st.view = [T0 - (T1 - T0) * 0.01, T1 + (T1 - T0) * 0.01]; renderTimeline(); };
  document.getElementById("tl-in").onclick = () => zoomAround((st.view[0] + st.view[1]) / 2, 0.6);
  document.getElementById("tl-out").onclick = () => zoomAround((st.view[0] + st.view[1]) / 2, 1 / 0.6);
}
function zoomAround(t, f) {
  const span = Math.min((T1 - T0) * 1.5, Math.max(0.5, (st.view[1] - st.view[0]) * f));
  const frac = (t - st.view[0]) / (st.view[1] - st.view[0]);
  st.view = [t - frac * span, t - frac * span + span];
  schedule(renderTimeline);
}
function revealInTimeline(n) {
  if (n._t0 == null) return;
  const span = st.view[1] - st.view[0];
  if (n._t0 < st.view[0] || n._t0 > st.view[1]) st.view = [n._t0 - span / 2, n._t0 + span / 2];
  if (n.agent && !st.agents.has(n.agent)) { st.agents.add(n.agent); buildFilters(); }
}
let pending = new Set(), raf = 0;
function schedule(fn) { pending.add(fn); if (!raf) raf = requestAnimationFrame(() => { raf = 0; const p = [...pending]; pending.clear(); p.forEach(f => f()); }); }

// ── provenance graph ──────────────────────────────────────────────────
const gsvg = document.getElementById("graph"), gScroll = document.getElementById("g-scroll");
let gView = null;
function upstream(id, ctx) {
  const n = byId.get(id); if (!n) return [];
  const out = [];
  if (n.kind === "file") { for (const e of inE.get(id) || []) if (e.kind === "generated") out.push([e.from, e]); }
  else if (n.kind === "call" || n.kind === "command") {
    for (const e of outE.get(id) || []) if (e.kind === "used" || e.kind === "child_of") out.push([e.to, e]);
    if (ctx) for (const e of inE.get(id) || []) if (e.kind === "called") out.push([e.from, e]);
  } else if (ctx && n.kind === "agent") { for (const e of inE.get(id) || []) if (e.kind === "received" || e.kind === "spawned") out.push([e.from, e]); }
  else if (ctx && n.kind === "message") { for (const e of inE.get(id) || []) if (e.kind === "sent") out.push([e.from, e]); }
  return out;
}
function downstream(id, ctx) {
  const n = byId.get(id); if (!n) return [];
  const out = [];
  if (n.kind === "file") { for (const e of inE.get(id) || []) if (e.kind === "used") out.push([e.from, e]); }
  else if (n.kind === "call" || n.kind === "command") {
    for (const e of outE.get(id) || []) if (e.kind === "generated") out.push([e.to, e]);
    for (const e of inE.get(id) || []) if (e.kind === "child_of") out.push([e.from, e]);
  } else if (n.kind === "agent") { if (ctx) for (const e of outE.get(id) || []) if (e.kind === "called" || e.kind === "sent" || e.kind === "spawned") out.push([e.to, e]); }
  else if (ctx && n.kind === "message") { for (const e of outE.get(id) || []) if (e.kind === "received") out.push([e.to, e]); }
  return out;
}
const COL_CAP = 40;
function renderGraph() {
  const info = document.getElementById("g-info");
  gsvg.textContent = "";
  const focus = st.sel && byId.get(st.sel);
  if (!focus) { info.textContent = "Select a node (a call, command, file, agent or message) to trace its provenance."; gsvg.setAttribute("width", 10); gsvg.setAttribute("height", 10); return; }
  const ctx = document.getElementById("g-ctx").checked || focus.kind === "agent" || focus.kind === "message";
  const upD = +document.getElementById("g-up").value, downD = +document.getElementById("g-down").value;
  const layer = new Map([[focus.id, 0]]);
  const gedges = [];
  let frontier = [focus.id], hidden = 0;
  for (let d = 1; d <= upD && frontier.length; d++) {
    const next = [];
    for (const id of frontier) for (const [o, e] of upstream(id, ctx)) { gedges.push(e); if (!layer.has(o)) { layer.set(o, -d); next.push(o); } }
    frontier = next;
  }
  frontier = [focus.id];
  for (let d = 1; d <= downD && frontier.length; d++) {
    const next = [];
    for (const id of frontier) for (const [o, e] of downstream(id, ctx)) { gedges.push(e); if (!layer.has(o)) { layer.set(o, d); next.push(o); } }
    frontier = next;
  }
  const cols = new Map();
  for (const [id, l] of layer) { if (!cols.has(l)) cols.set(l, []); cols.get(l).push(byId.get(id)); }
  const keys = [...cols.keys()].sort((a, b) => a - b);
  const W = 230, NH = 40, GX = 80, GY = 10;
  const pos = new Map();
  let maxRows = 0;
  keys.forEach((k, ci) => {
    const list = cols.get(k).sort((a, b) => (a.kind === b.kind ? 0 : a.kind < b.kind ? -1 : 1) || (a._t0 ?? 0) - (b._t0 ?? 0));
    const shown = list.slice(0, COL_CAP); hidden += list.length - shown.length;
    shown.forEach((n, ri) => pos.set(n.id, { x: 20 + ci * (W + GX), y: 30 + ri * (NH + GY), n }));
    if (list.length > COL_CAP) pos.set("more:" + k, { x: 20 + ci * (W + GX), y: 30 + COL_CAP * (NH + GY), more: list.length - COL_CAP });
    maxRows = Math.max(maxRows, shown.length + (list.length > COL_CAP ? 1 : 0));
  });
  const width = 40 + keys.length * (W + GX), height = 60 + maxRows * (NH + GY);
  const root = s("g", { id: "g-root" });
  keys.forEach((k, ci) => root.appendChild(s("text", { x: 20 + ci * (W + GX), y: 18, class: "colhead", text: k < 0 ? (k === -1 ? "inputs" : "inputs ×" + (-k)) : k === 0 ? "selected" : (k === 1 ? "used by / output" : "downstream ×" + k) })));
  const eg = s("g");
  const seen = new Set();
  for (const e of gedges) {
    const key = e.kind + e.from + ">" + e.to; if (seen.has(key)) continue; seen.add(key);
    const a = pos.get(e.from), b = pos.get(e.to); if (!a || !b) continue;
    // draw left to right in layer order
    let L = a, R = b; if (layer.get(e.from) > layer.get(e.to)) { L = b; R = a; }
    const x1 = L.x + W, y1 = L.y + NH / 2, x2 = R.x, y2 = R.y + NH / 2;
    const p = s("path", { d: `M${x1},${y1} C${x1 + GX / 2},${y1} ${x2 - GX / 2},${y2} ${x2},${y2}`, class: "gedge" + (e.inferred ? " inferred" : "") + ((e.from === focus.id || e.to === focus.id) ? " hl" : "") });
    p.appendChild(s("title", { text: e.kind + (e.basis ? " (" + e.basis + (e.inferred ? ", inferred" : "") + ")" : "") }));
    eg.appendChild(p);
  }
  root.appendChild(eg);
  for (const [id, p] of pos) {
    if (p.more) { root.appendChild(s("text", { x: p.x + 8, y: p.y + 20, class: "muted", text: `+ ${p.more} more (see the detail panel)` })); continue; }
    const n = p.n;
    const g = s("g", { class: "gnode" + (id === focus.id ? " sel" : "") + (n.kind !== "file" && n.kind !== "agent" && !passes(n) ? " dim" : ""), "data-id": id, transform: `translate(${p.x},${p.y})` });
    const color = n.kind === "call" ? (n.ok === false ? "var(--bad)" : tkColor.get(n.toolkit || "?")) : n.kind === "command" ? (n.ok === false ? "var(--bad)" : "var(--cmd)") : n.kind === "file" ? "var(--file)" : n.kind === "agent" ? agentColor.get(n.id) : "var(--msg)";
    g.appendChild(s("rect", { class: "box", width: W, height: NH, rx: 6 }));
    g.appendChild(s("rect", { width: 5, height: NH, rx: 2, fill: color }));
    g.appendChild(s("text", { x: 12, y: 16, class: "t1", text: short(nodeTitle(n), 32) }));
    let sub = "";
    if (n.kind === "call" || n.kind === "command") sub = `${n.kind === "call" ? n.toolkit : n.tool} · ${agentLabel(n.agent)} · ${fmtOff(n._t0)}`;
    else if (n.kind === "file") sub = `${fmtBytes(n.bytes)} · ${(n.sha256 || "no hash").slice(0, 10)} · ${n.origin || ""}`;
    else if (n.kind === "agent") sub = `${n.model || n.engine || ""} · ${n.id}`;
    else sub = `${agentLabel(n.from)} → ${agentLabel(n.to)} · ${fmtOff(n._t0)}`;
    g.appendChild(s("text", { x: 12, y: 31, class: "t2", text: short(sub, 40) }));
    g.appendChild(s("title", { text: n.kind === "file" ? (n.paths || []).join("\n") : tipText(n) }));
    root.appendChild(g);
  }
  gsvg.appendChild(root);
  const vw = gScroll.clientWidth || 800, vh = gScroll.clientHeight || 600;
  gsvg.setAttribute("width", vw); gsvg.setAttribute("height", vh);
  const fx = Math.min(1, vw / width, vh / height);
  const fid = focus.id;
  if (!gView || gView.focus !== fid) {
    const fp = pos.get(fid); const sc = Math.max(fx, 0.55);
    gView = { focus: fid, k: sc, x: vw / 2 - (fp.x + W / 2) * sc, y: Math.max(10, vh / 2 - (fp.y + NH / 2) * sc), w: width, h: height, fit: fx };
    if (fx >= 0.55) { gView.x = (vw - width * fx) / 2; gView.y = 10; gView.k = fx; }
  }
  gView.w = width; gView.h = height; gView.fit = fx;
  applyG();
  info.textContent = `${layer.size} nodes${hidden ? ", " + hidden + " not shown" : ""} · dashed edges are inferred · click a node to follow it`;
}
function applyG() { const r = document.getElementById("g-root"); if (r && gView) r.setAttribute("transform", `translate(${gView.x},${gView.y}) scale(${gView.k})`); }
function wireGraph() {
  let drag = null;
  gsvg.addEventListener("mousedown", e => { if (!gView) return; drag = { x: e.clientX, y: e.clientY, gx: gView.x, gy: gView.y, moved: false }; });
  addEventListener("mousemove", e => { if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true; gView.x = drag.gx + dx; gView.y = drag.gy + dy; applyG(); });
  addEventListener("mouseup", () => setTimeout(() => { drag = null; }, 0));
  gsvg.addEventListener("click", e => { if (drag && drag.moved) return; const id = e.target.closest("[data-id]")?.getAttribute("data-id"); if (id) select(id); });
  gsvg.addEventListener("wheel", e => {
    if (!gView) return; e.preventDefault();
    const r = gsvg.getBoundingClientRect(); const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = Math.exp(-(e.deltaY) * 0.0015); const k2 = Math.min(2.5, Math.max(0.1, gView.k * f));
    gView.x = mx - (mx - gView.x) * (k2 / gView.k); gView.y = my - (my - gView.y) * (k2 / gView.k); gView.k = k2; applyG();
  }, { passive: false });
  for (const id of ["g-up", "g-down", "g-ctx"]) document.getElementById(id).addEventListener("change", () => { gView = null; renderGraph(); });
  document.getElementById("g-fit").onclick = () => { if (!gView) return; const vw = gScroll.clientWidth, vh = gScroll.clientHeight; gView.k = Math.min(1.2, gView.fit); gView.x = (vw - gView.w * gView.k) / 2; gView.y = 10; applyG(); };
}

// ── virtual lists ─────────────────────────────────────────────────────
function vlist(container, rows, cols, cell, onclick, selFn) {
  container.textContent = "";
  const RH = 24;
  const head = h("div", { class: "vhead", style: "grid-template-columns:" + cols.map(c => c.w).join(" ") }, cols.map(c => h("span", { text: c.t, title: c.t, onclick: c.sort ? () => c.sort() : null })));
  const body = h("div", { style: "position:relative;height:" + (rows.length * RH) + "px" });
  container.appendChild(head); container.appendChild(body);
  let lastA = -1, lastB = -1;
  function draw(force) {
    const top = Math.max(0, container.scrollTop - 26), vh = container.clientHeight || 600;
    const a = Math.max(0, Math.floor(top / RH) - 10), b = Math.min(rows.length, Math.ceil((top + vh) / RH) + 10);
    if (!force && a === lastA && b === lastB) return; lastA = a; lastB = b;
    body.textContent = "";
    for (let i = a; i < b; i++) {
      const r = rows[i];
      const el = h("div", { class: "vrow" + (selFn(r) ? " sel" : ""), style: `top:${i * RH}px;grid-template-columns:${cols.map(c => c.w).join(" ")}` }, cell(r));
      el.addEventListener("click", () => onclick(r));
      body.appendChild(el);
    }
  }
  container.onscroll = () => draw(false);
  draw(true);
  return { redraw: () => draw(true) };
}
let actList = null, fileList = null, msgList = null;
function renderActivity() {
  const rows = acts.filter(passes);
  document.getElementById("a-info").textContent = `${rows.length} of ${acts.length} calls and commands (filters apply)`;
  actList = vlist(document.getElementById("a-scroll"), rows,
    [{ t: "time", w: "62px" }, { t: "agent", w: "110px" }, { t: "tool", w: "190px" }, { t: "arguments / command", w: "1fr" }, { t: "took", w: "62px" }, { t: "", w: "44px" }],
    n => [h("span", { class: "mono faint", text: fmtOff(n._t0) }), h("span", null, h("span", { class: "sw", style: `display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;background:${agentColor.get(n.agent)}` }), agentLabel(n.agent)),
      h("span", { title: toolKey(n) }, n.kind === "call" ? n.tool : h("span", { class: "faint", text: "$ " + (n.tool || "") })), h("span", { class: "mono", title: argSummary(n), text: short(argSummary(n), 220) }),
      h("span", { class: "faint", text: n._t1 > n._t0 ? fmtDur(n._t1 - n._t0) : "" }), h("span", { class: isFailed(n) ? "bad" : n.ok == null ? "faint" : "okc", text: isFailed(n) ? "failed" : n.ok == null ? "?" : "ok" })],
    n => select(n.id), n => n.id === st.sel);
}
let fileSort = "path";
function renderFiles() {
  const q = document.getElementById("fl-q").value.trim().toLowerCase();
  const og = document.getElementById("fl-origin").value, onlyG = document.getElementById("fl-graph").checked;
  let rows = FROWS.filter(r => (!q || r.path.toLowerCase().includes(q) || (r.sha && r.sha.startsWith(q))) && (!onlyG || r.node) &&
    (!og || (og === "absent" ? !r.present : r.present && r.origin === og)));
  if (fileSort === "size") rows = rows.slice().sort((a, b) => (b.bytes || 0) - (a.bytes || 0));
  const unk = FROWS.filter(r => r.present && r.origin === "unknown").length;
  document.getElementById("fl-info").textContent = `${rows.length} of ${FROWS.length} files · ${unk} of unknown origin`;
  fileList = vlist(document.getElementById("fl-scroll"), rows,
    [{ t: "path", w: "1fr", sort: () => { fileSort = "path"; renderFiles(); } }, { t: "size", w: "70px", sort: () => { fileSort = "size"; renderFiles(); } }, { t: "sha256", w: "92px" }, { t: "origin", w: "90px" }, { t: "generated by", w: "150px" }],
    r => [h("span", { class: "mono", title: r.path, text: r.path }), h("span", { class: "faint", text: fmtBytes(r.bytes) }), h("span", { class: "mono faint", text: (r.sha || "—").slice(0, 10) }),
      h("span", { class: r.origin === "unknown" ? "warn" : r.origin === "inferred" ? "faint" : "okc", text: !r.present ? "absent" : r.origin }),
      h("span", { text: r.gen ? nodeTitle(byId.get(r.gen)) : (r.before ? "before the trace" : "") })],
    r => selectRow(r), r => st.selRow === r.i || (r.node && r.node === st.sel));
}
function renderMessages() {
  const rows = msgs.filter(passes);
  msgList = vlist(document.getElementById("m-scroll"), rows,
    [{ t: "time", w: "62px" }, { t: "", w: "56px" }, { t: "from", w: "110px" }, { t: "to", w: "110px" }, { t: "body / brief", w: "1fr" }],
    m => [h("span", { class: "mono faint", text: fmtOff(m._t0) }), h("span", { class: "pill", text: m.event }), h("span", { text: agentLabel(m.from) }), h("span", { text: agentLabel(m.to) }), h("span", { text: short(oneLine(m.event === "spawn" ? (m.initial_message || m.role_prompt) : m.body), 300) })],
    m => select(m.id), m => m.id === st.sel);
}

// ── detail panel ──────────────────────────────────────────────────────
const detail = document.getElementById("detail");
function link(id, text) {
  const n = byId.get(id);
  return h("a", { href: "#" + id, title: id }, text || (n ? nodeTitle(n) : id));
}
function anchorBar(id) {
  const url = location.href.split("#")[0] + "#" + id;
  return h("div", { class: "anchor" }, h("code", { text: "#" + id }), h("button", { onclick: e => { copy(url); e.target.textContent = "copied"; setTimeout(() => e.target.textContent = "copy link", 1200); }, text: "copy link" }));
}
function copy(t) { try { navigator.clipboard.writeText(t); } catch (_) { const ta = h("textarea", null, t); document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); } }
function dl(pairs) { return h("dl", null, pairs.filter(p => p && p[1] != null && p[1] !== "").map(([k, v]) => [h("dt", { text: k }), h("dd", null, v)]).flat()); }
function pre(text) { return h("pre", { text: text == null ? "" : String(text) }); }
function json(v) { return pre(JSON.stringify(v, null, 2)); }
function fileList_(edges, dir) {
  const ul = h("ul");
  const max = 300;
  edges.slice(0, max).forEach(e => {
    const id = dir === "to" ? e.to : e.from; const f = byId.get(id);
    const label = f && f.kind === "file" ? (f.paths || [id]).join(", ") : nodeTitle(f);
    ul.appendChild(h("li", null, link(id, label), f && f.kind === "file" ? h("span", { class: "faint mono", text: `  ${fmtBytes(f.bytes)}  ${(f.sha256 || "no hash").slice(0, 16)}` }) : null, e.inferred ? h("span", { class: "pill", text: "inferred: " + (e.basis || "") }) : null));
  });
  if (edges.length > max) ul.appendChild(h("li", { class: "more", text: `… ${edges.length - max} more` }));
  return ul;
}
function renderDetail() {
  detail.textContent = "";
  if (st.selRow != null && !st.sel) { renderRowDetail(FROWS[st.selRow]); return; }
  const n = st.sel && byId.get(st.sel);
  if (!n) { detail.appendChild(h("div", { class: "empty", text: "Select a call, command, agent, file or message." })); return; }
  const kind = h("span", { class: "kind k-" + n.kind, text: n.kind });
  if (n.kind === "call") {
    detail.append(h("h2", null, kind, n.tool), h("div", { class: "sub" }, `${n.toolkit}${n.version ? " " + n.version : ""} · `, link(n.agent, agentLabel(n.agent)), ` · ${fmtAbs(n._t0)}`), anchorBar(n.id));
    detail.append(dl([["status", n.ok === false ? h("span", { class: "pill bad", text: "failed" }) : h("span", { class: "pill ok", text: "ok" })], ["took", fmtDur(n._t1 - n._t0)], ["started", fmtAbs(n._t0) + "  (" + fmtOff(n._t0) + ")"], ["ended", n.ts_end ? fmtAbs(n._t1) : null], ["parent", n.parent ? link(n.parent) : null]]));
    if (n.error) detail.append(h("h4", { text: "error" }), pre(n.error));
    detail.append(h("h4", { text: "arguments" }), json(n.args || {}));
    if (n.result) detail.append(h("h4", { text: "result (first 2048 characters)" }), pre(n.result.head), h("div", { class: "more mono", text: "sha256 " + n.result.sha256 }));
  } else if (n.kind === "command") {
    detail.append(h("h2", null, kind, n.tool === "Bash" || n.tool === "exec_command" || n.tool === "shell" ? "shell command" : n.tool), h("div", { class: "sub" }, link(n.agent, agentLabel(n.agent)), ` · ${n.source}${n.sources ? " (" + n.sources.join(", ") + ")" : ""} · ${fmtAbs(n._t0)}`), anchorBar(n.id));
    detail.append(h("h4", { text: "command" }), pre(n.command));
    detail.append(dl([["status", n.ok === false ? h("span", { class: "pill bad", text: "failed" + (n.exit_code != null ? " (exit " + n.exit_code + ")" : "") }) : n.ok == null ? h("span", { class: "pill warn", text: "no result" }) : h("span", { class: "pill ok", text: "ok" + (n.exit_code != null ? " (exit " + n.exit_code + ")" : "") })],
      ["took", n.ts_end ? fmtDur(n._t1 - n._t0) : "?"], ["cwd", n.cwd], ["background", n.background ? "yes (the window is only until it returned)" : null], ["description", n.description], ["attributed", n.attributed_by], ["tool use", h("span", { class: "mono", text: n.tool_use_id })]]));
    if (n.script) detail.append(h("h4", { text: "script" }), dl([["path", rowLink(n.script.path)], ["sha256", h("span", { class: "mono", text: n.script.sha256 || "not hashed" })], ["size", fmtBytes(n.script.bytes)], ["note", n.script.changed_since ? "hash at export; the script changed after this command ran" : "hash at export"]]));
    if (n.writes_unmatched) detail.append(h("h4", { text: "wrote (since rewritten)" }), h("ul", null, n.writes_unmatched.map(p => h("li", null, rowLink(p)))));
    if (n.output) detail.append(h("h4", { text: "output (first 2048 characters)" }), pre(n.output.head), h("div", { class: "more mono", text: "sha256 " + n.output.sha256 }));
  } else if (n.kind === "agent") {
    detail.append(h("h2", null, kind, n.label || n.id), h("div", { class: "sub", text: [n.model, n.engine, n.harness].filter(Boolean).join(" · ") }), anchorBar(n.id));
    const nc = (outE.get(n.id) || []).filter(e => e.kind === "called").length;
    detail.append(dl([["id", h("span", { class: "mono", text: n.id })], ["parent", n.parent ? link(n.parent, agentLabel(n.parent)) : null], ["started", fmtAbs(n._t0)], ["ended", n.ts_end ? fmtAbs(n._t1) : (n.external ? null : h("span", { class: "pill warn", text: "never ended" }))],
      ["terminated", n.terminated ? `by ${n.terminated.by || "?"}${n.terminated.cascade ? " (cascade)" : ""}` : null], ["activity", `${nc} calls and commands`], ["note", n.placeholder ? "placeholder: a transcript nobody could attribute" : n.unregistered ? "not registered in agents.jsonl" : null]]));
    const sp = n.spawn && byId.get(n.spawn);
    if (sp) {
      if (sp.toolkits) detail.append(h("h4", { text: "toolkits" }), json(sp.toolkits));
      if (sp.initial_message) detail.append(h("h4", { text: "brief (initial message)" }), pre(sp.initial_message));
      if (sp.role_prompt) detail.append(h("h4", { text: "role prompt" }), pre(sp.role_prompt));
    }
    const ch = (outE.get(n.id) || []).filter(e => e.kind === "spawned");
    if (ch.length) detail.append(h("h4", { text: "spawned" }), h("ul", null, ch.map(e => h("li", null, link(e.to, agentLabel(e.to))))));
    const ms = msgs.filter(m => m.from === n.id || m.to === n.id);
    if (ms.length) detail.append(h("h4", { text: "messages" }), h("ul", null, ms.map(m => h("li", null, h("span", { class: "faint mono", text: fmtOff(m._t0) + " " }), link(m.id, (m.from === n.id ? "→ " + agentLabel(m.to) : "← " + agentLabel(m.from)) + ": " + short(oneLine(m.event === "spawn" ? m.initial_message : m.body), 80))))));
  } else if (n.kind === "file") {
    detail.append(h("h2", null, kind, base((n.paths || [])[0]) || n.id), h("div", { class: "sub", text: (n.paths || []).join(", ") }), anchorBar(n.id));
    const rows = rowsByNode.get(n.id) || [];
    const cp = n.copied || (rows.find(r => r.copied) && "files/" + rows.find(r => r.copied).path);
    detail.append(dl([["sha256", h("span", { class: "mono", text: n.sha256 || "not hashed" })], ["size", fmtBytes(n.bytes)], ["mtime", n.mtime], ["origin", n.origin], ["in sandbox", n.present ? "yes, this version" : h("span", { class: "warn", text: "no (" + (rows[0]?.note || "not present at export") + ")" })], ["copy", cp ? h("a", { href: encodeURI(cp), target: "_blank", text: cp }) : "not copied (too large, or not in the sandbox)"]]));
    const gen = (inE.get(n.id) || []).filter(e => e.kind === "generated"), used = (inE.get(n.id) || []).filter(e => e.kind === "used");
    detail.append(h("h4", { text: "generated by" }), gen.length ? fileList_(gen, "from") : h("div", { class: "faint", text: "nothing in the trace: unknown origin" }));
    if (used.length) detail.append(h("h4", { text: "used by" }), fileList_(used, "from"));
  } else if (n.kind === "message") {
    const spawn = n.event === "spawn";
    detail.append(h("h2", null, kind, spawn ? "spawn of " + agentLabel(n.to) : "message to " + agentLabel(n.to)), h("div", { class: "sub" }, n.from ? link(n.from, agentLabel(n.from)) : "?", " → ", n.to ? link(n.to, agentLabel(n.to)) : "?", ` · ${fmtAbs(n._t0)}`), anchorBar(n.id));
    if (spawn) {
      detail.append(dl([["model", n.model], ["engine", n.engine]]));
      if (n.toolkits) detail.append(h("h4", { text: "toolkits" }), json(n.toolkits));
      detail.append(h("h4", { text: "brief (initial message)" }), pre(n.initial_message || "(none)"));
      if (n.role_prompt) detail.append(h("h4", { text: "role prompt" }), pre(n.role_prompt));
    } else {
      detail.append(dl([["msg id", n.msg_id], ["thread", n.thread_id]]));
      detail.append(h("h4", { text: "body" }), pre(txt(n.body)));
    }
  }
  if (n.kind === "call" || n.kind === "command") {
    const oe = outE.get(n.id) || [];
    const used = oe.filter(e => e.kind === "used"), gen = oe.filter(e => e.kind === "generated");
    detail.append(h("h4", { text: `files used (${used.length})` }), used.length ? fileList_(used, "to") : h("div", { class: "faint", text: "none recorded" }));
    detail.append(h("h4", { text: `files generated (${gen.length})` }), gen.length ? fileList_(gen, "to") : h("div", { class: "faint", text: "none recorded" }));
    const inferredRows = FROWS.filter(r => r.gen === n.id && !r.node);
    if (inferredRows.length) detail.append(h("h4", { text: `other files it wrote (inferred, ${inferredRows.length})` }), h("ul", null, inferredRows.slice(0, 200).map(r => h("li", null, h("a", { onclick: () => selectRow(r), text: r.path }), h("span", { class: "faint mono", text: "  " + fmtBytes(r.bytes) })))), inferredRows.length > 200 ? h("div", { class: "more", text: `… ${inferredRows.length - 200} more (Files tab)` }) : null);
    const kids_ = (inE.get(n.id) || []).filter(e => e.kind === "child_of");
    if (kids_.length) detail.append(h("h4", { text: "child calls" }), h("ul", null, kids_.map(e => h("li", null, link(e.from)))));
  }
}
function rowLink(path) {
  const r = FROWS.find(x => x.path === path);
  return r ? h("a", { onclick: () => selectRow(r), text: path }) : h("span", { class: "mono", text: path });
}
function renderRowDetail(r) {
  if (!r) return;
  detail.append(h("h2", null, h("span", { class: "kind k-file", text: "file" }), base(r.path)), h("div", { class: "sub", text: r.path }));
  if (r.sha) detail.append(anchorBar("f-" + r.sha.slice(0, 12)));
  detail.append(dl([["sha256", h("span", { class: "mono", text: r.sha || "not hashed" })], ["size", fmtBytes(r.bytes)], ["mtime", r.mtime], ["origin", r.origin + (r.basis ? " (" + r.basis + ")" : "")],
    ["generated by", r.gen ? link(r.gen) : r.before ? "present before the trace began" : "nothing in the trace"], ["copy", r.copied ? h("a", { href: encodeURI("files/" + r.path), target: "_blank", text: "files/" + r.path }) : "not copied"]]));
  if (r.node) detail.append(h("p", null, link(r.node, "open the graph node")));
}

// ── selection and anchors ─────────────────────────────────────────────
function resolveAnchor(id) {
  if (byId.has(id)) return { id };
  if (/^f-[0-9a-f]{4,}$/i.test(id)) {
    const pre_ = id.slice(2).toLowerCase();
    for (const n of NODES) if (n.kind === "file" && n.sha256 && n.sha256.startsWith(pre_)) return { id: n.id };
    const r = FROWS.find(r => r.sha && r.sha.startsWith(pre_)); if (r) return r.node ? { id: r.node } : { row: r.i };
  }
  if (/^(c|cmd)-/.test(id)) { for (const n of NODES) if (n.id.startsWith(id)) return { id: n.id }; }
  if (/^m-/.test(id) && byId.has("m-" + id.slice(2).replace(/^msg-/, ""))) return { id: "m-" + id.slice(2).replace(/^msg-/, "") };
  return null;
}
function select(id, fromHash) {
  const n = byId.get(id); if (!n) return;
  st.sel = id; st.selRow = null;
  relSet = new Set();
  for (const e of outE.get(id) || []) relSet.add(e.to);
  for (const e of inE.get(id) || []) relSet.add(e.from);
  if (n.kind === "file") for (const e of inE.get(id) || []) relSet.add(e.from);
  if (!fromHash) history.replaceState(null, "", "#" + id);
  if (n.kind === "call" || n.kind === "command" || n.kind === "message") revealInTimeline(n);
  if (fromHash && n.kind === "file" && st.tab === "timeline") setTab("graph");
  renderDetail(); refreshView();
}
function selectRow(r) {
  if (r.node) { select(r.node); return; }
  st.sel = null; st.selRow = r.i;
  if (r.sha) history.replaceState(null, "", "#f-" + r.sha.slice(0, 12));
  renderDetail(); if (fileList) fileList.redraw();
}
function fromHash() {
  const id = decodeURIComponent(location.hash.slice(1)); if (!id) return;
  const r = resolveAnchor(id);
  if (!r) { detail.textContent = ""; detail.appendChild(h("div", { class: "empty", text: `No node ${id} in this trace.` })); return; }
  if (r.id) select(r.id, true); else { setTab("files"); selectRow(FROWS[r.row]); }
}
addEventListener("hashchange", fromHash);

// ── tabs and refresh ──────────────────────────────────────────────────
const HINTS = { timeline: "one lane per agent; spawns and messages link lanes", graph: "inputs on the left, what used it on the right", activity: "every call and command that passes the filters", files: "every file in the bundle's index", messages: "spawns with their briefs, and sends" };
function setTab(t) {
  st.tab = t;
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("on", b.dataset.tab === t);
  for (const v of document.querySelectorAll(".view")) v.classList.toggle("on", v.id === "v-" + t);
  document.getElementById("tabhint").textContent = HINTS[t] || "";
  refreshView();
}
function refreshView() {
  if (st.tab === "timeline") renderTimeline();
  else if (st.tab === "graph") renderGraph();
  else if (st.tab === "activity") renderActivity();
  else if (st.tab === "files") renderFiles();
  else if (st.tab === "messages") renderMessages();
}
function refresh() { schedule(refreshView); }

function header() {
  const c = META.counts || {};
  document.getElementById("title").textContent = "Trace " + (META.session || "");
  document.title = "Trace " + (META.session || "");
  document.getElementById("summary").textContent = `${c.agents ?? agents.length} agents · ${c.calls ?? 0} calls (${c.failed_calls ?? 0} failed) · ${c.commands ?? 0} commands · ${c.messages ?? 0} messages · ${c.files ?? FROWS.length} files (${c.unknown_origin ?? 0} unknown origin) · ${fmtAbs(T0)} → ${fmtAbs(T1)} (${fmtDur(T1 - T0)})${META.complete === false ? " · incomplete" : ""}`;
  const w = META.warnings || [];
  const btn = document.getElementById("warnbtn"), box = document.getElementById("warnbox");
  if (w.length) {
    btn.style.display = ""; btn.textContent = `${w.length} note${w.length > 1 ? "s" : ""}`;
    const ul = document.getElementById("warnlist"); for (const x of w) ul.appendChild(h("li", { text: x }));
    btn.onclick = () => { box.style.display = box.style.display === "block" ? "none" : "block"; };
  }
  document.getElementById("themebtn").onclick = () => {
    const r = document.documentElement; const dark = r.dataset.theme ? r.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    r.dataset.theme = dark ? "light" : "dark";
  };
}

header(); buildFilters(); wireFilters(); wireTimeline(); wireGraph();
for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => setTab(b.dataset.tab));
for (const id of ["fl-q", "fl-origin", "fl-graph"]) document.getElementById(id).addEventListener(id === "fl-q" ? "input" : "change", renderFiles);
addEventListener("resize", () => schedule(refreshView));
setTab("timeline");
fromHash();
window.__trace = { nodes: NODES.length, edges: EDGES.length, files: FROWS.length, select, setTab, st };
})();

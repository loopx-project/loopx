#!/usr/bin/env -S node --no-warnings --experimental-strip-types
/** Render the time-anchored research history as a Lark-whiteboard SVG.
 *
 * Input is either a JSONL Explore result log or a captured-event fixture JSON.
 * The SVG is display-only: record-time thirds, routes
 * from recorded edges/parent grouping and explicit path_delta decisions; it
 * never adds edges, scores or evaluations. Upload via the lark-whiteboard skill:
 *   npx -y @larksuite/whiteboard-cli@^0.2.13 -i out.svg -f svg --check
 *   npx ... -i out.svg -f svg --to openapi --format json | lark-cli whiteboard +update ...
 *
 * Usage: node examples/research-history-lark/board.mts <log.jsonl|fixture.json> <out.svg> "<title>" <goal_id>
 */
// Prototype Lark board: time-anchored research history over the shared timeline model.
import {readFileSync, writeFileSync} from "node:fs";
import {parseExploreHistoryPage, projectExploreHistory} from "../../loopx/control_plane/presentation/explore_history.ts";
import {buildExploreTimeline, selectExploreTimelineDecisions} from "../../loopx/control_plane/presentation/explore_timeline.ts";

const [source, out, title, goal] = process.argv.slice(2);
let raw: unknown[];
try {
  const parsed = JSON.parse(readFileSync(source, "utf8"));
  raw = Array.isArray(parsed) ? parsed : parsed.events;
} catch (error) {
  if (!(error instanceof SyntaxError)) throw error;
  raw = readFileSync(source, "utf8").trim().split("\n").filter(Boolean).map(line => JSON.parse(line));
}
const page = parseExploreHistoryPage({ok: true, goal_id: goal, items: raw, total: raw.length, next_cursor: null}, goal);
const view = projectExploreHistory(page.items, goal);
const t = buildExploreTimeline(view.nodes, view.edges, view.events, {targetBins: 48});
if (t.start === null || t.end === null) throw new Error("No usable record time. Read the source records without a time axis.");
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const width = (s: string) => [...s].reduce((n, c) => n + (/[\u2E80-\uFFFF]/.test(c) ? 1 : 0.56), 0);
const cut = (s: string, w: number) => { let o = "", n = 0; for (const c of s) { const d = /[\u2E80-\uFFFF]/.test(c) ? 1.05 : 0.66; if (n + d > w - 1) return o + "…"; o += c; n += d; } return o; };
const fmt = (at: number) => new Intl.DateTimeFormat("zh-CN", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai"}).format(at);
const day = (at: number) => new Intl.DateTimeFormat("zh-CN", {month: "numeric", day: "numeric", timeZone: "Asia/Shanghai"}).format(at);
const C = {text: "#1F2329", muted: "#646A73", faint: "#8F959E", line: "#DEE0E3", bg: "#F5F6F7",
  confirmed: "#509863", confirmedBg: "#DFF5E5", refuted: "#D25D5A", refutedBg: "#FEE3E2", tentative: "#D4B45B", tentativeBg: "#FEF1CE", none: "#BBBFC4", noneBg: "#F2F3F5",
  replan: "#8569CB", replanBg: "#EAE2FE", cont: "#5178C6", contBg: "#F0F4FC", edge: "#8F959E"};
const labelColor = (l: string) => l === "confirmed" ? C.confirmed : l === "refuted" ? C.refuted : l === "tentative" ? C.tentative : C.none;
const labelBg = (l: string) => l === "confirmed" ? C.confirmedBg : l === "refuted" ? C.refutedBg : l === "tentative" ? C.tentativeBg : C.noneBg;
const LABEL = {confirmed: "来源记为确认", refuted: "已反驳", tentative: "待检查"} as Record<string, string>;
const OUT = {replan: "转向 replan", continue: "继续 continue", stop: "停止", wait: "等待", no_change: "无变化", ask_human: "请人决定"} as Record<string, string>;

const W = 1680, M = 48, plotX = M + 210, plotW = W - plotX - M;
const s: string[] = [];
let y = 40;
const text = (x: number, yy: number, v: string, o: {size?: number; color?: string; weight?: number; anchor?: string} = {}) =>
  s.push(`<text x="${x}" y="${yy}" font-size="${o.size ?? 14}" fill="${o.color ?? C.text}"${o.weight ? ` font-weight="${o.weight}"` : ""}${o.anchor ? ` text-anchor="${o.anchor}"` : ""}>${esc(v)}</text>`);
const X = (at: number) => plotX + (at - t.start!) / Math.max(1, t.end! - t.start!) * plotW;

// Header
text(M, y + 10, title, {size: 28, weight: 700});
const spanH = (t.end! - t.start!) / 3_600_000;
text(M, y + 44, `Explore 已记录历史 · ${fmt(t.start!)} — ${fmt(t.end!)} · 跨度 ${spanH >= 48 ? `${(spanH / 24).toFixed(1)} 天` : `${spanH.toFixed(1)} 小时`} · ${view.events.length} 条记录 · ${view.nodes.length} 项研究 · ${t.findings.length} 条结论`, {size: 15, color: C.muted});
text(M, y + 68, "时间为记录时间（含延后分析与回填），不是尝试发生时间；原生成绩未接入，本图不表达分数提升。", {size: 13, color: C.faint});
y += 100;

// Phase cards
const names = ["前段", "中段", "后段"];
const cardW = (W - 2 * M - 2 * 24) / 3, cardH = 196;
for (const p of t.phases) {
  const x = M + p.index * (cardW + 24);
  const late = p.index === 2;
  s.push(`<rect x="${x}" y="${y}" width="${cardW}" height="${cardH}" rx="12" fill="${late ? "#F0F4FC" : "#FFFFFF"}" stroke="${late ? C.cont : C.line}" stroke-width="2"/>`);
  text(x + 20, y + 32, `${names[p.index]} · ${fmt(p.start)} — ${fmt(p.end)}`, {size: 16, weight: 700});
  text(x + 20, y + 62, `研究项 ${p.nodes}`, {size: 22, weight: 700});
  text(x + 160, y + 62, `结论 ${p.findings}`, {size: 22, weight: 700});
  // stacked label bar
  const total = Math.max(1, p.findings); let bx = x + 20; const bw = cardW - 40;
  for (const l of ["confirmed", "refuted", "tentative"]) {
    const n = p.labels[l] ?? 0; if (!n) continue; const w = bw * n / total;
    s.push(`<rect x="${bx}" y="${y + 80}" width="${Math.max(2, w - 2)}" height="22" rx="4" fill="${labelColor(l)}"/>`);
    if (w > 110) text(bx + 8, y + 96, `${LABEL[l]} ${n}`, {size: 12, color: "#FFFFFF", weight: 600});
    else if (w > 24) text(bx + 6, y + 96, `${n}`, {size: 12, color: "#FFFFFF", weight: 600});
    bx += w;
  }
  const d = Object.entries(p.decisions);
  text(x + 20, y + 132, d.length ? `显式决策：${d.map(([k, v]) => `${OUT[k] ?? k} ${v}`).join(" · ")}` : "显式决策：未记录", {size: 14, color: d.length ? C.text : C.faint});
  const lastIn = p.lastDecision;
  if (lastIn) {
    text(x + 20, y + 158, cut(`最近变化：${lastIn.sections!.decision!.changed[0] ?? "—"}`, (cardW - 40) / 13.5), {size: 13, color: C.muted});
    text(x + 20, y + 180, cut(`停止：${lastIn.sections!.decision!.stopped[0] ?? "—"}`, (cardW - 40) / 13.5), {size: 13, color: C.muted});
  } else text(x + 20, y + 158, t.structuredCount ? "本段无 path_delta 决策记录" : "结论未按 writeback 结构记录，无法拆出依据/决策", {size: 13, color: C.faint});
}
y += cardH + 40;

// Time axis + density
text(M, y + 14, "记录密度", {size: 15, weight: 700});
text(M, y + 34, "柱高=事件数 · 浅红=无记录空档", {size: 12, color: C.faint});
text(M, y + 54, "下方刻度：紫=转向 蓝=继续 灰=其它决策", {size: 12, color: C.faint});
const maxE = Math.max(1, ...t.bins.map(b => b.events)), bandH = 56;
for (const b of t.bins) {
  const x0 = X(b.start), x1 = X(b.end), h = b.events ? Math.max(3, bandH * b.events / maxE) : bandH;
  s.push(b.events ? `<rect x="${x0 + 1}" y="${y + bandH - h}" width="${Math.max(1, x1 - x0 - 2)}" height="${h}" rx="2" fill="#BACEFD"/>`
    : `<rect x="${x0 + 1}" y="${y}" width="${Math.max(1, x1 - x0 - 2)}" height="${bandH}" rx="2" fill="${C.refutedBg}"/>`);
}
for (const item of t.decisions) {
  if (item.at === null) continue;
  const x = X(item.at), outcome = item.sections!.decision!.outcome;
  const color = outcome === "replan" ? C.replan : outcome === "continue" ? C.cont : C.muted;
  s.push(`<rect x="${x - 1.5}" y="${y + bandH + 4}" width="3" height="10" rx="1" fill="${color}"><title>${esc(OUT[outcome] ?? outcome)}</title></rect>`);
}
for (const p of t.phases.slice(1)) s.push(`<line x1="${X(p.start)}" y1="${y - 8}" x2="${X(p.start)}" y2="${y + bandH + 8}" stroke="${C.faint}" stroke-dasharray="4 4"/>`);
y += bandH + 22;
// day ticks
const d0 = new Date(t.start!); d0.setUTCHours(16, 0, 0, 0); if (d0.getTime() < t.start!) d0.setUTCDate(d0.getUTCDate() + 1);
s.push(`<line x1="${plotX}" y1="${y}" x2="${plotX + plotW}" y2="${y}" stroke="${C.line}"/>`);
const ticks: number[] = []; for (let at = d0.getTime(); at <= t.end!; at += 86_400_000) ticks.push(at);
const stride = Math.ceil(ticks.length / 12);
ticks.forEach((at, i) => { if (i % stride) return; s.push(`<line x1="${X(at)}" y1="${y}" x2="${X(at)}" y2="${y + 6}" stroke="${C.faint}"/>`); text(X(at), y + 22, day(at), {size: 12, color: C.muted, anchor: "middle"}); });
y += 44;

// Routes: compact time-anchored chains (markers + recorded edge arcs), no staircase.
text(M, y, "研究路线（仅由显式关联/父子分组相连）", {size: 18, weight: 700});
text(M + 420, y, "横轴=首次记录时间 · 实线弧=显式关联 · 红弧=反驳 · 虚线=父子分组 · 圆点颜色=最新结论来源标签 · 空心=仍开放", {size: 13, color: C.faint});
y += 18;
const pos = new Map<string, {x: number; y: number}>();
const lanes = t.routes.filter(route => route.first !== null).sort((a, b) => b.nodeIds.length - a.nodeIds.length).slice(0, 8).sort((a, b) => (a.first ?? 0) - (b.first ?? 0));
text(M, y + 14, `静态摘录：${lanes.length}/${t.routes.length} 条关联分组；未展示的分组请在工作台读取。`, {size: 12, color: C.muted});
y += 28;
const laneH = 78;
for (const route of lanes) {
  const members = route.nodeIds.map(id => t.nodes.get(id)!).filter(item => item.first !== null);
  s.push(`<rect x="${M}" y="${y}" width="${W - 2 * M}" height="${laneH}" rx="10" fill="#FAFBFC" stroke="${C.line}" stroke-dasharray="4 4"/>`);
  const labels = members.map(m => m.label);
  const tally = ["confirmed", "refuted", "tentative"].map(l => labels.filter(x => x === l).length ? `${LABEL[l]} ${labels.filter(x => x === l).length}` : "").filter(Boolean).join(" · ");
  text(M + 14, y + 24, `${members.length} 个问题 · ${day(route.first!)}–${day(route.last!)}`, {size: 13, weight: 700});
  text(M + 14, y + 44, tally || "无结论", {size: 12, color: C.muted});
  const span = (route.last! - route.first!) / 3_600_000;
  text(M + 14, y + 62, `记录跨度 ${span >= 24 ? (span / 24).toFixed(1) + " 天" : span.toFixed(1) + " 小时"}`, {size: 12, color: C.faint});
  const cy = y + laneH - 22;
  s.push(`<line x1="${X(route.first!)}" y1="${cy}" x2="${X(route.last!)}" y2="${cy}" stroke="${C.line}" stroke-width="2"/>`);
  for (const m of members) pos.set(m.node.id, {x: X(m.first ?? t.start!), y: cy});
  const first = members[0]!, last = members.at(-1)!;
  const x0 = X(first.first!), x1 = X(last.first!);
  // Start label above the chain, stop label to the right of its end (left only if it would overflow).
  text(Math.min(x0, plotX + plotW - 520), y + 22, cut(`起：${first.node.latest.title}`, 38), {size: 12});
  if (members.length > 1) {
    const stop = cut(`止：${last.node.latest.title}`, 30), sw = width(stop) * 12.6;
    const right = x1 + 14 + sw <= plotX + plotW;
    text(right ? x1 + 14 : x1 - 14, cy + 4, stop, {size: 12, color: labelColor(last.label), anchor: right ? undefined : "end"});
  }
  y += laneH + 10;
}
const edgeSvg: string[] = [];
const arc = (a: {x: number; y: number}, b: {x: number; y: number}, color: string, dash = "") => {
  const [p, q] = a.x <= b.x ? [a, b] : [b, a]; const h = Math.min(26, 8 + (q.x - p.x) / 6);
  edgeSvg.push(`<path d="M${p.x},${p.y - 6} C${p.x},${p.y - 6 - h} ${q.x},${q.y - 6 - h} ${q.x},${q.y - 6}" fill="none" stroke="${color}" stroke-width="1.5"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`);
};
for (const e of view.edges) { const a = pos.get(e.from_node), b = pos.get(e.to_node); if (a && b) arc(a, b, e.edge_type === "refutes" ? C.refuted : C.edge); }
for (const node of view.nodes) { const a = pos.get(node.id), b = pos.get(node.latest.parent_id); if (a && b) arc(a, b, C.faint, "3 4"); }
s.push(...edgeSvg);
for (const [id, p] of pos) { const m = t.nodes.get(id)!; const open = m.node.latest.status === "open" || m.node.latest.status === "exploring";
  s.push(`<circle cx="${p.x}" cy="${p.y}" r="6" fill="${open ? "#FFFFFF" : labelColor(m.label)}" stroke="${labelColor(m.label)}" stroke-width="2"/>`); }
// Singles beeswarm
y += 8;
const singles = t.singles.map(id => t.nodes.get(id)!).filter(n => n.first !== null);
const cols = new Map<number, number>(); const dot = 9;
let maxStack = 0;
const dots = singles.map(n => { const cx = X(n.first!), key = Math.round((cx - plotX) / (dot + 1)); const k = cols.get(key) ?? 0; cols.set(key, k + 1); maxStack = Math.max(maxStack, k + 1); return {n, cx: plotX + key * (dot + 1) + dot / 2, k}; });
const swarmH = Math.max(40, maxStack * (dot + 1) + 24);
s.push(`<rect x="${M}" y="${y}" width="${W - 2 * M}" height="${swarmH}" rx="10" fill="#FAFBFC" stroke="${C.line}" stroke-dasharray="4 4"/>`);
text(M + 14, y + 26, "未关联的单点问题", {size: 13, weight: 700});
text(M + 14, y + 46, `${singles.length} 个 · 不推断路线或因果`, {size: 12, color: C.muted});
for (const {n, cx, k} of dots) s.push(`<circle cx="${cx}" cy="${y + swarmH - 12 - k * (dot + 1)}" r="${dot / 2}" fill="${labelColor(n.label)}" fill-opacity="${n.node.latest.status === "open" ? 0.45 : 0.9}"/>`);
y += swarmH + 36;

// Decision trail (late first)
text(M, y, `为什么转向：最近 ${Math.min(6, t.decisions.length)}/${t.decisions.length} 条显式决策（按记录时间，保留同一问题多轮）`, {size: 18, weight: 700});
y += 16;
const trail = selectExploreTimelineDecisions(t, null).slice(0, 6);
if (!trail.length) { text(M, y + 24, "来源没有按 Outcome/retained/changed/stopped 记录决策；只能读到结论标签，无法回答“为什么转向”。", {size: 14, color: C.faint}); y += 48; }
const colW = (W - 2 * M - 24) / 2;
trail.forEach((item, i) => {
  const d = item.sections!.decision!, x = M + (i % 2) * (colW + 24), yy = y + Math.floor(i / 2) * 150;
  const replan = d.outcome === "replan";
  s.push(`<rect x="${x}" y="${yy}" width="${colW}" height="136" rx="10" fill="#FFFFFF" stroke="${C.line}" stroke-width="1.5"/>`);
  s.push(`<rect x="${x + 16}" y="${yy + 14}" width="${width(OUT[d.outcome] ?? d.outcome) * 13 + 20}" height="24" rx="12" fill="${replan ? C.replanBg : C.contBg}"/>`);
  text(x + 26, yy + 31, OUT[d.outcome] ?? d.outcome, {size: 13, color: replan ? C.replan : C.cont, weight: 700});
  text(x + width(OUT[d.outcome] ?? d.outcome) * 13 + 48, yy + 31, `${item.at === null ? "时间未记录" : fmt(item.at)} · ${LABEL[item.event.status] ?? item.event.status}`, {size: 12, color: C.muted});
  text(x + 16, yy + 60, cut(item.event.title, (colW - 32) / 15.8), {size: 14, weight: 600});
  text(x + 16, yy + 84, cut(`变化：${d.changed.join("；") || "—"}`, (colW - 32) / 14.2), {size: 13});
  text(x + 16, yy + 106, cut(`停止：${d.stopped.join("；") || "—"}`, (colW - 32) / 14.2), {size: 13, color: C.muted});
  text(x + 16, yy + 126, cut(`保留：${d.retained.join("；") || "—"}`, (colW - 32) / 13.2), {size: 12, color: C.faint});
});
y += Math.ceil(trail.length / 2) * 150 + 20;
text(M, y, "图中分段按记录时间三等分，仅为阅读分组，不是研究方预声明的评估窗口；结论标签沿用来源，不代表独立检查或实际采用。", {size: 12, color: C.faint});
const untimed = [...t.nodes.values()].filter(item => item.first === null).length;
if (untimed) { y += 22; text(M, y, `另有 ${untimed} 个问题缺少记录时间，未放上时间轴；请在工作台读取原始记录。`, {size: 12, color: C.muted}); }
y += 30;
writeFileSync(out, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${y}" width="${W}" height="${y}" font-family="Noto Sans SC, PingFang SC, sans-serif"><rect x="0" y="0" width="${W}" height="${y}" fill="#FFFFFF"/>${s.join("")}</svg>`);
console.log(out, W, y, "routes", lanes.length, "singles", singles.length, "decisions", t.decisions.length);

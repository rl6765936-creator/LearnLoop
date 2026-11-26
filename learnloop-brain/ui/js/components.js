// Shared visual building blocks. Every view uses these, so the same thing always looks the same.
import { h, s } from "./dom.js";
import { identityFor, SHAPES } from "./identity.js";
import { masteryLabel, STATE_LABELS } from "./format.js";

let identities = new Map();
export function setIdentities(map) { identities = map; }
export function identityOf(id, name) { return identities.get(id) || identityFor(id, name); }

// CSS variables that tint an element in the subject's colors (base, ink; tint is derived in CSS)
export function subjectVars(identity) {
  return { "--s-base": identity.base, "--s-ink": identity.ink };
}

// The subject's signature: its shape, filled with its color, drawn in its ink
export function emblem(identity, size = 32, extra = {}) {
  const [body, detail] = SHAPES[identity.shape] || SHAPES.hexagon;
  const w = size < 28 ? 2.4 : size < 56 ? 1.7 : 1.3;
  return s("svg", { viewBox: "0 0 48 48", width: size, height: size, class: "emblem", "aria-hidden": "true",
    style: subjectVars(identity), ...extra },
    s("path", { d: body, class: "emblem-body", "stroke-width": w, "stroke-linejoin": "round" }),
    s("path", { d: detail, class: "emblem-detail", "stroke-width": w * 0.75, "stroke-linejoin": "round", "stroke-linecap": "round" }));
}

// A small square tile holding the emblem on the subject tint
export function emblemTile(identity, size = 44, cls = "") {
  return h("span", { class: `emblem-tile ${cls}`, style: { ...subjectVars(identity), "--tile": `${size}px` } },
    emblem(identity, Math.round(size * 0.62)));
}

// Knowledge-state glyph: shape + color, so the state never depends on color alone
export function stateGlyph(state, size = 14) {
  const c = { cx: 8, cy: 8 };
  const parts = {
    understood: [s("circle", { ...c, r: 6.5, class: "g-fill" })],
    learning: [s("circle", { ...c, r: 6, class: "g-line" }), s("path", { d: "M8 2 A6 6 0 0 0 8 14 Z", class: "g-fill" })],
    reinforce: [s("circle", { ...c, r: 6, class: "g-line" }), s("circle", { ...c, r: 2.3, class: "g-fill" })],
    new: [s("circle", { ...c, r: 6, class: "g-line g-dash" })],
  }[state] || [s("circle", { ...c, r: 6, class: "g-line g-dash" })];
  return s("svg", { viewBox: "0 0 16 16", width: size, height: size, class: `glyph st-${state}`, "aria-hidden": "true" }, ...parts);
}

export function stateChip(state, assessment) {
  return h("span", { class: `state-chip st-${state}` }, stateGlyph(state, 12), masteryLabel(state, assessment));
}

// Ribbon: one segment per topic (or per count), colored by state, in a fixed order
const ORDER = ["understood", "learning", "reinforce", "new"];
export function stateRibbon(counts, label) {
  const total = ORDER.reduce((n, k) => n + (counts[k] || 0), 0);
  if (!total) return h("div", { class: "ribbon empty", role: "img", "aria-label": "עדיין אין נושאים" });
  return h("div", { class: "ribbon", role: "img", "aria-label": label || ribbonLabel(counts) },
    ORDER.filter((k) => counts[k]).map((k) => h("span", { class: `seg st-${k}`, style: { "flex-grow": String(counts[k]) } })));
}
export function ribbonLabel(counts) {
  return ORDER.filter((k) => counts[k]).map((k) => `${counts[k]} ${k === "new" ? "עדיין בלי ראיות" : STATE_LABELS[k]}`).join(", ");
}
export function stateLegend(counts) {
  return h("div", { class: "legend" }, ORDER.map((k) => h("span", {}, stateGlyph(k, 12),
    `${k === "new" ? "עדיין בלי ראיות" : STATE_LABELS[k]} ${counts[k] || 0}`)));
}
export function countStates(topics) {
  const c = { understood: 0, learning: 0, reinforce: 0, new: 0 };
  for (const t of topics || []) c[t.state] = (c[t.state] || 0) + 1;
  return c;
}

// Progress ring for an estimated mastery between 0 and 1 (null = not enough evidence)
export function masteryRing(value, size = 72) {
  const r = 30, circ = 2 * Math.PI * r;
  const pct = value === null || value === undefined ? null : Math.round(value * 100);
  const arc = s("circle", { cx: 36, cy: 36, r, class: "ring-value", "stroke-dasharray": `${circ.toFixed(1)} ${circ.toFixed(1)}`,
    "stroke-dashoffset": circ.toFixed(1), transform: "rotate(-90 36 36)", "data-target": pct === null ? "" : String(circ * (1 - pct / 100)) });
  return h("span", { class: "ring", role: "img", style: { "--size": `${size}px` },
    "aria-label": pct === null ? "עוד אין מספיק ראיות לציון שליטה" : `שליטה משוערת ${pct} אחוז` },
    s("svg", { viewBox: "0 0 72 72", width: size, height: size, "aria-hidden": "true" },
      s("circle", { cx: 36, cy: 36, r, class: "ring-track" }), pct === null ? null : arc),
    h("span", { class: "ring-label" }, pct === null ? "—" : `${pct}%`));
}

export function bar(value, cls = "") {
  const pct = Math.max(0, Math.min(100, Math.round((value || 0) * 100)));
  return h("span", { class: `bar ${cls}`, role: "img", "aria-label": `${pct}%` },
    h("span", { class: "bar-fill", "data-width": `${pct}%` }));
}

export function brandMark(size = 34) {
  return s("svg", { viewBox: "0 0 44 28", width: size, height: Math.round(size * 28 / 44), class: "brand-mark", "aria-hidden": "true" },
    s("ellipse", { cx: 15, cy: 14, rx: 12, ry: 11, class: "loop-gold" }),
    s("ellipse", { cx: 29, cy: 14, rx: 12, ry: 11, class: "loop-ink" }));
}

// Stroke icons, one family (1.6 px, round joins)
const ICONS = {
  home: "M4 11 L12 4 L20 11 M6 10 V20 H18 V10",
  decisions: "M5 12 L10 17 L19 7",
  history: "M12 7 V12 L15 14 M4 12 A8 8 0 1 0 6.3 6.3 M4 4 V8 H8",
  system: "M5 6 H19 V16 H5 Z M9 20 H15 M12 16 V20",
  settings: "M4 7 H14 M18 7 H20 M4 17 H8 M12 17 H20 M14 5 V9 M8 15 V19",
  spark: "M12 4 C12.6 9 15 11.4 20 12 C15 12.6 12.6 15 12 20 C11.4 15 9 12.6 4 12 C9 11.4 11.4 9 12 4 Z",
  check: "M5 12 L10 17 L19 7",
  close: "M6 6 L18 18 M18 6 L6 18",
  edit: "M5 19 L9 18 L19 8 L16 5 L6 15 Z",
  info: "M12 11 V17 M12 7 V8",
};
export function icon(name, size = 18) {
  return s("svg", { viewBox: "0 0 24 24", width: size, height: size, class: "icon", "aria-hidden": "true" },
    s("path", { d: ICONS[name] || ICONS.info }));
}

export function emptyState(title, text, action) {
  return h("div", { class: "empty" }, h("span", { class: "empty-mark" }, brandMark(40)),
    h("p", { class: "empty-title" }, title), text ? h("p", { class: "muted" }, text) : null, action || null);
}

export function skeleton(lines = 3) {
  return h("div", { class: "card skeleton", "aria-busy": "true", "aria-label": "טוען" },
    Array.from({ length: lines }, (_, i) => h("span", { class: "sk-line", style: { width: `${90 - i * 18}%` } })));
}

// Section heading with an optional quiet action on the other side
export function sectionHead(title, action, level = "h2") {
  return h("div", { class: "section-head" }, h(level, {}, title), action || null);
}

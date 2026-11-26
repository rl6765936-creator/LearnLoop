// Subject visual identity: a color, a geometric shape, a light tint and a dark ink per subject.
// Decision 25.11.2026: computed in the UI only, deterministically, from the subject's stable id.
// No database or server change. Pure functions – tested in ui/tests.
//
// How it works:
//  1. The first ten slots belong to the subjects named in the design brief (Python, C#, ...). A subject
//     whose name matches one of them takes that slot.
//  2. Any other subject prefers a slot from its id hash (FNV-1a), starting in the extended slots.
//  3. Collisions: subjects are processed in id order (subj_1, subj_2, ... – ids only grow), and a taken
//     slot moves the newcomer to the next free one. A new subject is always processed last, so it can
//     never change the identity of an existing subject.
//  4. More than 16 subjects: colors repeat, but the shape comes from a second hash, so the pair stays
//     distinguishable.

export const PALETTE = [
  { key: "sage", base: "#A8CFC4", ink: "#3F6E62", shape: "hexagon" },
  { key: "lavender", base: "#BDB0DE", ink: "#5A4C8A", shape: "diamond" },
  { key: "champagne", base: "#D6B779", ink: "#7D6030", shape: "star" },
  { key: "powder", base: "#AFC7E3", ink: "#3E5F86", shape: "cube" },
  { key: "pistachio", base: "#C3D6A5", ink: "#566B34", shape: "circle" },
  { key: "mist", base: "#A7C9D5", ink: "#356575", shape: "cylinder" },
  { key: "coral", base: "#E9AFA6", ink: "#9A4A3E", shape: "triangle" },
  { key: "butter", base: "#E8D18E", ink: "#7A6420", shape: "split" },
  { key: "aqua", base: "#9FCEDB", ink: "#2F6B7A", shape: "orbit" },
  { key: "plum", base: "#B6A6C9", ink: "#5E4D74", shape: "layers" },
  // extended pool for subjects discovered later
  { key: "apricot", base: "#EBC09B", ink: "#8A5A2E", shape: "pentagon" },
  { key: "fig", base: "#CBA9B5", ink: "#7A4A5C", shape: "octagon" },
  { key: "olive", base: "#C8C9A0", ink: "#5C5E32", shape: "square" },
  { key: "slate", base: "#A9AFCC", ink: "#4A5280", shape: "arch" },
  { key: "clay", base: "#D9B8A8", ink: "#87503E", shape: "drop" },
  { key: "celadon", base: "#B8D3B0", ink: "#4C6B44", shape: "cross" },
];
const FIRST_EXTENDED = 10;

// Names (lower case, spaces and punctuation removed) of the subjects in the approved initial mapping.
const KNOWN = [
  ["python"],
  ["c#", "csharp", "c sharp", "סישארפ"],
  ["ai", "artificialintelligence", "בינהמלאכותית", "machinelearning", "למידתמכונה"],
  ["datastructures", "מבנינתונים"],
  ["numpy", "datascience", "מדעיהנתונים", "numpyומדעיהנתונים"],
  ["sql", "databases", "מסדינתונים", "sqlומסדינתונים"],
  ["web", "webdevelopment", "פיתוחweb", "פיתוחאתרים", "html", "css"],
  ["javascript", "js"],
  ["react"],
  ["softwarearchitecture", "ארכיטקטורתתוכנה", "architecture"],
];

export function normalizeName(name) {
  const s = String(name || "").trim().toLowerCase();
  return s === "c#" || s === "c #" ? "c#" : s.replace(/[\s\-_.,:;'"()׳״]+/g, "");
}

export function knownSlot(name) {
  const n = normalizeName(name);
  const i = KNOWN.findIndex((names) => names.includes(n));
  return i === -1 ? null : i;
}

export function hash(text) {                       // FNV-1a, 32 bit
  let h = 0x811c9dc5;
  for (const ch of String(text)) { h ^= ch.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}

function idOrder(a, b) {                           // subj_2 before subj_10
  const na = Number(String(a).match(/(\d+)$/)?.[1]), nb = Number(String(b).match(/(\d+)$/)?.[1]);
  if (Number.isFinite(na) && Number.isFinite(nb) && na !== nb) return na - nb;
  return String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0;
}

function make(slot, id, repeat) {
  const p = PALETTE[slot];
  const shape = repeat ? SHAPE_NAMES[hash(`shape:${id}`) % SHAPE_NAMES.length] : p.shape;
  return { slot, key: p.key, base: p.base, ink: p.ink, shape };
}

// subjects: [{id, name}] -> Map(id -> identity). Same input set always gives the same result.
export function assignIdentities(subjects) {
  const out = new Map();
  const used = new Set();
  const list = [...subjects].sort((a, b) => idOrder(a.id, b.id));
  for (const s of list) {
    const known = knownSlot(s.name);
    if (known !== null && !used.has(known)) { used.add(known); out.set(s.id, make(known, s.id, false)); continue; }
    const n = PALETTE.length, ext = n - FIRST_EXTENDED;
    const order = [];
    const startExt = FIRST_EXTENDED + (hash(s.id) % ext);
    for (let i = 0; i < ext; i++) order.push(FIRST_EXTENDED + ((startExt - FIRST_EXTENDED + i) % ext));
    const start = hash(s.id) % FIRST_EXTENDED;
    for (let i = 0; i < FIRST_EXTENDED; i++) order.push((start + i) % FIRST_EXTENDED);
    const free = order.find((slot) => !used.has(slot));
    if (free !== undefined) { used.add(free); out.set(s.id, make(free, s.id, false)); }
    else out.set(s.id, make(hash(s.id) % n, s.id, true));
  }
  return out;
}

// One subject without the others (e.g. before the list has loaded): same rules, no collision check.
export function identityFor(id, name) {
  const known = knownSlot(name);
  if (known !== null) return make(known, id, false);
  return make(FIRST_EXTENDED + (hash(id) % (PALETTE.length - FIRST_EXTENDED)), id, false);
}

// Geometry of the signature shapes, 48x48 view box: [body, inner detail]
export const SHAPES = {
  hexagon: ["M24 4 L41.3 14 L41.3 34 L24 44 L6.7 34 L6.7 14 Z", "M24 13 L33.5 18.5 L33.5 29.5 L24 35 L14.5 29.5 L14.5 18.5 Z"],
  diamond: ["M24 4 L42 24 L24 44 L6 24 Z", "M6 24 H42 M24 4 L17 24 L24 44 M24 4 L31 24 L24 44"],
  star: ["M24 4 C25.6 16.2 31.8 22.4 44 24 C31.8 25.6 25.6 31.8 24 44 C22.4 31.8 16.2 25.6 4 24 C16.2 22.4 22.4 16.2 24 4 Z", "M24 19 L25.8 24 L24 29 L22.2 24 Z"],
  cube: ["M24 5 L41 14.5 L41 33.5 L24 43 L7 33.5 L7 14.5 Z", "M7 14.5 L24 24 L41 14.5 M24 24 V43"],
  circle: ["M24 5 A19 19 0 1 1 23.99 5 Z", "M24 13 A11 11 0 1 1 23.99 13"],
  cylinder: ["M8 12 C8 5 40 5 40 12 V36 C40 43 8 43 8 36 Z", "M8 12 C8 19 40 19 40 12 M8 24 C8 31 40 31 40 24"],
  triangle: ["M24 5 L44 41 H4 Z", "M24 18 L33 34 H15 Z"],
  split: ["M24 5 A19 19 0 1 1 23.99 5 Z", "M24 5 C34 14 14 34 24 43"],
  orbit: ["M24 18.5 A5.5 5.5 0 1 1 23.99 18.5 Z", "M4 24 C4 15 44 15 44 24 C44 33 4 33 4 24 Z M24 4 C33 4 33 44 24 44 C15 44 15 4 24 4 Z"],
  layers: ["M24 7 L42 15.5 L24 24 L6 15.5 Z", "M6 24 L24 32.5 L42 24 M6 32.5 L24 41 L42 32.5"],
  pentagon: ["M24 4 L43 18 L36 42 H12 L5 18 Z", "M24 14 L33.5 21 L30 33 H18 L14.5 21 Z"],
  octagon: ["M16 5 H32 L43 16 V32 L32 43 H16 L5 32 V16 Z", "M24 14 V34 M14 24 H34"],
  square: ["M9 9 H39 V39 H9 Z", "M17 17 H31 V31 H17 Z"],
  arch: ["M8 42 V22 A16 16 0 0 1 40 22 V42 Z", "M16 42 V24 A8 8 0 0 1 32 24 V42"],
  drop: ["M24 4 C32 16 40 24 40 31 A16 16 0 0 1 8 31 C8 24 16 16 24 4 Z", "M24 22 C28 27 31 30 31 33 A7 7 0 0 1 17 33 C17 30 20 27 24 22 Z"],
  cross: ["M18 5 H30 V18 H43 V30 H30 V43 H18 V30 H5 V18 H18 Z", "M24 14 V34 M14 24 H34"],
};
const SHAPE_NAMES = Object.keys(SHAPES);

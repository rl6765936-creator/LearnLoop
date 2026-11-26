// Motion with meaning. Everything here is off when the system asks for reduced motion,
// and the cursor trail is off on touch screens.
const reduced = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const finePointer = () => window.matchMedia?.("(hover: hover) and (pointer: fine)").matches;

// ---------- gold sparkle trail: one fixed canvas, never intercepts clicks ----------
export function startSparkles() {
  if (reduced() || !finePointer()) return () => {};
  const canvas = document.createElement("canvas");
  canvas.className = "sparkle-layer";
  canvas.setAttribute("aria-hidden", "true");
  document.body.append(canvas);
  const ctx = canvas.getContext("2d");
  let parts = [], raf = 0, prev = 0, last = null, dpr = 1;
  const fit = () => {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = innerWidth * dpr; canvas.height = innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  const step = (now) => {
    const dt = Math.min(now - prev, 40); prev = now;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    parts = parts.filter((p) => (p.t += dt) < p.life);
    for (const p of parts) {
      p.x += p.vx * dt * 0.06; p.y += p.vy * dt * 0.06;
      const k = 1 - p.t / p.life, a = k * k * 0.9, r = p.s * (0.55 + 0.45 * k);
      ctx.save(); ctx.translate(p.x, p.y);
      ctx.shadowColor = `rgba(${p.c},${a})`; ctx.shadowBlur = 7; ctx.fillStyle = `rgba(${p.c},${a})`;
      ctx.beginPath(); ctx.moveTo(0, -r * 2);
      ctx.quadraticCurveTo(0, 0, r * 2, 0); ctx.quadraticCurveTo(0, 0, 0, r * 2);
      ctx.quadraticCurveTo(0, 0, -r * 2, 0); ctx.quadraticCurveTo(0, 0, 0, -r * 2);
      ctx.fill(); ctx.restore();
    }
    raf = parts.length ? requestAnimationFrame(step) : 0;     // the loop stops when nothing is left
  };
  const move = (e) => {
    if (e.pointerType !== "mouse") return;
    const x = e.clientX, y = e.clientY;
    if (last) { const dx = x - last.x, dy = y - last.y; if (dx * dx + dy * dy < 150) return; }
    last = { x, y };
    if (parts.length > 34) parts.shift();
    parts.push({ x: x + (Math.random() - 0.5) * 8, y: y + (Math.random() - 0.5) * 8, vx: (Math.random() - 0.5) * 0.3,
      vy: 0.12 + Math.random() * 0.3, s: 1.8 + Math.random() * 2.2, t: 0, life: 600 + Math.random() * 400,
      c: Math.random() < 0.6 ? "201,166,106" : "226,200,150" });
    if (!raf) { prev = performance.now(); raf = requestAnimationFrame(step); }
  };
  fit();
  addEventListener("resize", fit, { passive: true });
  addEventListener("pointermove", move, { passive: true });
  return () => {                                             // full clean-up
    cancelAnimationFrame(raf);
    removeEventListener("resize", fit); removeEventListener("pointermove", move);
    canvas.remove();
  };
}

// ---------- progress that moves from its previous value to the new one ----------
const lastValues = new Map();
export function animateProgress(root) {
  for (const el of root.querySelectorAll("[data-width]")) {
    const key = el.closest("[data-key]")?.dataset.key, target = el.dataset.width;
    const from = key && lastValues.has(key) ? lastValues.get(key) : "0%";
    if (key) lastValues.set(key, target);
    if (reduced() || from === target) { el.style.setProperty("width", target); continue; }
    el.style.setProperty("width", from);
    requestAnimationFrame(() => requestAnimationFrame(() => el.style.setProperty("width", target)));
  }
  for (const el of root.querySelectorAll(".ring-value[data-target]")) {
    const key = el.closest("[data-key]")?.dataset.key, target = el.getAttribute("data-target");
    const full = el.getAttribute("stroke-dashoffset");
    const from = key && lastValues.has(`ring:${key}`) ? lastValues.get(`ring:${key}`) : full;
    if (key) lastValues.set(`ring:${key}`, target);
    if (reduced() || from === target) { el.setAttribute("stroke-dashoffset", target); continue; }
    el.setAttribute("stroke-dashoffset", from);
    requestAnimationFrame(() => requestAnimationFrame(() => el.setAttribute("stroke-dashoffset", target)));
  }
}

// ---------- knowledge events: a topic seen for the first time, a topic that became 'understood' ----------
const seenTopics = new Map();          // subject id -> Map(topic id -> state)
export function topicEvents(subjectId, topics) {
  const before = seenTopics.get(subjectId);
  const now = new Map(topics.map((t) => [t.id, t.state]));
  seenTopics.set(subjectId, now);
  if (!before) return { fresh: new Set(), completed: new Set() };   // first visit: nothing is "new"
  const fresh = new Set(), completed = new Set();
  for (const [id, state] of now) {
    if (!before.has(id)) fresh.add(id);
    else if (state === "understood" && before.get(id) !== "understood") completed.add(id);
  }
  return { fresh, completed };
}

// A short gold halo, once
export function glow(el) {
  if (!el || reduced()) return;
  el.classList.remove("is-glowing");
  void el.getBoundingClientRect();
  el.classList.add("is-glowing");
  setTimeout(() => el.classList.remove("is-glowing"), 1400);
}

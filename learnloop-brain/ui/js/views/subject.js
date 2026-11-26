import { h, s } from "../dom.js";
import { bar, countStates, emblemTile, emptyState, identityOf, masteryRing, sectionHead, skeleton, stateChip,
  stateGlyph, stateLegend, stateRibbon, subjectVars } from "../components.js";
import { topicEvents } from "../effects.js";
import { ASSESS_LABELS, confidenceLabel, historyLabel, masteryLabel, percent, periodLabel, studyLabel } from "../format.js";

const TRACK_STEPS = { placement: "בחן רחב ראשוני", track_project: "פעילות לבדיקת הבנה ויישום",
  refresh: "רענון שבועי", waiting: "הרענון הבא" };
const MODE = { user: "החלטה שלך", approved: "באישורך" };

function trackCard(d, actions) {
  const t = d.completed_track;
  const sid = d.subject_id;
  return h("div", { class: "card tinted" },
    h("h3", {}, "חומר שכבר למדת"),
    h("p", { class: "muted" }, `${t.topics} נושאים מחומר שהסתיים · ${t.not_assessed} טרם הוערכו. `
      + "סיום קורס לא נחשב הבנה – רק התשובות שלך."),
    t.step === "waiting"
      ? h("p", {}, `${TRACK_STEPS.waiting}: ${t.next_refresh_at.slice(0, 10)}`)
      : h("div", { class: "row" },
          h("span", {}, `השלב הבא: ${TRACK_STEPS[t.step]}`),
          h("button", { class: "btn", onclick: () => actions.startTrack(sid) }, "להתחיל"),
          t.step === "refresh" ? h("button", { class: "btn ghost", onclick: () => actions.startTrack(sid, "game") }, "משחק במקום") : null,
          h("span", { class: "muted small" }, "אפשר גם לדלג – בלי השפעה על ההתקדמות")));
}

// Knowledge map: topics placed in learning order (prerequisites first, on the right), edges from skill_map
function knowledgeMap(d, events, actions) {
  const byName = new Map(d.topics.map((t) => [t.name, t]));
  const edges = d.skill_map.filter((e) => e.kind !== "partOf" && byName.has(e.from) && byName.has(e.to));
  if (!d.topics.length) return emptyState("עוד אין נושאים", "הם יופיעו אחרי שהחומר הראשון יעובד.");
  const level = new Map(d.topics.map((t) => [t.name, 0]));
  for (let pass = 0; pass < d.topics.length; pass++) {        // longest prerequisite chain; bounded, so cycles cannot hang
    let moved = false;
    for (const e of edges) if (e.kind === "prerequisite" && level.get(e.to) < level.get(e.from) + 1) { level.set(e.to, level.get(e.from) + 1); moved = true; }
    if (!moved) break;
  }
  const columns = [];
  for (const t of d.topics) (columns[level.get(t.name)] ||= []).push(t);
  const colW = 210, rowH = 66, pad = 28;
  const rows = Math.max(...columns.map((c) => (c ? c.length : 0)));
  const width = Math.max(columns.length * colW, 320), height = rows * rowH + pad * 2;
  const pos = new Map();
  columns.forEach((col, li) => (col || []).forEach((t, ri) => {
    const offset = ((rows - col.length) * rowH) / 2;
    pos.set(t.name, { x: width - (li + 0.5) * colW, y: pad + offset + (ri + 0.5) * rowH });
  }));
  const paths = edges.map((e) => {
    const a = pos.get(e.from), b = pos.get(e.to), m = (a.x + b.x) / 2;
    const done = byName.get(e.from).state === "understood" && byName.get(e.to).state === "understood";
    return s("path", { class: `edge${done ? " done" : ""}${e.kind === "related" ? " related" : ""}`,
      d: `M${a.x} ${a.y} C${m} ${a.y} ${m} ${b.y} ${b.x} ${b.y}` });
  });
  return h("div", { class: "map-scroll" },
    h("div", { class: "kmap", style: { width: `${width}px`, height: `${height}px` } },
      s("svg", { class: "edges", width, height, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" }, paths),
      d.topics.map((t) => {
        const p = pos.get(t.name);
        return h("button", { class: `knode st-${t.state}${events.fresh.has(t.id) ? " is-new" : ""}`,
          style: { left: `${p.x}px`, top: `${p.y}px` }, "data-glow": events.completed.has(t.id) ? "" : null,
          title: `${t.name}: ${masteryLabel(t.state, t.assessment)}`, "aria-label": `${t.name}, ${masteryLabel(t.state, t.assessment)}. היסטוריה`,
          onclick: () => actions.showHistory(t.id, t.name) },
          stateGlyph(t.state, 14), h("bdi", {}, t.name),
          events.fresh.has(t.id) ? h("span", { class: "chip gold" }, "חדש") : null);
      })));
}

function topicRow(d, t, events, actions) {
  return h("div", { class: `topic${events.fresh.has(t.id) ? " is-new" : ""}`, id: `topic-${t.id}`, "data-key": `topic:${t.id}` },
    h("div", { class: "title" }, stateChip(t.state, t.assessment), h("bdi", { class: "name" }, t.name)),
    h("div", { class: "score", title: percent(t.score) }, bar(t.score), h("span", {}, percent(t.score))),
    h("div", { class: "meta" }, `${t.evidence} ראיות · ${studyLabel(t.study.summary)}, ${ASSESS_LABELS[t.assessment] || ""}`,
      d.show_confidence && confidenceLabel(t.confidence) ? `, ${confidenceLabel(t.confidence)}` : ""),
    h("div", { class: "actions" },
      h("button", { class: "btn small", onclick: () => actions.startActivity(t.id, "quiz", "learn") }, "בוחן קצר"),
      h("button", { class: "btn ghost small", onclick: () => actions.showHistory(t.id, t.name) }, "היסטוריה")));
}

function jump(targets) {
  return h("nav", { class: "jump", "aria-label": "אזורי המקצוע" }, targets.map(([label, id]) =>
    h("button", { type: "button", onclick: () => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }) }, label)));
}

export function subjectView(state, actions) {
  const d = state.dashboard;
  if (!d) return h("section", {}, skeleton(2), skeleton(4));
  const id = identityOf(d.subject_id, d.subject);
  const events = topicEvents(d.subject_id, d.topics);
  const counts = countStates(d.topics);
  return h("section", { style: subjectVars(id) },
    h("div", { class: "space-head" }, h("div", { class: "inner" },
      h("div", { class: "space-title" },
        emblemTile(id, 92, "on-white"),
        h("div", {},
          h("h1", {}, h("bdi", {}, d.subject)),
          h("div", { class: "chips" },
            d.periods.length ? d.periods.map((p) => h("span", { class: "chip subject" }, periodLabel(p))) : h("span", { class: "chip" }, "תקופה: לא ידוע"),
            d.indicator === "red" ? h("span", { class: "chip alert" }, h("span", { class: "red-indicator" }), "בוחן שלא הושלם") : null))),
      h("div", { class: "space-summary", "data-key": `mastery:${d.subject_id}` },
        masteryRing(d.estimated_mastery, 76),
        h("div", { class: "stack", style: { gap: "4px" } },
          h("span", { class: "muted small" }, d.estimated_mastery === null || d.estimated_mastery === undefined
            ? "עוד אין מספיק ראיות לציון שליטה" : "הערכת שליטה, לפי הראיות"),
          h("span", { class: "small" }, `${counts.understood} הובנו, ${counts.learning} בלמידה, ${counts.reinforce} לחיזוק`),
          d.assessment_model === "provisional" ? h("span", { class: "status-label" }, "מודל הערכה זמני – לא נוסחה מאושרת") : null,
          d.assessment_model === "bayesian" ? h("span", { class: "status-label" }, "נוסחה מאושרת") : null)))),
    jump([["סקירה", "sec-overview"], ["מפת ידע", "sec-map"], ["נושאים", "sec-topics"], ["התפתחות", "sec-timeline"]]),
    h("div", { class: "card", id: "sec-overview" },
      sectionHead("סקירה", null, "h2"),
      stateRibbon(counts),
      stateLegend(counts),
      d.work_minutes_7d
        ? h("p", { class: "muted small" }, `זמן עבודה ב-7 הימים האחרונים (לפי ActivityWatch): ${Math.round(d.work_minutes_7d / 6) / 10} שעות – זמן אינו ראיה להבנה.`)
        : null),
    d.completed_track ? trackCard(d, actions) : null,
    h("div", { class: "card", id: "sec-map" },
      sectionHead("מפת הידע", h("span", { class: "muted small" }, "סדר הלמידה מימין לשמאל · קו מלא: דרישת קדם · מקווקו: קשור"), "h2"),
      knowledgeMap(d, events, actions)),
    state.history ? h("div", { class: "card tinted", id: "topic-history" },
      sectionHead(`היסטוריה: ${state.history.name}`, null, "h3"),
      h("ol", { class: "timeline" }, state.history.items.map((r) =>
        h("li", {}, h("span", { class: "dot" }),
          h("div", { class: "stack", style: { gap: "2px" } },
            h("span", {}, historyLabel(r.entity, r.action), r.reason ? ` – ${r.reason}` : ""),
            h("span", { class: "muted small" }, `${r.at.slice(0, 16).replace("T", " ")} · ${MODE[r.mode] || "אוטומטי"}`)))))) : null,
    h("div", { class: "split" },
      h("div", { class: "main" },
        h("div", { class: "card", id: "sec-topics" },
          sectionHead("נושאים", null, "h2"),
          d.topics.length ? d.topics.map((t) => topicRow(d, t, events, actions))
            : emptyState("עוד אין נושאים", "הם יופיעו אחרי שהחומר הראשון יעובד."))),
      h("aside", { class: "side" },
        d.strengths && d.strengths.length
          ? h("div", { class: "card quiet" }, h("h3", {}, "חוזקות (לפי הראיות)"), d.strengths.map((x) => h("div", { class: "strength" }, x.text)))
          : null,
        d.project_progress && d.project_progress.projects
          ? h("div", { class: "card", "data-key": `project:${d.subject_id}` }, h("h3", {}, "התקדמות בפרויקט"),
              h("p", {}, d.project_progress.last_title || ""),
              bar(d.project_progress.items_total ? d.project_progress.items_answered / d.project_progress.items_total : 0),
              h("p", { class: "muted small" }, `${d.project_progress.finished}/${d.project_progress.projects} הושלמו · `
                + `${d.project_progress.items_answered}/${d.project_progress.items_total} שאלות נענו`))
          : null,
        h("div", { class: "card", id: "sec-timeline" },
          h("h3", {}, "התפתחות"),
          (d.timeline || []).length
            ? h("ol", { class: "timeline" }, d.timeline.slice(-6).reverse().map((r) => {
                const [entity, action] = String(r.what).split(" ");
                return h("li", {}, h("span", { class: "dot" }), h("div", { class: "stack", style: { gap: "2px" } },
                  h("span", { class: "small" }, historyLabel(entity, action)),
                  r.why ? h("span", { class: "muted small" }, r.why) : null));
              }))
            : h("p", { class: "muted" }, "עדיין אין שינויים.")),
      )),
    h("div", { class: "card quiet" },
      h("h3", {}, "מפת מיומנויות"),
      d.skill_map.length ? h("ul", {}, d.skill_map.map((e) => h("li", {}, h("bdi", {}, e.from), " ← נדרש לפני ← ", h("bdi", {}, e.to))))
        : h("p", { class: "muted" }, "עדיין אין קשרים.")));
}

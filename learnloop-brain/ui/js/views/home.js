import { h, s } from "../dom.js";
import { countStates, emblem, emblemTile, emptyState, identityOf, ribbonLabel, sectionHead, stateRibbon, subjectVars } from "../components.js";
import { greeting, KIND_LABELS, longDate, percent, studyLabel, TYPE_LABELS } from "../format.js";

function subjectOf(state, topicId) {
  for (const sub of state.subjects) for (const t of (sub.topicsCache || [])) if (t.id === topicId) return { sub, topic: t };
  return null;
}

// One short sentence built only from what the system knows right now
function summaryLine(state) {
  const n = state.subjects.length;
  const topics = state.subjects.reduce((sum, x) => sum + (x.topics || 0), 0);
  const waiting = state.approvals.length + (state.classifications || []).length;
  if (!n) return "עוד אין מקצועות. כשחומר הלימוד הראשון יעובד, הם יופיעו כאן.";
  const parts = [`${n} ${n === 1 ? "מקצוע" : "מקצועות"} ו־${topics} ${topics === 1 ? "נושא" : "נושאים"} במעקב.`];
  if (waiting) parts.push(`${waiting} ${waiting === 1 ? "החלטה ממתינה" : "החלטות ממתינות"} לך.`);
  return parts.join(" ");
}

function nextCard(state, actions) {
  const next = state.next;
  if (!next) {
    return h("div", { class: "card next-card" },
      h("span", { class: "eyebrow" }, "הצעד הבא"),
      emptyState("אין כרגע הצעה", "כשיתווסף חומר לימוד, תופיע כאן הצעה קצרה אחת."));
  }
  const found = subjectOf(state, next.topic_id);
  const sub = found ? found.sub : state.subjects.find((x) => x.id === next.subject_id);
  const id = sub ? identityOf(sub.id, sub.name) : null;
  const what = next.action === "continue" ? "להמשיך את הפעילות" : (TYPE_LABELS[next.activity_type] || next.activity_type);
  return h("div", { class: "card next-card", style: id ? subjectVars(id) : {} },
    id ? h("span", { class: "watermark" }, emblem(id, 200)) : null,
    h("span", { class: "eyebrow" }, "הצעד הבא המומלץ"),
    h("div", { class: "title-row" },
      id ? emblemTile(id, 56) : null,
      h("div", { class: "stack" },
        h("h2", {}, what, found ? [" · ", h("bdi", {}, found.topic.name)] : ""),
        sub ? h("span", { class: "muted small" }, h("bdi", {}, sub.name)) : null,
        h("p", {}, next.reason))),
    h("div", { class: "row actions" },
      h("button", { class: "btn", onclick: () => actions.startRecommended() }, next.action === "continue" ? "להמשיך" : "להתחיל"),
      h("span", { class: "muted small" }, "אפשר גם לדלג – זה לא משפיע על ההתקדמות.")));
}

// The loop: every active subject on one orbit (visual summary, opens the subject)
function loopCard(state, actions) {
  const subs = state.subjects.slice(0, 8);
  const cx = 50, cy = 52, rx = 38, ry = 36;
  return h("div", { class: "card quiet loop-card" },
    h("h3", {}, "הלולאה שלך"),
    h("p", { class: "muted small" }, "לומדים, בודקים, מחזקים. לחיצה על מקצוע פותחת את המרחב שלו."),
    subs.length
      ? h("div", { class: "orbit" },
          s("svg", { class: "track", viewBox: "0 0 100 100", preserveAspectRatio: "none", "aria-hidden": "true" },
            s("ellipse", { class: "outer", cx, cy, rx, ry }), s("ellipse", { class: "inner", cx, cy, rx: 24, ry: 22 })),
          subs.map((sub, i) => {
            const a = -Math.PI / 3 + (i * 2 * Math.PI) / subs.length;
            const id = identityOf(sub.id, sub.name);
            return h("button", { class: "orbit-node", style: { ...subjectVars(id), left: `${cx - rx * Math.cos(a)}%`, top: `${cy + ry * Math.sin(a)}%` },
              onclick: () => actions.openSubject(sub.id), "aria-label": `פתיחת ${sub.name}` },
              emblemTile(id, 42), h("bdi", { class: "label" }, sub.name));
          }),
          h("div", { class: "orbit-center" }, `${subs.length} ${subs.length === 1 ? "מקצוע" : "מקצועות"}`))
      : emptyState("הלולאה עוד ריקה", null));
}

function subjectCard(sub, actions) {
  const id = identityOf(sub.id, sub.name);
  const counts = { understood: 0, learning: 0, reinforce: 0, new: 0, ...(sub.states || countStates(sub.topicsCache)) };
  const period = (sub.periods || [])[0];
  return h("button", { class: "card lift subject-card", style: subjectVars(id), "data-key": `subject:${sub.id}`,
    onclick: () => actions.openSubject(sub.id), "aria-label": `${sub.name}: ${ribbonLabel(counts) || "אין נושאים"}` },
    h("span", { class: "watermark" }, emblem(id, 150)),
    h("span", { class: "head" },
      emblemTile(id, 44),
      h("span", { class: "stack", style: { gap: "0" } },
        h("bdi", { class: "name" }, sub.name),
        period ? h("span", { class: "meta" }, studyLabel(period.study_status)) : null),
      sub.indicator === "red" ? h("span", { class: "red-indicator", title: "בוחן פתוח יותר מיומיים" }) : null,
      h("span", { class: "pct" }, percent(sub.mastery))),
    stateRibbon(counts),
    h("span", { class: "meta" }, `${sub.topics || 0} נושאים`, sub.indicator === "red" ? " · בוחן פתוח יותר מיומיים" : ""));
}

export function homeView(state, actions) {
  const waiting = state.approvals.length + (state.classifications || []).length;
  return h("section", {},
    h("header", { class: "hello" },
      h("span", { class: "date" }, longDate()),
      h("h1", {}, `${greeting()}.`),
      h("p", {}, summaryLine(state))),
    h("div", { class: "hero" }, nextCard(state, actions), loopCard(state, actions)),
    h("div", { class: "split" },
      h("div", { class: "main" },
        h("div", { class: "stack" },
          sectionHead("המקצועות שלך"),
          state.subjects.length
            ? h("div", { class: "subject-grid" }, state.subjects.map((sub) => subjectCard(sub, actions)))
            : h("div", { class: "card" }, emptyState("עוד אין מקצועות", "מקצוע חדש מופיע כאן אחרי שתאשרי אותו."))),
        h("div", { class: "stack" },
          sectionHead("מה השתנה"),
          h("div", { class: "card" },
            state.log.length
              ? h("ol", { class: "timeline" }, state.log.slice(-8).reverse().map((t) => h("li", {}, h("span", { class: "dot" }), h("span", {}, t))))
              : h("p", { class: "muted" }, "עדיין אין שינויים.")))),
      h("aside", { class: "side" },
        h("div", { class: "card" },
          sectionHead("ממתין להחלטתך", waiting ? h("button", { class: "link small", onclick: () => actions.go("approvals") }, "לכל ההחלטות") : null, "h3"),
          waiting
            ? [state.approvals.slice(0, 3).map((a) => h("div", { class: "decision-preview" },
                  h("span", { class: `kind k-${a.kind}` }, KIND_LABELS[a.kind] || a.kind),
                  h("span", {}, a.summary))),
               (state.classifications || []).length
                 ? h("div", { class: "decision-preview" }, h("span", { class: "kind" }, "סיווג חומר"),
                     h("span", {}, `${state.classifications.length} מקצועות ממתינים לסיווג`)) : null,
               h("button", { class: "btn ghost", onclick: () => actions.go("approvals") }, "להחלטות")]
            : h("p", { class: "muted" }, "אין החלטות ממתינות.")),
        state.strengths && state.strengths.length
          ? h("div", { class: "card quiet" }, h("h3", {}, "חוזקות שעלו מהראיות"),
              state.strengths.map((x) => h("div", { class: "strength" }, h("span", {}, x.text))))
          : null,
        state.schedule && state.schedule.pending_files
          ? h("p", { class: "muted small" }, state.schedule.deferred_because_studying
              ? "חומר חדש יעובד אחרי שתסיימי – לא נפריע לך עכשיו."
              : `${state.schedule.pending_files} קבצים ממתינים לעיבוד הקבוע (כל ${state.schedule.interval_hours} שעות). אפשר גם לבקש עיבוד עכשיו.`)
          : null)));
}

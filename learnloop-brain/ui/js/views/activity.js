import { h } from "../dom.js";
import { bar, emblemTile, emptyState, icon, identityOf, stateGlyph } from "../components.js";
import { feedbackClass, percent, stateLabel, TYPE_LABELS } from "../format.js";

function itemInput(item, onAnswer) {
  if (item.kind === "multiple_choice") {
    return h("div", { class: "options", role: "radiogroup", "aria-label": item.prompt }, item.options.map((opt, i) =>
      h("label", { class: "option" },
        h("input", { type: "radio", name: item.id, onchange: () => onAnswer(i) }), h("bdi", {}, opt))));
  }
  if (item.kind === "match") {
    const selects = item.left.map((left) => {
      const sel = h("select", { "aria-label": left }, h("option", { value: "" }, "—"),
        item.right.map((r) => h("option", { value: r }, r)));
      return { left, sel };
    });
    return h("div", { class: "stack" },
      selects.map(({ left, sel }) => h("div", { class: "match-row" }, h("bdi", {}, left), sel)),
      h("div", {}, h("button", { class: "btn ghost", onclick: () => onAnswer(selects.map(({ left, sel }) => [left, sel.value])) }, "לבדוק")));
  }
  const area = h("textarea", { "aria-label": "התשובה שלך", placeholder: "כתבי במילים שלך…" });
  return h("div", { class: "stack" }, area,
    h("div", {}, h("button", { class: "btn ghost", onclick: () => area.value.trim() && onAnswer(area.value) }, "לשלוח")));
}

function subjectTile(state) {
  const sub = state.subjects.find((x) => x.id === state.activitySubject);
  return sub ? emblemTile(identityOf(sub.id, sub.name), 52) : null;
}

export function activityView(state, actions) {
  const a = state.activity;
  if (state.result) {
    const r = state.result;
    return h("section", {},
      h("div", { class: "card next-card" },
        h("div", { class: "result-mark", "data-glow": "" }, subjectTile(state) || h("span", { class: "emblem-tile" }, icon("spark", 26))),
        h("h1", {}, "סיום"),
        h("p", {}, `ענית על ${r.answered} מתוך ${r.total}. ממוצע: ${percent(r.average)}`),
        r.mastery_changes.map((c) => h("div", { class: "stack", style: { gap: "2px" } },
          h("div", { class: "change" }, "עדכון הערכה:",
            h("span", { class: `state-chip st-${c.before.state}` }, stateGlyph(c.before.state, 12), stateLabel(c.before.state)), "←",
            h("span", { class: `state-chip st-${c.after.state}` }, stateGlyph(c.after.state, 12), stateLabel(c.after.state))),
          h("span", { class: "muted small" }, c.reason))),
        r.next ? h("p", {}, h("strong", {}, "הצעד הבא: "), r.next.reason) : null,
        h("div", { class: "row actions" },
          r.next ? h("button", { class: "btn", onclick: () => actions.startRecommended(r.next) }, "להמשיך") : null,
          h("button", { class: "btn ghost", onclick: () => actions.go("home") }, "חזרה"))));
  }
  if (!a) return h("section", {}, h("div", { class: "card" }, emptyState("אין פעילות פתוחה", null,
    h("button", { class: "btn ghost", onclick: () => actions.go("home") }, "חזרה לבית"))));
  const answered = a.items.filter((it) => state.feedback[it.id] || it.answered).length;
  return h("section", {},
    h("header", { class: "activity-head" },
      h("div", { class: "row" }, subjectTile(state), h("span", { class: "eyebrow" }, TYPE_LABELS[a.type] || a.type)),
      h("h1", {}, h("bdi", {}, a.title)),
      h("div", { class: "progress-line", "data-key": `activity:${a.id}` },
        bar(a.items.length ? answered / a.items.length : 0), `${answered} מתוך ${a.items.length}`)),
    a.summary_text ? h("div", { class: "card quiet" }, h("p", {}, a.summary_text)) : null,
    a.code ? h("div", { class: "card" }, h("p", { class: "muted small" }, "הקוד נוצר על ידי AI – לא נחשב כקוד שכתבת."),
      h("pre", { class: "code" }, a.code)) : null,
    h("div", { class: "card" },
      a.items.map((item) => {
        const fb = state.feedback[item.id];
        return h("div", { class: "item" },
          h("p", { class: "prompt" }, item.prompt),
          fb ? null : itemInput(item, (ans) => actions.answer(item.id, ans)),
          fb ? h("div", { class: `feedback ${feedbackClass(fb.score)}` }, fb.feedback) : null,
          fb ? null : h("div", { class: "row" },
            item.has_hint ? h("button", { class: "btn text small", onclick: () => actions.hint(item.id) }, "רמז") : null,
            h("button", { class: "btn text small", onclick: () => actions.solution(item.id) }, "להראות פתרון")));
      })),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => actions.finish() }, "לסיים"),
      h("button", { class: "btn ghost", onclick: () => actions.skip() }, "לדלג (ללא השפעה)")));
}

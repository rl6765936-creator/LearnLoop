import { h } from "../dom.js";
import { emblemTile, emptyState, icon, identityOf, sectionHead } from "../components.js";
import { KIND_LABELS } from "../format.js";

function classificationCards(state, actions) {
  const groups = state.classifications || [];
  if (!groups.length) return null;
  return h("div", { class: "stack" },
    sectionHead("סיווג חומר – לפי מקצוע"),
    h("p", { class: "muted" }, "האם זה חומר שכבר סיימת ללמוד, שאת לומדת עכשיו, או שטרם נלמד? "
      + "החומר ממשיך להיות מעובד כ'לא ידוע' עד שתחליטי, ולא תישאלי שוב על אותו חומר."),
    groups.map((g) => {
      const known = state.subjects.find((x) => x.id === g.subject_id || x.name === g.subject);
      const id = identityOf(known ? known.id : g.subject_id || g.subject, g.subject);
      return h("div", { class: "card" },
        h("div", { class: "row" }, emblemTile(id, 40), h("h3", {}, h("bdi", {}, g.subject))),
        g.items.map((it) => {
          const period = h("input", { type: "text", placeholder: "למשל: שנה א'" });
          const pick = (status) => actions.classify(it.id, status, period.value.trim() || null);
          return h("div", { class: "class-item" },
            h("div", { class: "stack", style: { gap: "2px", flex: "1 1 160px" } },
              h("bdi", { class: "name" }, it.collection || "התיקייה הראשית"),
              h("span", { class: "muted small" }, `${it.files} קבצים`)),
            h("label", { class: "field" }, "תקופה (לא חובה)", period),
            h("div", { class: "row" },
              h("button", { class: "btn", onclick: () => pick("completed") }, "סיימתי ללמוד"),
              h("button", { class: "btn ghost", onclick: () => pick("active") }, "לומדת עכשיו"),
              h("button", { class: "btn ghost", onclick: () => pick("not_studied") }, "טרם נלמד")));
        }));
    }));
}

function decisionCard(a, actions) {
  const input = h("input", { type: "text", placeholder: "תיקון: השם הנכון", "aria-label": "תיקון" });
  const correct = () => input.value.trim() && actions.decide(a.id, "correct", { name: input.value.trim() });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") correct(); });
  return h("div", { class: "card decision" },
    h("div", { class: "row" },
      h("span", { class: `kind k-${a.kind}` }, KIND_LABELS[a.kind] || a.kind),
      a.count > 1 ? h("span", { class: "chip" }, `מ-${a.count} קבצים`) : null),
    h("p", { class: "summary" }, a.summary),
    h("div", { class: "controls" },
      h("button", { class: "btn", onclick: () => actions.decide(a.id, "approve") }, icon("check", 16), "לאשר"),
      h("button", { class: "btn ghost", onclick: () => actions.decide(a.id, "reject") }, "לדחות"),
      h("div", { class: "correct-row" }, input,
        h("button", { class: "btn ghost", onclick: correct }, icon("edit", 16), "לתקן"))));
}

export function approvalsView(state, actions) {
  return h("section", {},
    h("header", { class: "hello" },
      h("h1", {}, "החלטות שממתינות לך"),
      h("p", {}, "המערכת לא משנה דבר בנושאים האלה עד שתחליטי. אין הגבלת זמן.")),
    classificationCards(state, actions),
    h("div", { class: "stack" },
      state.classifications && state.classifications.length ? sectionHead("הצעות לאישור") : null,
      state.approvals.length === 0
        ? h("div", { class: "card" }, emptyState("אין החלטות ממתינות", "כשהמערכת תזהה מקצוע, נושא או שינוי שדורש אישור, הוא יופיע כאן."))
        : state.approvals.map((a) => decisionCard(a, actions))));
}

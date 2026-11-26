import { h } from "../dom.js";
import { skeleton } from "../components.js";

const HOLD = {
  no_provider: "עדיין לא הוגדר ספק AI",
  network_locked: "הרשת נעולה (שני המנעולים סגורים)",
  free_tier_not_for_real_material: "שכבה חינמית – לא לחומר אמיתי",
};

function money(v) { return v === null || v === undefined ? "—" : `$${Number(v).toFixed(2)}`; }

// System state: AI + budget, queue, ActivityWatch time and questions, manual uploads.
export function systemView(state, actions) {
  const s = state.system;
  if (!s) return h("section", {}, h("h1", {}, "מצב המערכת"), skeleton(3));
  const b = s.ai.budget;
  return h("section", {},
    h("h1", {}, "מצב המערכת"),
    h("div", { class: "card" },
      h("h2", {}, h("bdi", {}, "AI")),
      s.ai.state === "held"
        ? h("p", {}, "ממתין – ", HOLD[s.ai.hold_reason] || s.ai.hold_reason, ". החומר נשמר מוצפן במחשב ושום דבר לא נשלח.")
        : s.ai.credit_exhausted
          ? h("p", {}, h("strong", {}, "הקרדיט בחשבון של ספק ה-AI נגמר."), " השליחה ל-AI נעצרה והחומר שמור בתור. אחרי טעינת קרדיט העיבוד יתחדש לבד.")
          : h("p", {}, "פעיל."),
      b ? h("p", {}, `תקציב ${b.month}: נוצל ${money(b.spent_usd)} מתוך ${money(b.limit_usd)} · נותר ${money(b.remaining_usd)}`,
            b.exhausted ? h("strong", {}, " – התקציב נוצל; העיבוד ממשיך בחודש הבא, שום דבר לא נמחק.") : null)
        : h("p", { class: "muted" }, "אין תקציב פעיל (אין ספק בתשלום מחובר)."),
      h("p", { class: "muted" }, `ממתינים לעיבוד: ${s.queue.waiting}`,
        s.last_cycle_at ? ` · מחזור אחרון: ${new Date(s.last_cycle_at).toLocaleString("he-IL")}` : "")),
    s.questions.length ? h("div", { class: "card" },
      h("h2", {}, "שאלה קצרה (לא חובה)"),
      s.questions.map((q) => h("div", { class: "question" },
        h("p", {}, `השבוע היית ${Math.round(q.minutes / 60 * 10) / 10} שעות ב-${q.app}. למה זה היה קשור?`),
        h("div", { class: "row" },
          q.subjects.map((sub) => h("button", { class: "btn", onclick: () => actions.answerActivity(q.app, sub.id) }, h("bdi", {}, sub.name))),
          h("button", { class: "btn ghost", onclick: () => actions.answerActivity(q.app, "not_study") }, "לא קשור ללימודים"),
          h("button", { class: "btn text", onclick: () => actions.answerActivity(q.app, "skipped") }, "דלגי"))))) : null,
    h("div", { class: "card" },
      h("h2", {}, "זמן באפליקציות – 7 ימים (ActivityWatch)"),
      s.activity.apps.length
        ? h("div", { class: "table-wrap" }, h("table", {}, h("tr", {}, h("th", {}, "אפליקציה"), h("th", {}, "קטגוריה"), h("th", {}, "דקות")),
            s.activity.apps.map((a) => h("tr", {}, h("td", {}, h("bdi", {}, a.app)), h("td", {}, a.category || "—"), h("td", {}, a.minutes)))))
        : h("p", { class: "muted" }, "אין נתונים (ActivityWatch לא אושר להעברה, או שעוד לא נשלח יום שהסתיים)."),
      h("p", { class: "muted" }, "שם אפליקציה, קטגוריה ודקות בלבד – בלי כותרות חלונות ובלי כתובות. זמן אינו ראיה להבנה.")),
    h("div", { class: "card" },
      h("h2", {}, "העלאות ידניות"),
      s.uploads.length ? h("ul", {}, s.uploads.map((u) => h("li", {}, h("bdi", {}, u))))
        : h("p", { class: "muted" }, "אין. להעלאה: ", h("code", { dir: "ltr" }, "python -m collector.main upload FILE"))));
}

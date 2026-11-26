import { h } from "../dom.js";

export function settingsView(state) {
  return h("section", {},
    h("h1", {}, "הגדרות והנחות"),
    h("p", { class: "muted" }, "ערכים שמסומנים ASSUMPTION הם ברירות מחדל זמניות – לא החלטות מוצר."),
    h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", {},
      h("tr", {}, h("th", {}, "הגדרה"), h("th", {}, "ערך"), h("th", {}, "סטטוס"), h("th", {}, "למה")),
      state.settings.map((s) => h("tr", {},
        h("td", {}, h("code", {}, s.setting)), h("td", {}, h("code", {}, JSON.stringify(s.value))),
        h("td", {}, h("span", { class: "status-label" }, s.status)), h("td", { class: "muted" }, s.why)))))));
}

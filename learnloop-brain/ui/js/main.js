// Controller: user actions -> api -> store. Views only read state and call actions.
import { api } from "./api.js";
import { bidiText, h } from "./dom.js";
import { brandMark, emblem, icon, identityOf, setIdentities, subjectVars } from "./components.js";
import { animateProgress, glow, startSparkles } from "./effects.js";
import { assignIdentities } from "./identity.js";
import { createStore, initialState } from "./store.js";
import { activityView } from "./views/activity.js";
import { approvalsView } from "./views/approvals.js";
import { homeView } from "./views/home.js";
import { settingsView } from "./views/settings.js";
import { subjectView } from "./views/subject.js";
import { systemView } from "./views/system.js";

const store = createStore(initialState);
const $ = (id) => document.getElementById(id);

let toastTimer = 0;
function toast(text, kind = "info") {
  const el = $("toast");
  el.textContent = bidiText(text);
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), kind === "error" ? 5000 : 3200);
}

async function guarded(fn) {
  try { await fn(); } catch (e) { toast(`הפעולה לא הושלמה: ${e.message}`, "error"); }
}

async function refresh() {
  const [{ subjects }, { approvals }, { next }, { log }, { classifications }, schedule, { strengths }] = await Promise.all(
    [api.subjects(), api.approvals(), api.next(), api.log(), api.classifications(), api.schedule(), api.strengths()]);
  for (const s of subjects) {
    const d = await api.dashboard(s.id);
    s.topicsCache = d.topics;
    s.mastery = d.estimated_mastery;          // display only: the value the server already computed
  }
  setIdentities(assignIdentities(subjects));
  store.set({ subjects, approvals, next, log, classifications, schedule, strengths });
}

// Which subject an activity belongs to (for its colors only)
function subjectOfTopic(topicId) {
  for (const s of store.get().subjects) if ((s.topicsCache || []).some((t) => t.id === topicId)) return s.id;
  return null;
}

const actions = {
  go: (view) => guarded(async () => {
    store.set({ view, result: null, history: null });
    if (view === "settings") store.set({ settings: (await api.settings()).settings });
    if (view === "system") store.set({ system: await api.system() });
    await refresh();
  }),
  openSubject: (id) => guarded(async () => {
    store.set({ view: "subject", subjectId: id, history: null, dashboard: await api.dashboard(id) });
  }),
  decide: (id, action, correction) => guarded(async () => {
    const r = await api.decide(id, action, correction);
    toast(r.summary || "ההחלטה נשמרה", "success");
    await refresh();
  }),
  classify: (id, status, period) => guarded(async () => {
    await api.classify(id, status, period);
    toast("הסיווג נשמר – לא תישאלי עליו שוב", "success");
    await refresh();
  }),
  startRecommended: (rec) => guarded(async () => {
    const next = rec || store.get().next;
    if (!next) return;
    const activity = next.action === "continue" ? await api.continueActivity(next.activity_id)
      : next.action === "start_track" ? await api.startTrack(next.subject_id, next.activity_type)
      : await api.startActivity(next.topic_id, next.activity_type, next.purpose);
    store.set({ view: "activity", activity, feedback: {}, result: null,
      activitySubject: next.subject_id || subjectOfTopic(next.topic_id) });
  }),
  startTrack: (subjectId, type) => guarded(async () => {
    store.set({ view: "activity", activity: await api.startTrack(subjectId, type), feedback: {}, result: null, activitySubject: subjectId });
  }),
  startActivity: (topicId, type, purpose) => guarded(async () => {
    store.set({ view: "activity", activity: await api.startActivity(topicId, type, purpose), feedback: {}, result: null,
      activitySubject: subjectOfTopic(topicId) });
  }),
  answer: (itemId, answer) => guarded(async () => {
    const fb = await api.answer(store.get().activity.id, itemId, answer);
    store.set((s) => ({ feedback: { ...s.feedback, [itemId]: fb } }));
  }),
  hint: (itemId) => guarded(async () => toast((await api.hint(store.get().activity.id, itemId)).hint)),
  solution: (itemId) => guarded(async () => toast((await api.solution(store.get().activity.id, itemId)).solution)),
  finish: () => guarded(async () => {
    store.set({ result: await api.finish(store.get().activity.id), activity: null });
    await refresh();
  }),
  skip: () => guarded(async () => {
    await api.skip(store.get().activity.id);
    toast("דילגת – אין לזה השפעה על ההתקדמות.");
    store.set({ activity: null, view: "home" });
    await refresh();
  }),
  answerActivity: (app, answer) => guarded(async () => {
    await api.answerActivity(app, answer);
    toast("נשמר – לא תישאלי על זה שוב", "success");
    store.set({ system: await api.system() });
  }),
  showHistory: (topicId, name) => guarded(async () => {
    store.set({ history: { name, items: (await api.history(topicId)).history } });
  }),
};

function renderSidebar(state) {
  const waiting = state.approvals.length + (state.classifications || []).length;
  $("approval-count").textContent = waiting ? String(waiting) : "";
  $("subject-list").replaceChildren(...state.subjects.map((s) => {
    const id = identityOf(s.id, s.name);
    const current = state.view === "subject" && state.subjectId === s.id;
    return h("li", {}, h("button", { class: `subject-btn${current ? " active" : ""}`, style: subjectVars(id),
      "aria-current": current ? "page" : null, onclick: () => actions.openSubject(s.id) },
      emblem(id, 20), h("bdi", { class: "name" }, s.name),
      s.indicator === "red" ? h("span", { class: "red-indicator", role: "img", "aria-label": "בוחן פתוח יותר מיומיים", title: "בוחן פתוח יותר מיומיים" }) : null));
  }));
  for (const b of document.querySelectorAll(".nav-btn")) {
    const on = b.dataset.view === state.view;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
}

let lastScreen = "";
let lastHistory = null;
function render(state) {
  renderSidebar(state);
  const view = { home: homeView, approvals: approvalsView, subject: subjectView, activity: activityView, settings: settingsView, system: systemView }[state.view] || homeView;
  const main = $("view");
  const screen = `${state.view}:${state.view === "subject" ? state.subjectId : ""}:${state.result ? "result" : ""}`;
  const sid = state.view === "subject" ? state.subjectId : state.view === "activity" ? state.activitySubject : null;
  const subject = sid && state.subjects.find((s) => s.id === sid);
  const vars = sid ? subjectVars(identityOf(sid, subject && subject.name)) : { "--s-base": "", "--s-ink": "" };
  for (const [k, v] of Object.entries(vars)) v ? main.style.setProperty(k, v) : main.style.removeProperty(k);
  main.replaceChildren(view(state, actions));
  if (screen !== lastScreen) {                      // a new screen: soft entrance, scroll to the top
    main.classList.remove("entering"); void main.offsetWidth; main.classList.add("entering");
    if (lastScreen) scrollTo({ top: 0 });
    lastScreen = screen;
  }
  animateProgress(main);
  if (state.history && state.history !== lastHistory) main.querySelector("#topic-history")?.scrollIntoView({ block: "nearest" });
  lastHistory = state.history;
  for (const el of main.querySelectorAll("[data-glow]")) glow(el);
}

store.subscribe(render);
api.system().then((s) => {           // the badge tells honestly which mode is running
  $("mode-badge").textContent = s.mode === "live" ? "הנתונים שלך · מוצפן במחשב" : "מצב הדגמה · נתונים מומצאים";
  $("mode-badge").classList.toggle("live", s.mode === "live");
}).catch(() => {});
$("brand").prepend(brandMark(34));
for (const b of document.querySelectorAll(".nav-btn")) {
  b.prepend(icon(b.dataset.icon));
  b.addEventListener("click", () => actions.go(b.dataset.view));
}
startSparkles();
actions.go("home");

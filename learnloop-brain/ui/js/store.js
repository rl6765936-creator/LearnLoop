// Minimal state store: one state object, subscribers re-render on change.
// Kept framework-free so it can be replaced by the chosen framework's state tools.
export function createStore(initial) {
  let state = { ...initial };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      for (const fn of subs) fn(state);
    },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}

export const initialState = {
  view: "home", subjectId: null, subjects: [], approvals: [], classifications: [], schedule: null, strengths: [], next: null,
  dashboard: null, activity: null, activitySubject: null, feedback: {}, result: null, history: null,
  settings: [], log: [], system: null, busy: false, error: null,
};

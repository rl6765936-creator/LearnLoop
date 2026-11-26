// Run: node --test ui/tests
import test from "node:test";
import assert from "node:assert/strict";
import { confidenceLabel, feedbackClass, historyLabel, percent, stateLabel, subjectAccent } from "../js/format.js";
import { createStore, initialState } from "../js/store.js";

test("percent shows a dash when there is no score yet", () => {
  assert.equal(percent(null), "—");
  assert.equal(percent(0.834), "83%");
});

test("each subject keeps the same accent color", () => {
  assert.equal(subjectAccent("subj_1"), subjectAccent("subj_1"));
  assert.notEqual(subjectAccent("subj_1"), subjectAccent("subj_2"));
});

test("state labels and feedback classes", () => {
  assert.equal(stateLabel("understood"), "הובן");
  assert.equal(feedbackClass(0.9), "good");
  assert.equal(feedbackClass(0.3), "part");
});

test("store notifies subscribers with the merged state", () => {
  const store = createStore(initialState);
  const seen = [];
  store.subscribe((s) => seen.push(s.view));
  store.set({ view: "approvals" });
  store.set((s) => ({ feedback: { ...s.feedback, q1: { score: 1 } } }));
  assert.deepEqual(seen, ["approvals", "approvals"]);
  assert.equal(store.get().feedback.q1.score, 1);
  assert.equal(store.get().subjects.length, 0);
});

test("history labels are readable and fall back safely", () => {
  assert.equal(historyLabel("topic", "mastery_updated"), "נושא: הערכה עודכנה");
  assert.equal(historyLabel("unknown", "x"), "unknown: x");
});

test("confidence label reflects how much evidence there is", () => {
  assert.equal(confidenceLabel(null), "");
  assert.equal(confidenceLabel(1.0), "ביטחון נמוך");
  assert.equal(confidenceLabel(2.5), "ביטחון בינוני");
  assert.equal(confidenceLabel(5), "ביטחון גבוה");
});

import { masteryLabel, periodLabel, studyLabel } from "../js/format.js";

test("no evidence is never shown as 'not studied'", () => {
  assert.equal(masteryLabel("new", "not_assessed"), "אין עדיין ראיות");
  assert.equal(masteryLabel("learning", "not_assessed"), "בלמידה");
  assert.equal(studyLabel("completed"), "נלמד בעבר (הסתיים)");
  assert.equal(periodLabel({ collection: "שנה א'", period: null, study_status: "completed" }), "שנה א' · נלמד בעבר (הסתיים)");
});

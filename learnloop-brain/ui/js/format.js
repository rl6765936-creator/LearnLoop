import { identityFor } from "./identity.js";
// Pure helpers (tested with node --test).
export const STATE_LABELS = { new: "חדש", learning: "בלמידה", understood: "הובן", reinforce: "לחיזוק" };
export const TYPE_LABELS = { quiz: "בוחן קצר", game: "משחק חיזוק", project: "פרויקט", summary: "סיכום", challenge: "אתגר" };
export const KIND_LABELS = {
  new_subject: "מקצוע חדש", low_confidence: "סיווג לא ודאי", conflict: "סתירה בין מקורות",
  major_graph: "שינוי גדול במפת הידע", sensitive_transfer: "העברה רגישה",
};

export function stateLabel(state) { return STATE_LABELS[state] || state; }

// Decision 2026-11-24: three separate dimensions. 'new' only means "no evidence in the system
// yet" – it is never shown as "you did not study this".
export const STUDY_LABELS = { completed: "נלמד בעבר (הסתיים)", active: "נלמד עכשיו", not_studied: "טרם נלמד",
  unknown: "לא ידוע", mixed: "כמה תקופות" };
export const ASSESS_LABELS = { assessed: "הוערך", not_assessed: "טרם הוערך" };
export function studyLabel(status) { return STUDY_LABELS[status] || status; }
export function masteryLabel(state, assessment) {
  if (state === "new" && assessment !== "assessed") return "אין עדיין ראיות";
  return stateLabel(state);
}
export function periodLabel(p) {
  const where = p.collection ? p.collection : "התיקייה הראשית";
  return `${p.period || where} · ${studyLabel(p.study_status)}`;
}

export function percent(score) {
  return score === null || score === undefined ? "—" : `${Math.round(score * 100)}%`;
}

// Stable accent color per subject: the same subject always gets the same visual identity.
// The full identity (color, shape, tint, ink) lives in identity.js.
export function subjectAccent(id, name) {
  return identityFor(id, name).base;
}

// How much evidence a score rests on (approved: show it). Bands are a display ASSUMPTION.
export function confidenceLabel(confidence) {
  if (confidence === null || confidence === undefined) return "";
  if (confidence < 1.5) return "ביטחון נמוך";
  if (confidence < 4) return "ביטחון בינוני";
  return "ביטחון גבוה";
}

export function feedbackClass(score) { return score >= 0.8 ? "good" : "part"; }

// History entries: technical names -> readable Hebrew.
const ENTITY = { subject: "מקצוע", topic: "נושא", edge: "קשר", evidence: "ראיה", content: "קובץ",
  approval: "אישור", activity: "פעילות", permission: "הרשאה" };
const ACTION = { created: "נוצר", updated: "עודכן", mastery_updated: "הערכה עודכנה", decided: "הוחלט",
  source_removed: "הוסר", finished: "הסתיימה", skipped: "דולגה", expired: "פג תוקף",
  transfer_allowed: "הועברה הרשאה", transfer_revoked: "בוטלה הרשאה" };

export function historyLabel(entity, action) {
  return `${ENTITY[entity] || entity}: ${ACTION[action] || action}`;
}

// Greeting by the local hour (display only)
export function greeting(date = new Date()) {
  const hour = date.getHours();
  return hour < 5 ? "לילה טוב" : hour < 12 ? "בוקר טוב" : hour < 17 ? "צהריים טובים" : hour < 21 ? "ערב טוב" : "לילה טוב";
}
export function longDate(date = new Date()) {
  return date.toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });
}

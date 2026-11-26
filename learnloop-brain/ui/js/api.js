// The only module that talks to the backend. Every POST carries X-LearnLoop: 1
// (the server refuses POSTs without it – protection against requests from other sites).
async function call(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: method === "POST" ? { "Content-Type": "application/json", "X-LearnLoop": "1" } : {},
    body: method === "POST" ? JSON.stringify(body || {}) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export const api = {
  subjects: () => call("GET", "/api/subjects"),
  dashboard: (id) => call("GET", `/api/subjects/${encodeURIComponent(id)}/dashboard`),
  history: (topicId) => call("GET", `/api/topics/${encodeURIComponent(topicId)}/history`),
  approvals: () => call("GET", "/api/approvals"),
  decide: (id, action, correction) => call("POST", `/api/approvals/${encodeURIComponent(id)}`, { action, correction }),
  cycle: () => call("POST", "/api/cycle"),
  classifications: () => call("GET", "/api/classifications"),
  schedule: () => call("GET", "/api/schedule"),
  strengths: () => call("GET", "/api/strengths"),
  classify: (id, study_status, period) => call("POST", `/api/classifications/${encodeURIComponent(id)}`, { study_status, period }),
  next: (subjectId) => call("GET", subjectId ? `/api/subjects/${encodeURIComponent(subjectId)}/next` : "/api/next"),
  startActivity: (topic_id, activity_type, purpose) => call("POST", "/api/activities", { topic_id, activity_type, purpose }),
  startTrack: (subjectId, activity_type) => call("POST", `/api/subjects/${encodeURIComponent(subjectId)}/track`, { activity_type }),
  continueActivity: (id) => call("POST", `/api/activities/${encodeURIComponent(id)}/continue`),
  answer: (id, item_id, answer) => call("POST", `/api/activities/${encodeURIComponent(id)}/answer`, { item_id, answer }),
  hint: (id, item_id) => call("POST", `/api/activities/${encodeURIComponent(id)}/hint`, { item_id }),
  solution: (id, item_id) => call("POST", `/api/activities/${encodeURIComponent(id)}/solution`, { item_id }),
  finish: (id) => call("POST", `/api/activities/${encodeURIComponent(id)}/finish`),
  skip: (id) => call("POST", `/api/activities/${encodeURIComponent(id)}/skip`),
  settings: () => call("GET", "/api/settings"),
  log: () => call("GET", "/api/log"),
  system: () => call("GET", "/api/system"),
  answerActivity: (app, answer) => call("POST", "/api/activity/questions", { app, answer }),
};

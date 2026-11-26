"""Learning activities, grading, feedback and the next-step recommendation (spec Part VI–VIII).

APPROVED rules implemented here:
- activity types quiz, game, project, summary, challenge; short and focused (§22);
- create_quiz / create_project / recommend / update_mastery are autonomous (§15);
- choose by knowledge state; prerequisites first (§18); reinforcement adapted (§19);
- ignoring or skipping is never a failure, never lowers progress, never blocks (§6, §20);
- an unfinished quiz stays accessible for two days, then a red indicator next to the
  subject only (§6);
- independence is tracked: hint / solution shown lower the weight (§14);
- AI-generated project code is never evidence of the user's ability; the user's
  explanations, predictions and error finding are (§7, §23).
Closed questions are graded by code; open answers by the model (a proposal, validated).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from brain import contracts
from brain.domain import study
from brain.domain.strengths import best_ability
from brain.domain.mastery import MasteryModel, reassess
from brain.domain.models import Evidence, Topic
from brain.i18n import msg, t
from brain.settings import Settings

TYPES = ("quiz", "game", "project", "summary", "challenge")
# spec §19: reinforcement adapted to the type of mistake [mapping: ASSUMPTION]
MISTAKE_ROUTE = {"misconception": ("summary", "rec.mistake_misconception"),
                 "incomplete": ("challenge", "rec.mistake_incomplete"),
                 "careless": ("game", "rec.mistake_careless")}
CODE_GRADED = ("multiple_choice", "match")


@dataclass
class Activity:
    id: str
    type: str
    purpose: str                      # learn | reinforce | retention | transfer | understand_code
                                      # | placement | track_project | refresh (spec §21 track)
    subject_id: str
    topic_ids: list[str]
    content: dict                     # validated model proposal (title, items, code, summary_text)
    status: str = "created"           # created | in_progress | finished | skipped | expired
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    answers: dict = field(default_factory=dict)   # item id -> {answer, score, feedback, hints, saw_solution}


class ActivityError(ValueError):
    pass


class ActivityService:
    def __init__(self, repo, model, mastery: MasteryModel, settings: Settings | None = None):
        self.repo, self.model, self.mastery = repo, model, mastery
        self.settings = settings or Settings()
        self.lang = self.settings.language
        if not hasattr(repo, "activities"):
            repo.activities = {}

    # ---------------------------------------------------------------- recommendation
    def recommend(self, subject_id: str | None = None) -> dict | None:
        """The next step (Recommendation Specialist). Deterministic rules; never pressure."""
        self.expire_overdue()
        for a in self.repo.activities.values():
            if a.status == "in_progress" and (subject_id is None or a.subject_id == subject_id):
                return {"action": "continue", "activity_id": a.id, "topic_id": a.topic_ids[0],
                        "activity_type": a.type, "reason_key": "rec.continue",
                        "reason": t("rec.continue", self.lang)}
        track_ids = {t.id for t in self.track_topics(subject_id)}
        topics = [t for t in self.repo.topics.values() if (subject_id is None or t.subject_id == subject_id)
                  and t.id not in track_ids]
        if not topics:
            return self._track_recommendation(subject_id)
        retention = self.settings.mastery.retention_days

        def rank(t: Topic):
            last = self._last_graded_days(t)
            if t.state == "reinforce":
                return (0, t.name)
            if t.state in ("new", "learning"):
                return (1, t.name)
            if t.state == "understood" and (last is None or last > retention):
                return (2, t.name)
            return (3, t.name)

        topic = min(topics, key=rank)
        topic, prereq_reason = self._foundation_first(topic)
        state, last = topic.state, self._last_graded_days(topic)
        if state == "reinforce":
            # spec §19 (APPROVED): reinforcement adapted to the actual problem – type of mistake first
            mistake = self._main_mistake(topic)
            if mistake in MISTAKE_ROUTE:
                kind, reason = MISTAKE_ROUTE[mistake]
                purpose = "reinforce"
            else:
                weak = (topic.score or 0) < self.settings.mastery.reinforce_score
                kind, purpose = ("quiz", "reinforce") if weak else ("game", "reinforce")
                reason = "rec.weak" if weak else "rec.game"
        elif state in ("new", "learning"):
            kind, purpose, reason = "quiz", "learn", "rec.learn"
        elif last is None or last > retention:
            kind, purpose, reason = "quiz", "retention", "rec.retention"
        else:
            kind, purpose, reason = "challenge", "transfer", "rec.transfer"
        text = t(reason, self.lang)
        if prereq_reason:
            text = t("rec.foundation", self.lang, **prereq_reason) + " " + text
        rec = {"action": "start", "topic_id": topic.id, "activity_type": kind, "purpose": purpose,
               "reason_key": reason, "foundation": bool(prereq_reason), "reason": text}
        if purpose == "reinforce":                           # spec §42: build on a strength
            ability = best_ability(self.repo, self.settings, topic.subject_id)
            if ability:
                rec["use_strength"] = ability
                rec["reason"] += " " + t("rec.use_strength", self.lang, ability=t("ev." + ability, self.lang))
        return rec

    def _main_mistake(self, topic: Topic) -> str | None:
        """Most frequent mistake type in her last answers on this topic (open answers only)."""
        found = []
        for a in sorted(self.repo.activities.values(), key=lambda a: a.created_at):
            if topic.id not in a.topic_ids:
                continue
            for item in a.content["items"]:
                m = a.answers.get(item["id"], {}).get("mistake_type")
                if m and m != "none" and item["topic"].casefold() == topic.name.casefold():
                    found.append(m)
        recent = found[-5:]
        return max(dict.fromkeys(recent), key=recent.count) if recent else None

    # ---------------------------------------------------------------- completed-material track (§21)
    def track_topics(self, subject_id: str | None = None) -> list[Topic]:
        """Topics known ONLY from material she marked as finished (completed). A topic that also
        appears in current material follows the regular path. Finishing a course never makes a
        topic 'understood' – only her answers in the track can."""
        out = []
        for t in self.repo.topics.values():
            if subject_id is not None and t.subject_id != subject_id:
                continue
            if study.topic_study(self.repo, t.id)["summary"] == "completed":
                out.append(t)
        return out

    def track_status(self, subject_id: str) -> dict | None:
        """APPROVED path (§21): broad quiz → project/activity → knowledge picture → weekly refresh."""
        topics = self.track_topics(subject_id)
        if not topics:
            return None
        done = [a for a in self.repo.activities.values() if a.subject_id == subject_id and a.status == "finished"
                and a.purpose in ("placement", "track_project", "refresh")]
        last = max((a.finished_at for a in done), default=None)
        if not any(a.purpose == "placement" for a in done):
            step = "placement"
        elif not any(a.purpose == "track_project" for a in done):
            step = "track_project"
        else:
            due = datetime.fromisoformat(last) + timedelta(days=self.settings.activities.refresh_days)
            step = "refresh" if self._now() >= due else "waiting"
        not_assessed = [t for t in topics if study.assessment_status(self.repo, t.id) == "not_assessed"]
        return {"subject_id": subject_id, "step": step, "topics": len(topics), "not_assessed": len(not_assessed),
                "last_activity_at": last,
                "next_refresh_at": None if step != "waiting" else
                (datetime.fromisoformat(last) + timedelta(days=self.settings.activities.refresh_days)).isoformat()}

    def _track_recommendation(self, subject_id: str | None) -> dict | None:
        subjects = [subject_id] if subject_id else sorted({t.subject_id for t in self.track_topics()})
        for sid in subjects:
            st = self.track_status(sid)
            if st and st["step"] != "waiting":
                kind = {"placement": "quiz", "track_project": "project", "refresh": "quiz"}[st["step"]]
                reason = {"placement": "rec.track_placement", "track_project": "rec.track_project",
                          "refresh": "rec.track_refresh"}[st["step"]]
                rec = {"action": "start_track", "subject_id": sid, "step": st["step"], "activity_type": kind,
                       "purpose": st["step"], "reason_key": reason, "reason": t(reason, self.lang)}
                if st["step"] == "refresh":
                    rec["choices"] = ["quiz", "game", "summary"]          # "quiz or choice of activity"
                return rec
        return None

    def create_track(self, subject_id: str, activity_type: str | None = None) -> Activity:
        st = self.track_status(subject_id)
        if not st or st["step"] == "waiting":
            raise ActivityError("nothing is due in this subject's track")
        topics = self.track_topics(subject_id)
        if st["step"] == "placement":
            # broad: prefer topics never assessed, at most placement_max_topics
            topics = sorted(topics, key=lambda x: (study.assessment_status(self.repo, x.id) == "assessed", x.name))
            topics = topics[: self.settings.activities.placement_max_topics]
            kind = "quiz"
        elif st["step"] == "track_project":
            weakest = sorted(topics, key=lambda x: (x.score if x.score is not None else 0.5, x.name))
            topics, kind = weakest[:1], activity_type or "project"
        else:
            due = sorted(topics, key=lambda x: (x.state != "reinforce", x.score if x.score is not None else 0.5))
            kind = activity_type or "quiz"
            topics = due[: 3 if kind == "quiz" else 1]
        if kind not in TYPES:
            raise ActivityError("unknown activity type")
        return self._create([x.id for x in topics], kind, st["step"])

    def _foundation_first(self, topic: Topic, depth: int = 0) -> tuple[Topic, dict]:
        """spec §18 (APPROVED): a missing prerequisite comes first."""
        for e in self.repo.edges:
            if e.kind == "prerequisite" and e.dst == topic.id and e.src in self.repo.topics:
                pre = self.repo.topics[e.src]
                if pre.state != "understood" and depth < 10:
                    found, _ = self._foundation_first(pre, depth + 1)
                    return found, {"pre": found.name, "topic": topic.name}
        return topic, {}

    def _last_graded_days(self, topic: Topic) -> float | None:
        dates = [e.occurred_at for e in self.repo.evidence.values()
                 if e.topic_id == topic.id and e.score is not None and e.occurred_at]
        if not dates:
            return None
        return (self._now() - datetime.fromisoformat(max(dates))).total_seconds() / 86400

    # ---------------------------------------------------------------- lifecycle
    def create(self, topic_id: str, activity_type: str, purpose: str = "learn") -> Activity:
        if activity_type not in TYPES:
            raise ActivityError("unknown activity type")
        return self._create([topic_id], activity_type, purpose)

    def _create(self, topic_ids: list[str], activity_type: str, purpose: str) -> Activity:
        topics = [self.repo.topics[i] for i in topic_ids]
        topic, subject = topics[0], self.repo.subjects[topics[0].subject_id]
        spec = {"type": activity_type, "purpose": purpose, "subject": subject.name,
                "topics": [{"name": x.name, "state": x.state} for x in topics],
                "max_items": max(self.settings.activities.max_items, len(topics)) if purpose == "placement"
                else self.settings.activities.max_items, "language": self.lang,
                "context": [c.text[:500] for x in topics for c in self._topic_chunks(x)][:3]}
        if purpose == "reinforce":
            mistake = self._main_mistake(topic)
            ability = best_ability(self.repo, self.settings, subject.id)
            if mistake:
                spec["mistake_type"] = mistake
            if ability:
                spec["use_strength"] = ability
        if activity_type == "project":
            # spec §23: the project evolves with her – continue the last one instead of starting over
            prev = [a for a in self.repo.activities.values()
                    if a.subject_id == subject.id and a.type == "project" and a.status == "finished"]
            if prev:
                last = max(prev, key=lambda a: a.finished_at or "")
                spec["previous_project"] = {"title": last.content["title"],
                                            "code": (last.content.get("code") or "")[:2000]}
        content = self.model.generate_activity(spec)
        contracts.validate(contracts.ACTIVITY, content)
        content["items"] = content["items"][: spec["max_items"]]
        for item in content["items"]:
            if item["kind"] == "multiple_choice" and item["answer_index"] >= len(item["options"]):
                raise ActivityError("model proposed an invalid answer index")
        activity = Activity(self.repo.new_id("act"), activity_type, purpose, subject.id, list(topic_ids),
                            content, created_at=self.repo.clock())
        self.repo.activities[activity.id] = activity
        self.repo.record("activity", activity.id, "created", None,
                         {"type": activity_type, "purpose": purpose, "topic_ids": list(topic_ids)},
                         msg("h.activity_created", type_key=activity_type), "autonomous")
        return activity

    def start(self, activity_id: str) -> dict:
        a = self._get(activity_id, ("created", "in_progress"))
        if a.status == "created":
            a.status, a.started_at = "in_progress", self.repo.clock()
        return self.view(a)

    def hint(self, activity_id: str, item_id: str) -> str:
        a = self._get(activity_id, ("in_progress",))
        item = self._item(a, item_id)
        entry = a.answers.setdefault(item_id, {})
        entry["hints"] = entry.get("hints", 0) + 1
        return item.get("hint") or t("hint.default", self.lang)

    def show_solution(self, activity_id: str, item_id: str) -> str:
        a = self._get(activity_id, ("in_progress",))
        item = self._item(a, item_id)
        a.answers.setdefault(item_id, {})["saw_solution"] = True
        if item["kind"] == "multiple_choice":
            return item["options"][item["answer_index"]]
        if item["kind"] == "match":
            return "; ".join(f"{x} = {y}" for x, y in item["pairs"])
        return item["reference_answer"]

    def answer(self, activity_id: str, item_id: str, answer) -> dict:
        """Grade one answer and give feedback right away."""
        a = self._get(activity_id, ("in_progress",))
        item = self._item(a, item_id)
        mistake = None
        if item["kind"] == "multiple_choice":
            score = 1.0 if answer == item["answer_index"] else 0.0
            feedback = t("fb.correct", self.lang) if score else \
                t("fb.wrong", self.lang, answer=item["options"][item["answer_index"]])
        elif item["kind"] == "match":
            expected = {x: y for x, y in item["pairs"]}
            given = dict(answer) if isinstance(answer, (dict, list)) else {}
            score = round(sum(given.get(k) == v for k, v in expected.items()) / len(expected), 2)
            feedback = t("fb.match_all", self.lang) if score == 1 else \
                t("fb.match_some", self.lang, k=int(round(score * len(expected))), n=len(expected))
        else:
            grade = self.model.grade_answer(item, str(answer), language=self.lang)
            contracts.validate(contracts.GRADING, grade)
            score, feedback = grade["score"], grade["feedback"]
            mistake = grade.get("mistake_type")
        entry = a.answers.setdefault(item_id, {})
        entry.update({"answer": answer, "score": score, "feedback": feedback, "answered_at": self.repo.clock(),
                      "mistake_type": mistake})
        return {"item_id": item_id, "score": score, "feedback": feedback}

    def finish(self, activity_id: str) -> dict:
        """Answered items become evidence; unanswered items count for nothing (no penalty)."""
        a = self._get(activity_id, ("in_progress",))
        a.status, a.finished_at = "finished", self.repo.clock()
        by_topic: dict[str, list[str]] = {}
        names = {t.name.casefold(): t.id for t in (self.repo.topics[i] for i in a.topic_ids)}
        for item in a.content["items"]:
            entry = a.answers.get(item["id"], {})
            if "score" not in entry:
                continue
            topic_id = names.get(item["topic"].casefold(), a.topic_ids[0])
            ev_type = "retention" if a.purpose in ("retention", "refresh") and item["evidence_type"] == "quiz" \
                else item["evidence_type"]
            ev = Evidence(self.repo.new_id("ev"), topic_id, ev_type, "user", f"activity:{a.id}:{item['id']}",
                          counts_as_user_ability=True, independence=self._independence(entry),
                          score=entry["score"], occurred_at=self.repo.clock(), source="activity",
                          activity_id=a.id, note=item["kind"])
            self.repo.add_evidence(ev, msg("h.evidence_activity", type_key=a.type), "autonomous")
            by_topic.setdefault(topic_id, []).append(ev.id)
        changes = [c for tid, ids in by_topic.items()
                   if (c := reassess(self.repo, self.mastery, self.repo.topics[tid],
                                     msg("cause.activity_finished", type_key=a.type), ids, self.lang))]
        self.repo.record("activity", a.id, "finished", None, {"evidence": sum(map(len, by_topic.values()))},
                         msg("h.activity_finished"), "user")
        answered = [e for e in a.answers.values() if "score" in e]
        return {"activity_id": a.id, "answered": len(answered), "total": len(a.content["items"]),
                "average": round(sum(e["score"] for e in answered) / len(answered), 2) if answered else None,
                "mastery_changes": changes, "next": self.recommend(a.subject_id)}

    def skip(self, activity_id: str) -> dict:
        """Skipping / ignoring is allowed and costs nothing (spec §6, §20)."""
        a = self._get(activity_id, ("created", "in_progress"))
        a.status = "skipped"
        self.repo.record("activity", a.id, "skipped", None, {}, msg("h.activity_skipped"), "user")
        return {"activity_id": a.id, "status": "skipped", "next": self.recommend(a.subject_id)}

    def expire_overdue(self) -> list[str]:
        """spec §6: after two days an unfinished quiz is no longer offered; red indicator."""
        limit = timedelta(days=self.settings.activities.unfinished_quiz_days)
        expired = []
        for a in self.repo.activities.values():
            if a.status == "in_progress" and a.started_at and \
                    self._now() - datetime.fromisoformat(a.started_at) > limit:
                a.status = "expired"
                self.repo.record("activity", a.id, "expired", None, {},
                                 msg("h.activity_expired", days=self.settings.activities.unfinished_quiz_days),
                                 "autonomous")
                expired.append(a.id)
        return expired

    def subject_indicators(self) -> dict[str, str]:
        """Red indicator NEXT TO THE SUBJECT ONLY for an expired unfinished quiz (spec §6)."""
        self.expire_overdue()
        return {a.subject_id: "red" for a in self.repo.activities.values()
                if a.status == "expired" and a.type == "quiz"}

    # ---------------------------------------------------------------- views
    def view(self, a: Activity) -> dict:
        """What the user sees: never the correct answers."""
        items = []
        for item in a.content["items"]:
            shown = {k: item[k] for k in ("id", "kind", "prompt", "options") if k in item}
            if item["kind"] == "match":
                shown["left"] = [x for x, _ in item["pairs"]]
                shown["right"] = sorted(y for _, y in item["pairs"])
            shown["has_hint"] = bool(item.get("hint"))
            shown["answered"] = "score" in a.answers.get(item["id"], {})
            items.append(shown)
        out = {"id": a.id, "type": a.type, "purpose": a.purpose, "status": a.status,
               "title": a.content["title"], "items": items}
        if a.type == "project":
            out["code"] = a.content.get("code", "")
            out["code_author"] = "ai"            # spec §7: shown as AI-generated
        if a.content.get("summary_text"):
            out["summary_text"] = a.content["summary_text"]
        return out

    # ---------------------------------------------------------------- helpers
    def _get(self, activity_id: str, allowed: tuple) -> Activity:
        a = self.repo.activities.get(activity_id)
        if a is None:
            raise ActivityError("unknown activity")
        if a.status not in allowed:
            raise ActivityError(f"activity is {a.status}")
        return a

    @staticmethod
    def _item(a: Activity, item_id: str) -> dict:
        for item in a.content["items"]:
            if item["id"] == item_id:
                return item
        raise ActivityError("unknown item")

    @staticmethod
    def _independence(entry: dict) -> str:
        if entry.get("saw_solution"):
            return "saw_solution"
        hints = entry.get("hints", 0)
        return "independent" if hints == 0 else "hint" if hints == 1 else "substantial_help"

    def _topic_chunks(self, topic: Topic):
        ids = {e.chunk_id for e in self.repo.evidence.values() if e.topic_id == topic.id}
        return [c for c in self.repo.active_chunks() if c.chunk_id in ids]

    def _now(self) -> datetime:
        return datetime.fromisoformat(self.repo.clock())


def activity_to_dict(a: Activity) -> dict:
    return asdict(a)

"""Ingestion steps end to end, without LangGraph (run_without_graph plays the workflow)."""

import unittest

from brain.ingestion import steps
from brain.ingestion.steps import run_without_graph
from brain.model_gateway import FailingModel, FakeModel
from helpers import SOURCE, analysis, approve_all, deps, envelope, reject_all


def with_subject(d, name="Python"):
    """A repository that already knows the subject (approved earlier)."""
    return d.repo.add_subject(name, "req_earlier", "approved earlier")


class TestArrival(unittest.TestCase):
    def test_source_without_transfer_permission_is_rejected_and_not_stored(self):
        d = deps()
        d.repo.set_transfer_permission(SOURCE, False)
        state = run_without_graph(envelope(["x"]), d, approve_all)
        self.assertEqual(state["status"], "rejected")
        self.assertEqual(d.model.calls, [])
        self.assertEqual(d.repo.content, {})

    def test_invalid_envelope_is_rejected(self):
        d = deps()
        env = envelope(["x"])
        env["content_trust"] = "trusted"
        self.assertEqual(run_without_graph(env, d)["status"], "rejected")
        self.assertEqual(d.model.calls, [])


class TestNewSubject(unittest.TestCase):
    def test_waits_for_the_user_and_writes_nothing_before(self):
        d = deps()
        state = run_without_graph(envelope(["x = [1, 2]"]), d, decide=None)
        self.assertEqual(state["status"], "waiting_for_user")
        self.assertEqual([r["kind"] for r in state["requests"]], ["new_subject"])
        self.assertEqual((d.repo.subjects, d.repo.topics, d.repo.content, d.repo.history),
                         ({}, {}, {}, []))

    def test_pending_request_is_never_resolved_automatically(self):
        d = deps()
        state = run_without_graph(envelope(["x"]), d, decide=None)
        self.assertEqual(steps.apply_changes(state, d)["status"], "waiting_for_user")
        self.assertEqual(d.repo.subjects, {})

    def test_approve_creates_subject_topics_and_evidence(self):
        d = deps()
        state = run_without_graph(envelope(["x = [1, 2]"]), d, approve_all)
        self.assertEqual(state["status"], "applied")
        self.assertEqual([s.name for s in d.repo.subjects.values()], ["Python"])
        self.assertEqual([t.name for t in d.repo.topics.values()], ["Lists"])
        self.assertEqual(len(d.repo.evidence), 1)
        subject_record = next(r for r in d.repo.history if r.entity == "subject")
        self.assertEqual((subject_record.mode, subject_record.approval_id),
                         ("approved", state["requests"][0]["id"]))

    def test_reject_leaves_the_graph_unchanged(self):
        d = deps()
        state = run_without_graph(envelope(["x"]), d, reject_all)
        self.assertEqual(state["status"], "applied")
        self.assertEqual((d.repo.subjects, d.repo.topics, d.repo.evidence), ({}, {}, {}))
        self.assertEqual(state["summary"]["note"]["key"], "note.new_subject_rejected")
        self.assertIn("נדחה", state["transparency"])

    def test_correct_to_an_existing_subject(self):
        d = deps(analysis(subject="Pyton"))          # model misspelled it -> "new"
        existing = with_subject(d)
        correct = lambda reqs: [{"request_id": reqs[0]["id"], "action": "correct",
                                 "correction": {"use_existing_id": existing.id}}]
        run_without_graph(envelope(["x"]), d, correct)
        self.assertEqual(len(d.repo.subjects), 1)
        self.assertEqual(next(iter(d.repo.topics.values())).subject_id, existing.id)


class TestRoutine(unittest.TestCase):
    def test_known_subject_confident_topics_run_autonomously(self):
        d = deps(analysis(topics=(("Lists", 0.9), ("Loops", 0.8))))
        with_subject(d)
        state = run_without_graph(envelope(["for x in items: ..."]), d, decide=None)
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["requests"], [])
        self.assertEqual(sorted(state["summary"]["topics_added"]), ["Lists", "Loops"])
        self.assertTrue(all(r.mode == "autonomous" for r in d.repo.history
                            if r.entity in ("topic", "evidence")))

    def test_unsure_topic_rejected_skips_it_and_its_evidence(self):
        d = deps(analysis(topics=(("Lists", 0.9), ("Decorators", 0.3))))
        with_subject(d)
        state = run_without_graph(envelope(["x"]), d, reject_all)
        self.assertEqual([t.name for t in d.repo.topics.values()], ["Lists"])
        self.assertEqual(len(d.repo.evidence), 1)
        self.assertIn({"key": "skip.rejected", "topic": "Decorators"}, state["summary"]["skipped"])
        self.assertIn("Decorators: נדחה על ידך", state["transparency"])

    def test_unsure_topic_corrected_gets_the_users_name(self):
        d = deps(analysis(topics=(("Decorater", 0.3),)))
        with_subject(d)
        fix = lambda reqs: [{"request_id": reqs[0]["id"], "action": "correct",
                             "correction": {"name": "Decorators"}}]
        run_without_graph(envelope(["x"]), d, fix)
        self.assertEqual([t.name for t in d.repo.topics.values()], ["Decorators"])

    def test_existing_topic_is_reused(self):
        d = deps()
        s = with_subject(d)
        t = d.repo.add_topic(s.id, "lists", "earlier", "autonomous")
        run_without_graph(envelope(["x"]), d)
        self.assertEqual(list(d.repo.topics), [t.id])
        self.assertEqual(next(iter(d.repo.evidence.values())).topic_id, t.id)


class TestIdempotencyAndFailures(unittest.TestCase):
    def test_same_envelope_twice_is_processed_once(self):
        d = deps()
        with_subject(d)
        env = envelope(["x"])
        run_without_graph(env, d)
        self.assertEqual(run_without_graph(env, d)["status"], "duplicate")
        self.assertEqual(len(d.repo.evidence), 1)
        self.assertEqual(len(d.model.calls), 1)

    def test_apply_running_twice_changes_nothing_the_second_time(self):
        d = deps()
        with_subject(d)
        state = run_without_graph(envelope(["x"]), d)
        history = len(d.repo.history)
        self.assertEqual(steps.apply_changes(state, d)["status"], "duplicate")
        self.assertEqual(len(d.repo.history), history)

    def test_model_failure_is_retried_then_reported_and_not_marked_processed(self):
        d = deps(model=FailingModel(failures=3))
        with_subject(d)
        env = envelope(["x"])
        state = run_without_graph(env, d)
        self.assertEqual((state["status"], state["attempts"]), ("failed", 3))
        self.assertFalse(d.repo.already_applied(env["idempotency_key"]))
        self.assertEqual(d.repo.content, {})                  # nothing half-stored
        d.model = FakeModel(analysis())                        # next cycle: model works
        self.assertEqual(run_without_graph(env, d)["status"], "applied")

    def test_transient_failure_recovers_within_retries(self):
        d = deps(model=FailingModel(failures=2, then=FakeModel(analysis())))
        with_subject(d)
        state = run_without_graph(envelope(["x"]), d)
        self.assertEqual((state["status"], state["attempts"]), ("applied", 3))

    def test_invalid_model_output_is_never_used(self):
        d = deps({"subject": "not an object"})
        with_subject(d)
        state = run_without_graph(envelope(["x"]), d)
        self.assertEqual(state["status"], "failed")
        self.assertIn("model output rejected", state["error"])


class TestTraceabilityAndRules(unittest.TestCase):
    def test_references_to_text_that_was_not_sent_are_dropped(self):
        answer = analysis(topics=(("Lists", 0.9),))
        answer["topics"].append({"name": "Invented", "confidence": 0.9, "chunk_ids": ["c99"]})
        answer["evidence"].append({"topic": "Lists", "type": "quiz", "authorship": "user",
                                   "chunk_id": "c99"})
        d = deps(answer)
        with_subject(d)
        state = run_without_graph(envelope(["x"]), d)
        self.assertEqual([t.name for t in d.repo.topics.values()], ["Lists"])
        self.assertEqual(len(d.repo.evidence), 1)
        self.assertEqual(state["plan"]["dropped_topics"], ["Invented"])

    def test_evidence_keeps_the_exact_segment(self):
        d = deps()
        with_subject(d)
        run_without_graph(envelope(["first", "second"]), d)
        ev = next(iter(d.repo.evidence.values()))
        self.assertEqual(d.repo.chunks[ev.chunk_id].location, "lines 1-200")

    def test_ai_generated_code_is_not_user_ability(self):
        ev = [{"topic": "Lists", "type": "apply", "authorship": "ai", "chunk_id": "c0"},
              {"topic": "Lists", "type": "apply", "authorship": "user", "chunk_id": "c0"}]
        d = deps(analysis(evidence=ev))
        with_subject(d)
        run_without_graph(envelope(["x"]), d)
        flags = sorted(e.counts_as_user_ability for e in d.repo.evidence.values())
        self.assertEqual(flags, [False, True])

    def test_prerequisite_that_contradicts_existing_order_is_not_added(self):
        d = deps(analysis(topics=(("Loops", 0.9), ("Lists", 0.9)),
                          prerequisites={"Lists": ["Loops"]}))
        with_subject(d)
        run_without_graph(envelope(["a"]), d)
        self.assertEqual(len(d.repo.edges), 1)                 # Loops -> Lists
        d.model = FakeModel(analysis(topics=(("Loops", 0.9), ("Lists", 0.9)),
                                     prerequisites={"Loops": ["Lists"]}))
        state = run_without_graph(envelope(["b"]), d)
        self.assertEqual(len(d.repo.edges), 1)
        self.assertTrue(any(s["key"] == "skip.order_contradicts" for s in state["summary"]["skipped"]))

    def test_mastery_is_not_changed_without_an_approved_formula(self):
        d = deps(analysis(evidence=[{"topic": "Lists", "type": "quiz", "authorship": "user",
                                     "chunk_id": "c0"}] * 3))
        with_subject(d)
        state = run_without_graph(envelope(["x"]), d)
        topic = next(iter(d.repo.topics.values()))
        self.assertEqual((topic.state, topic.score), ("new", None))
        self.assertIn("לא השתנתה", state["transparency"])


class TestRemovalAndMove(unittest.TestCase):
    def test_removed_file_stops_being_used_but_history_and_evidence_stay(self):
        d = deps()
        with_subject(d)
        run_without_graph(envelope(["x"], rel_path="a.py"), d)
        evidence = dict(d.repo.evidence)
        removal = envelope([], rel_path="a.py", change="removed")
        state = run_without_graph(removal, d)
        self.assertEqual(state["status"], "applied")
        self.assertEqual(d.repo.content[(SOURCE, "a.py")].status, "source_removed")
        self.assertEqual(d.repo.active_chunks(), [])
        self.assertEqual(d.repo.evidence, evidence)
        self.assertEqual(d.model.calls.__len__(), 1)          # removal needs no model call

    def test_moved_file_marks_the_old_path(self):
        d = deps()
        with_subject(d)
        run_without_graph(envelope(["x"], rel_path="old.py"), d)
        run_without_graph(envelope(["x"], rel_path="new.py", change="moved", previous="old.py"), d)
        self.assertEqual(d.repo.content[(SOURCE, "old.py")].status, "source_removed")
        self.assertEqual(d.repo.content[(SOURCE, "new.py")].status, "active")



class TestChunkNamespacing(unittest.TestCase):
    def test_chunks_of_two_files_do_not_overwrite_each_other(self):
        d = deps()
        with_subject(d)
        run_without_graph(envelope(["first file"], rel_path="a.py"), d)
        run_without_graph(envelope(["second file"], rel_path="b.py"), d)
        self.assertEqual(sorted(c.text for c in d.repo.chunks.values()), ["first file", "second file"])
        for ev in d.repo.evidence.values():
            self.assertIn(ev.chunk_id, d.repo.chunks)



class TestLatePrerequisite(unittest.TestCase):
    def test_foundation_arriving_in_a_later_file_is_linked(self):
        d = deps(analysis(topics=(("Lists", 0.9),), prerequisites={"Lists": ["Loops"]}))
        with_subject(d)
        run_without_graph(envelope(["lists"], rel_path="lists.py"), d)
        self.assertEqual(d.repo.edges, set())
        d.model = FakeModel(analysis(topics=(("Loops", 0.9),)))
        state = run_without_graph(envelope(["loops"], rel_path="loops.py"), d)
        names = {(d.repo.topics[e.src].name, d.repo.topics[e.dst].name) for e in d.repo.edges}
        self.assertEqual(names, {("Loops", "Lists")})
        self.assertIn({"key": "tr.order_item", "src": "Loops", "dst": "Lists"}, state["summary"]["autonomous"])
        self.assertEqual(d.repo.pending_prerequisites, [])


if __name__ == "__main__":
    unittest.main()

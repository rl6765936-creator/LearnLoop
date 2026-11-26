import unittest

from brain import policy


def plan(subject_existing="subj_1", subject_conf=0.9, topics=()):
    return {"subject": {"name": "Python", "existing_id": subject_existing,
                        "confidence": subject_conf},
            "topics": [dict({"name": n, "existing_id": e, "confidence": c}, **x)
                       for n, e, c, x in topics]}


class TestPolicy(unittest.TestCase):
    def test_routine_needs_no_approval(self):
        self.assertEqual(policy.approvals_needed(plan(topics=[("Lists", "t1", 0.9, {})]), "k"), [])

    def test_new_subject_needs_approval(self):
        kinds = [r["kind"] for r in policy.approvals_needed(plan(subject_existing=None), "k")]
        self.assertEqual(kinds, ["new_subject"])

    def test_unsure_topics_are_grouped_into_one_request(self):
        reqs = policy.approvals_needed(plan(topics=[("A", None, 0.3, {}), ("B", None, 0.2, {})]), "k")
        self.assertEqual([r["kind"] for r in reqs], ["low_confidence"])
        self.assertEqual(reqs[0]["refs"], ["topic:A", "topic:B"])

    def test_many_new_topics_is_a_major_change(self):
        topics = [(f"T{i}", None, 0.9, {}) for i in range(policy.MAJOR_NEW_TOPICS + 1)]
        self.assertEqual([r["kind"] for r in policy.approvals_needed(plan(topics=topics), "k")],
                         ["major_graph"])

    def test_conflict_needs_approval(self):
        reqs = policy.approvals_needed(plan(topics=[("A", None, 0.9, {"conflict": "x"})]), "k")
        self.assertEqual([r["kind"] for r in reqs], ["conflict"])

    def test_request_ids_are_stable_for_resume(self):
        p = plan(subject_existing=None)
        self.assertEqual(policy.approvals_needed(p, "k"), policy.approvals_needed(p, "k"))
        self.assertNotEqual(policy.approvals_needed(p, "k")[0]["id"],
                            policy.approvals_needed(p, "other")[0]["id"])


if __name__ == "__main__":
    unittest.main()

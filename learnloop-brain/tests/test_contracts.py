import copy
import unittest

from brain import contracts
from helpers import analysis, envelope


class TestEnvelope(unittest.TestCase):
    def test_valid_envelope_passes(self):
        contracts.validate(contracts.ENVELOPE, envelope(["x = [1, 2]"]))

    def test_unknown_field_is_rejected(self):
        env = envelope(["x"])
        env["run_this"] = "rm -rf"
        with self.assertRaises(contracts.ContractError):
            contracts.validate(contracts.ENVELOPE, env)

    def test_content_must_be_marked_untrusted(self):
        env = envelope(["x"])
        env["content_trust"] = "trusted"
        with self.assertRaises(contracts.ContractError):
            contracts.validate(contracts.ENVELOPE, env)

    def test_error_message_never_contains_content(self):
        env = envelope(["SECRET STUDY TEXT"])
        env["chunks"][0]["index"] = "not a number"
        with self.assertRaises(contracts.ContractError) as ctx:
            contracts.validate(contracts.ENVELOPE, env)
        self.assertNotIn("SECRET STUDY TEXT", str(ctx.exception))


class TestAnalysis(unittest.TestCase):
    def test_valid_analysis_passes(self):
        contracts.validate(contracts.ANALYSIS, analysis())

    def test_model_cannot_set_approval_or_mastery_fields(self):
        for extra in ({"approval_required": False}, {"mastery": "understood"},
                      {"permissions": {"transfer": True}}):
            bad = dict(analysis(), **extra)
            with self.assertRaises(contracts.ContractError, msg=str(extra)):
                contracts.validate(contracts.ANALYSIS, bad)

    def test_confidence_out_of_range_is_rejected(self):
        bad = copy.deepcopy(analysis())
        bad["topics"][0]["confidence"] = 7
        with self.assertRaises(contracts.ContractError):
            contracts.validate(contracts.ANALYSIS, bad)


class TestDecision(unittest.TestCase):
    def test_correct_requires_correction(self):
        with self.assertRaises(contracts.ContractError):
            contracts.validate(contracts.DECISION, {"request_id": "r", "action": "correct"})
        contracts.validate(contracts.DECISION, {"request_id": "r", "action": "correct",
                                                "correction": {"name": "C#"}})

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(contracts.ContractError):
            contracts.validate(contracts.DECISION, {"request_id": "r", "action": "auto"})


if __name__ == "__main__":
    unittest.main()

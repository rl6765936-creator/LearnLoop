"""Tests for Stage 7 – security filtering.

All "secrets" here are FAKE values in valid-looking formats (test numbers such as
4111 1111 1111 1111 are published test card numbers).
"""

import json
import tempfile
import unittest
from pathlib import Path

from collector.config import CollectorConfig, save_config
from collector.monitor.monitor import IGNORED, Monitor
from collector.readers.base import OK, ExtractionResult, Segment
from collector.security import outbox
from collector.security.detectors import DEFINITE, UNCERTAIN, find_sensitive
from collector.security.filter import CLEAN, PENDING_REVIEW, REDACTED, filter_result
from collector.security.review import APPROVE, IGNORE, save_decision

FAKE_OPENAI = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8t"
FAKE_GITHUB = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
RANDOM_TOKEN = "Zx8Qp2Lm7Rt4Vb9Nc1Hy6Wk3Js5Df0GaK2pT9"


def kinds(text, certainty=None):
    return [f.kind for f in find_sensitive(text) if certainty in (None, f.certainty)]


def filtered(text, **kwargs):
    result = ExtractionResult(Path("x.txt"), OK, "text", [Segment("lines 1-1", text)])
    return filter_result(result, **kwargs)


class TestSecrets(unittest.TestCase):
    def test_api_keys(self):
        for key in (FAKE_OPENAI, FAKE_GITHUB, FAKE_AWS):
            self.assertIn("api_key", kinds(f"key is {key} ok", DEFINITE), key)

    def test_private_key_block(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(kinds(text), ["private_key"])

    def test_password_value_only_is_redacted(self):
        out = filtered('db_password = "Hunter2!x"\nprint("hi")')
        self.assertEqual(out.status, REDACTED)
        text = out.segments[0].text
        self.assertNotIn("Hunter2!x", text)
        self.assertIn("db_password = \"[REDACTED:password]\"", text)
        self.assertIn('print("hi")', text)

    def test_hebrew_password(self):
        out = filtered("הסיסמה של המעבדה - סיסמה: Lab2024pass")
        self.assertNotIn("Lab2024pass", out.segments[0].text)

    def test_password_placeholders_are_not_secrets(self):
        for text in ("password = input()", "password: <your password>", "password = None",
                     "password = os.environ['DB_PASS']", "password = ****"):
            self.assertEqual(kinds(text), [], text)

    def test_secret_assignment(self):
        self.assertIn("secret", kinds('API_KEY = "abcd1234efgh"'))
        self.assertIn("secret", kinds("client_secret: s3cr3tvalue99"))

    def test_connection_string_password(self):
        out = filtered("postgres://admin:SuperPass9@db.example.com/app")
        self.assertIn("admin:[REDACTED:password]@db.example.com", out.segments[0].text)

    def test_hidden_zero_width_character_does_not_hide_a_secret(self):
        out = filtered("pass​word = Hunter2!x")
        self.assertNotIn("Hunter2!x", out.segments[0].text)


class TestPersonalData(unittest.TestCase):
    def test_valid_israeli_id(self):
        self.assertEqual(kinds("ת.ז. 123456782"), ["israeli_id"])

    def test_invalid_israeli_id_is_ignored(self):
        self.assertEqual(kinds("מספר 123456789"), [])

    def test_credit_card_with_spaces(self):
        self.assertIn("credit_card", kinds("card: 4111 1111 1111 1111"))

    def test_number_failing_luhn_is_ignored(self):
        self.assertNotIn("credit_card", kinds("order 4111 1111 1111 1112"))

    def test_iban(self):
        self.assertIn("bank_account", kinds("IBAN IL620108000000099999999"))

    def test_bank_account_with_context(self):
        self.assertIn("bank_account", kinds("מספר חשבון: 12-345-678901"))

    def test_emails_and_phones_are_kept(self):
        # User decision (option B): emails and phone numbers are NOT redacted.
        self.assertEqual(kinds("lecturer@uni.ac.il 052-1234567"), [])


class TestUncertainAndFalsePositives(unittest.TestCase):
    def test_random_token_without_context_holds_the_file(self):
        out = filtered(f"see {RANDOM_TOKEN}")
        self.assertEqual(out.status, PENDING_REVIEW)
        self.assertEqual(out.segments, [])          # nothing continues
        self.assertEqual(out.uncertain[0].kind, "possible_secret")

    def test_approved_review_lets_content_through(self):
        out = filtered(f"see {RANDOM_TOKEN} and password = Hunter2!x", accept_uncertain=True)
        self.assertEqual(out.status, REDACTED)
        self.assertNotIn("Hunter2!x", out.segments[0].text)  # definite still removed

    def test_git_hash_is_not_a_secret(self):
        self.assertEqual(kinds("commit 3f786850e387550fdab836ed7e6dc881de23001b"), [])

    def test_base64_image_is_not_a_secret(self):
        self.assertEqual(kinds(f"data:image/png;base64,{RANDOM_TOKEN}"), [])

    def test_normal_study_text_is_clean(self):
        text = "Big-O של מיון מהיר הוא O(n log n) בממוצע. ראו עמוד 42, שנת 2026."
        self.assertEqual(filtered(text).status, CLEAN)

    def test_one_secret_found_by_two_detectors_counts_once(self):
        out = filtered(f'API_KEY = "{FAKE_OPENAI}"')
        self.assertEqual(len(out.redactions), 1)

    def test_report_never_contains_the_secret(self):
        out = filtered(f"token {FAKE_GITHUB}")
        self.assertNotIn(FAKE_GITHUB, json.dumps([r.__dict__ for r in out.redactions]))


class TestPipeline(unittest.TestCase):
    """Monitor -> read -> filter -> outbox, end to end."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root = base / "study"
        self.root.mkdir()
        self.cfg = base / "settings.json"
        self.outbox = base / "outbox"
        self.review = base / "review.json"
        save_config(CollectorConfig(approved_folder=str(self.root)), self.cfg)
        self.monitor = Monitor(self.cfg, base / "state.json", settle_seconds=0,
                               outbox_dir=self.outbox, review_path=self.review)

    def tearDown(self):
        self._tmp.cleanup()

    def entries(self):
        return {e["source"]: e for e in outbox.list_entries(self.outbox)}

    def test_secret_never_reaches_the_outbox(self):
        (self.root / "hw.py").write_text(f'KEY = "{FAKE_OPENAI}"\nprint(1)', encoding="utf-8")
        report = self.monitor.run_once()
        self.assertEqual(report.redacted, [("hw.py", 1)])
        raw = json.dumps(outbox.list_entries(self.outbox), ensure_ascii=False)
        self.assertNotIn(FAKE_OPENAI, raw)
        self.assertIn("print(1)", raw)

    def test_uncertain_file_is_held_and_not_in_outbox(self):
        (self.root / "notes.txt").write_text(f"x {RANDOM_TOKEN}", encoding="utf-8")
        report = self.monitor.run_once()
        self.assertEqual(report.review_needed[0][0], "notes.txt")
        self.assertNotIn("notes.txt", self.entries())

    def test_approve_releases_the_file(self):
        (self.root / "notes.txt").write_text(f"x {RANDOM_TOKEN}", encoding="utf-8")
        self.monitor.run_once()
        sha = self.monitor.state.files["notes.txt"].sha256
        save_decision(sha, APPROVE, self.review)
        self.monitor.run_once()
        self.assertIn("notes.txt", self.entries())

    def test_ignore_keeps_it_out(self):
        (self.root / "notes.txt").write_text(f"x {RANDOM_TOKEN}", encoding="utf-8")
        self.monitor.run_once()
        save_decision(self.monitor.state.files["notes.txt"].sha256, IGNORE, self.review)
        self.monitor.run_once()
        self.assertEqual(self.monitor.state.files["notes.txt"].status, IGNORED)
        self.assertNotIn("notes.txt", self.entries())

    def test_approval_does_not_cover_a_changed_file(self):
        path = self.root / "notes.txt"
        path.write_text(f"x {RANDOM_TOKEN}", encoding="utf-8")
        self.monitor.run_once()
        save_decision(self.monitor.state.files["notes.txt"].sha256, APPROVE, self.review)
        path.write_text(f"changed {RANDOM_TOKEN} more", encoding="utf-8")
        report = self.monitor.run_once()
        self.assertEqual(report.review_needed[0][0], "notes.txt")

    def test_removed_file_leaves_the_outbox(self):
        path = self.root / "a.txt"
        path.write_text("study text", encoding="utf-8")
        self.monitor.run_once()
        self.assertIn("a.txt", self.entries())
        path.unlink()
        self.monitor.run_once()
        self.assertNotIn("a.txt", self.entries())

    def test_renamed_file_moves_in_the_outbox(self):
        path = self.root / "a.txt"
        path.write_text("study text", encoding="utf-8")
        self.monitor.run_once()
        path.rename(self.root / "b.txt")
        self.monitor.run_once()
        self.assertEqual(list(self.entries()), ["b.txt"])


if __name__ == "__main__":
    unittest.main()

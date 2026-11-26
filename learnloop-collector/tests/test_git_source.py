"""Git source (decision 2026-11-24 Q11). Needs dulwich (in requirements.txt); skipped without it."""

import tempfile
import unittest
from pathlib import Path

from collector.sources import git_repos as g

try:
    import dulwich  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

KEY = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8t"


def make_repo(root: Path):
    from dulwich import porcelain
    repo = porcelain.init(str(root))
    (root / "lists.py").write_text(f'KEY = "{KEY}"\nnums = [3, 1]\n', encoding="utf-8")
    porcelain.add(repo, [str(root / "lists.py")])
    porcelain.commit(repo, message=b"add lists exercise", author=b"Me <me@example.com>",
                     committer=b"Me <me@example.com>")
    (root / "lists.py").write_text('nums = [3, 1]\nnums.sort()\n', encoding="utf-8")
    porcelain.add(repo, [str(root / "lists.py")])
    porcelain.commit(repo, message="מיון רשימה".encode("utf-8"), author=b"Me <me@example.com>",
                     committer=b"Me <me@example.com>")
    repo.close()


class TestGitChecks(unittest.TestCase):
    def test_refuses_non_repo_and_forbidden(self):
        d = Path(tempfile.mkdtemp())
        self.assertFalse(g.check_repository(str(d))[0])                       # no .git
        self.assertFalse(g.check_repository("relative/path")[0])
        home = d / "home"
        (home / "Downloads" / "r" / ".git").mkdir(parents=True)
        ok, why, _ = g.check_repository(str(home / "Downloads" / "r"), home=home)
        self.assertFalse(ok)
        self.assertIn("Downloads", why)


@unittest.skipUnless(HAVE, "dulwich not installed – pip install -r requirements.txt")
class TestGitRead(unittest.TestCase):
    def test_messages_and_diffs_filtered_nothing_executed(self):
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir()
        make_repo(root)
        hooks = root / ".git" / "hooks"
        hooks.mkdir(exist_ok=True)
        marker = root / "HOOK_RAN"
        for name in ("post-checkout", "pre-commit", "post-merge"):
            (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
        commits = g.read_commits(root)
        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0].message.strip(), "מיון רשימה")            # newest first
        self.assertIn("nums.sort()", commits[0].diff)
        everything = "".join(c.message + c.diff for c in commits)
        self.assertNotIn(KEY, everything)                                     # secret removed locally
        self.assertEqual(commits[1].status, "redacted")
        self.assertFalse(marker.exists())                                     # no hook ran
        again = g.read_commits(root, seen={c.commit_id for c in commits})
        self.assertEqual(again, [])                                           # not read twice


if __name__ == "__main__":
    unittest.main()

"""Synthetic test data only – no real study material."""

import hashlib

from brain.domain.repository import InMemoryRepository
from brain.ingestion.steps import Deps
from brain.model_gateway import FakeModel

SOURCE = "folder-1"


def envelope(texts, rel_path="python/lists.py", change="new", source=SOURCE, grant="g1",
             previous=None):
    body = "\n".join(texts)
    sha = hashlib.sha256((rel_path + body).encode("utf-8")).hexdigest()
    env = {
        "schema_version": 1,
        "envelope_id": "env-" + sha[:12],
        "idempotency_key": hashlib.sha256(f"{source}|{grant}|{sha}".encode()).hexdigest(),
        "source": {"type": "folder", "source_id": source, "grant_id": grant},
        "file": {"rel_path": rel_path, "sha256": sha, "size": len(body), "change": change},
        "filter": {"status": "clean", "redactions": []},
        "extract": {"reader": "text", "warnings": []},
        "chunks": [{"chunk_id": f"c{i}", "index": i, "total": len(texts),
                    "location": f"lines {i * 200 + 1}-{i * 200 + 200}", "text": t}
                   for i, t in enumerate(texts)],
        "content_trust": "untrusted_user_file",
    }
    if previous:
        env["file"]["previous_rel_path"] = previous
    return env


def analysis(subject="Python", topics=(("Lists", 0.9),), evidence=None, matched=None,
             prerequisites=None):
    prerequisites = prerequisites or {}
    return {
        "subject": {"name": subject, "matched_subject_id": matched, "confidence": 0.9},
        "topics": [{"name": n, "confidence": c, "chunk_ids": ["c0"],
                    "prerequisites": list(prerequisites.get(n, []))} for n, c in topics],
        "evidence": evidence if evidence is not None else
        [{"topic": n, "type": "exposure", "authorship": "unknown", "chunk_id": "c0"}
         for n, _ in topics],
    }


def deps(answer=None, model=None):
    repo = InMemoryRepository(clock=lambda: "2026-11-24T00:00:00+00:00")
    repo.set_transfer_permission(SOURCE, True)
    return Deps(repo=repo, model=model or FakeModel(answer or analysis()))


def approve_all(requests):
    return [{"request_id": r["id"], "action": "approve"} for r in requests]


def reject_all(requests):
    return [{"request_id": r["id"], "action": "reject"} for r in requests]


def close_databases_after(test):
    """Close every SQLite connection opened during `test` (no ResourceWarning on Windows)."""
    from brain.storage import db as _db
    opened = []
    original = _db.SqliteDatabase.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        opened.append(self)
    _db.SqliteDatabase.__init__ = init

    def done():
        _db.SqliteDatabase.__init__ = original
        for d in opened:
            try:
                d.close()
            except Exception:
                pass
    test.addCleanup(done)

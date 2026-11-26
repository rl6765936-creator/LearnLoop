"""Demo setup: synthetic study material + FakeModel, so the whole product can be tried
without any AI provider. Every text here is invented."""

from __future__ import annotations

import hashlib
from pathlib import Path

from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel

DEMO_SOURCE = "demo-folder"
LOCAL_DATA = Path(__file__).resolve().parents[2] / "local_data"

FILES = {
    "python/lists.py": ("numbers = [3, 1, 2]\nnumbers.sort()\nprint(numbers)",
                        "Python", [("Lists", 0.92, ["Loops"])]),
    "python/loops.py": ("for i in range(3):\n    print(i)",
                        "Python", [("Loops", 0.9, [])]),
    "python/dicts.md": ("# Dictionaries\nA dict maps keys to values: {'a': 1}",
                        "Python", [("Dictionaries", 0.88, ["Lists"])]),
    "python/decorators.md": ("@cache above a function – what does it do?",
                             "Python", [("Decorators", 0.4, [])]),
    "csharp/stack.cs": ("var s = new Stack<int>();\ns.Push(1);\ns.Pop();",
                        "C#", [("Stack", 0.9, [])]),
}


def _analysis_for(chunks, known):
    text = chunks[0]["text"]
    for _path, (body, subject, topics) in FILES.items():
        if body == text:
            match = next((s["id"] for s in known if s["name"] == subject), None)
            return {"subject": {"name": subject, "matched_subject_id": match, "confidence": 0.9},
                    "topics": [{"name": n, "confidence": c, "chunk_ids": ["c0"], "prerequisites": pre}
                               for n, c, pre in topics],
                    "evidence": [{"topic": n, "type": "exposure", "authorship": "unknown", "chunk_id": "c0"}
                                 for n, _c, _p in topics]}
    return {"subject": {"name": "Unknown", "confidence": 0.3}, "topics": [], "evidence": []}


def demo_envelope(rel_path: str, text: str) -> dict:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = hashlib.sha256(f"{DEMO_SOURCE}|demo|{sha}".encode()).hexdigest()
    return {"schema_version": 1, "envelope_id": "env-" + key[:16], "idempotency_key": key,
            "source": {"type": "folder", "source_id": DEMO_SOURCE, "grant_id": "demo"},
            "file": {"rel_path": rel_path, "sha256": sha, "size": len(text), "change": "new"},
            "filter": {"status": "clean", "redactions": []}, "extract": {"reader": "text", "warnings": []},
            "chunks": [{"chunk_id": "c0", "index": 0, "total": 1, "location": "lines 1-200", "text": text}],
            "content_trust": "untrusted_user_file"}


def demo_model(provider_config=None):
    """FakeModel by default. With a provider config (local_data/providers.json), the demo runs
    through that provider's adapter – offline with "transport": "mock"."""
    if provider_config is None or provider_config.provider == "fake":
        return FakeModel(_analysis_for)
    from brain.providers import build_gateway
    return build_gateway(provider_config, analysis_script=_analysis_for)


def build_demo_app(db_path: str | None = None, clock=None, engine: str = "plain",
                   model=None) -> LearnLoopApp:
    store = None
    if db_path:
        from brain.storage.crypto import AesGcmCipher, DevKeyFile
        from brain.storage.db import SqliteDatabase
        from brain.storage.store import Store
        store = Store(SqliteDatabase(db_path), AesGcmCipher(DevKeyFile(LOCAL_DATA / "dev.key").key()))
    kwargs = {"clock": clock} if clock else {}
    app = LearnLoopApp(model or FakeModel(_analysis_for), engine=engine, store=store, **kwargs)
    if app.repo.history:                       # loaded from the database – do not ingest again
        return app
    app.set_transfer_permission(DEMO_SOURCE, True)
    # Python first; the remaining files arrive after the first approval in the UI.
    for rel, (text, _s, _t) in FILES.items():
        app.receive(demo_envelope(rel, text))
    app.run_cycle()
    app.commit()
    return app

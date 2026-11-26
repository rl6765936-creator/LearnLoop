"""B6: migrations, save/load of the whole brain, encryption at rest, append-only history,
encrypted backup and restore. Local SQLite only – no server, no production keys."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from brain.app import LearnLoopApp
from brain.model_gateway import FakeModel
from brain.storage.backup import create_backup, restore_backup
from brain.storage.crypto import AesGcmCipher, DevKeyFile, PlaintextCipher, crypto_available
from brain.storage.db import SqliteDatabase, migrate
from brain.storage.schema import migration_v1
from brain.storage.store import StorageError, Store
from helpers import SOURCE, analysis, envelope

SECRET_TEXT = "UNIQUE-STUDY-TEXT-4711"
SUBJECT = "Astronomy-Synthetic"


def make_app(store, now="2026-11-24T10:00:00+00:00"):
    app = LearnLoopApp(FakeModel(analysis(subject=SUBJECT, topics=(("Orbits", 0.9),))),
                       clock=lambda: now, engine="plain", store=store)
    return app


class Base(unittest.TestCase):
    cipher_factory = staticmethod(lambda: PlaintextCipher())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "brain.db"
        self.cipher = self.cipher_factory()
        self._open = []

    def tearDown(self):
        for db in self._open:          # Windows cannot delete open database files
            db.close()
        self.tmp.cleanup()

    def store(self, path=None, cipher=None):
        db = SqliteDatabase(path or self.path)
        self._open.append(db)
        return Store(db, cipher or self.cipher)

    def connect(self):
        con = sqlite3.connect(self.path)
        self._open.append(con)
        return con

    def ingest_and_wait(self, app):
        app.set_transfer_permission(SOURCE, True)
        app.receive(envelope([SECRET_TEXT], rel_path="space/orbits.md"))
        app.run_cycle()
        app.commit()


class TestMigrations(unittest.TestCase):
    def test_migrations_are_applied_once(self):
        db = SqliteDatabase()
        self.assertEqual(migrate(db), [1, 2, 3, 4])
        self.assertEqual(migrate(db), [])
        db.close()

    def test_existing_v1_database_is_upgraded_and_still_loads(self):
        from brain.storage.schema import migration_v1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            db = SqliteDatabase(path)
            db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT)")
            for stmt in migration_v1("sqlite"):
                db.execute(stmt)
            db.execute("INSERT INTO schema_migrations VALUES (1, 'initial schema')")
            db.conn.commit()
            self.assertEqual(migrate(db), [2, 3, 4])
            cols = [r[1] for r in db.query("PRAGMA table_info(topics)")]
            self.assertIn("confidence", cols)
            db.close()

    def test_same_schema_for_postgres(self):
        sql = " ".join(migration_v1("postgres"))
        self.assertIn("BYTEA", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS change_log", sql)


class TestPersistence(Base):
    def test_pending_approval_survives_a_restart(self):
        app = make_app(self.store())
        self.ingest_and_wait(app)
        restarted = make_app(self.store())
        pending = restarted.pending_approvals()
        self.assertEqual([p["kind"] for p in pending], ["new_subject"])
        self.assertEqual(restarted.decide(pending[0]["id"], "approve")["status"], "applied")
        restarted.commit()
        again = make_app(self.store())
        self.assertEqual([s["name"] for s in again.subject_spaces()], [SUBJECT])
        self.assertEqual(again.pending_approvals(), [])

    def test_activity_in_progress_continues_after_restart(self):
        app = make_app(self.store())
        self.ingest_and_wait(app)
        app.decide(app.pending_approvals()[0]["id"], "approve")
        topic_id = next(iter(app.repo.topics))
        activity = app.start_activity(topic_id, "quiz")
        app.answer(activity["id"], "q1", 1)
        app.commit()
        later = make_app(self.store())
        later.answer(activity["id"], "q2", "Orbits key idea")
        result = later.finish_activity(activity["id"])
        self.assertEqual(result["answered"], 2)

    def test_ids_continue_after_restart(self):
        app = make_app(self.store())
        self.ingest_and_wait(app)
        used = set(app.repo.subjects) | set(app.repo.topics)
        later = make_app(self.store())
        self.assertNotIn(later.repo.new_id("topic"), used)

    def test_history_is_append_only_in_the_database(self):
        app = make_app(self.store())
        self.ingest_and_wait(app)
        first = self.connect().execute("SELECT seq, detail FROM change_log").fetchall()
        app.decide(app.pending_approvals()[0]["id"], "approve")
        app.commit()
        second = dict(self.connect().execute("SELECT seq, detail FROM change_log").fetchall())
        self.assertGreater(len(second), len(first))
        for seq, detail in first:
            self.assertEqual(second[seq], detail)                    # old rows untouched

    def test_waiting_langgraph_threads_are_not_silently_lost(self):
        app = make_app(self.store())
        app.set_transfer_permission(SOURCE, True)
        app.receive(envelope([SECRET_TEXT]))
        app.run_cycle()
        app.supervisor.engine = object()                            # an engine without durable pause store
        with self.assertRaises(StorageError):
            app.commit()


@unittest.skipUnless(crypto_available(), "the 'cryptography' package is not installed")
class TestEncryptionAtRest(Base):
    cipher_factory = staticmethod(lambda: AesGcmCipher(os.urandom(32)))

    def test_no_content_or_names_in_the_database_file(self):
        app = make_app(self.store())
        self.ingest_and_wait(app)
        app.decide(app.pending_approvals()[0]["id"], "approve")
        app.commit()
        raw = self.path.read_bytes()
        for secret in (SECRET_TEXT, SUBJECT, "Orbits", "space/orbits.md"):
            self.assertNotIn(secret.encode(), raw, secret)

    def test_wrong_key_cannot_read(self):
        app = make_app(self.store())
        self.ingest_and_wait(app)
        with self.assertRaises(Exception):
            make_app(self.store(cipher=AesGcmCipher(os.urandom(32))))

    def test_value_moved_to_another_row_is_detected(self):
        app = make_app(self.store())
        self.ingest_and_wait(app)
        app.decide(app.pending_approvals()[0]["id"], "approve")
        app.commit()
        con = self.connect()
        subject_name = con.execute("SELECT name FROM subjects").fetchone()[0]
        con.execute("UPDATE topics SET name = ?", (subject_name,))
        con.commit()
        with self.assertRaises(Exception):
            make_app(self.store())

    def test_dev_key_file_is_created_once_and_reused(self):
        keyfile = DevKeyFile(Path(self.tmp.name) / "dev.key")
        self.assertEqual(keyfile.key(), keyfile.key())
        self.assertEqual(len(keyfile.key()), 32)


@unittest.skipUnless(crypto_available(), "the 'cryptography' package is not installed")
class TestBackup(Base):
    cipher_factory = staticmethod(lambda: AesGcmCipher(os.urandom(32)))

    def setUp(self):
        super().setUp()
        self.app = make_app(self.store())
        self.ingest_and_wait(self.app)
        self.app.decide(self.app.pending_approvals()[0]["id"], "approve")
        self.app.commit()
        self.backup_cipher = AesGcmCipher(os.urandom(32))
        self.file = Path(self.tmp.name) / "brain.backup"

    def test_restore_to_a_new_computer_with_another_key(self):
        info = create_backup(self.store(), self.backup_cipher, self.file)
        self.assertFalse(info["chunk_text_included"])
        new_path = Path(self.tmp.name) / "new.db"
        new_store = self.store(new_path, AesGcmCipher(os.urandom(32)))
        restore_backup(self.file, self.backup_cipher, new_store)
        restored = make_app(new_store)
        self.assertEqual([s["name"] for s in restored.subject_spaces()], [SUBJECT])
        self.assertEqual(len(restored.repo.history), len(self.app.repo.history))
        self.assertEqual({c.text for c in restored.repo.chunks.values()}, {None})   # study text left out

    def test_backup_file_is_encrypted_and_tamper_proof(self):
        create_backup(self.store(), self.backup_cipher, self.file)
        raw = self.file.read_bytes()
        for secret in (SECRET_TEXT, SUBJECT, "Orbits"):
            self.assertNotIn(secret.encode(), raw)
        tampered = Path(self.tmp.name) / "bad.backup"
        tampered.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaises(Exception):
            restore_backup(tampered, self.backup_cipher, self.store(Path(self.tmp.name) / "x.db"))
        with self.assertRaises(Exception):
            restore_backup(self.file, AesGcmCipher(os.urandom(32)), self.store(Path(self.tmp.name) / "y.db"))


if __name__ == "__main__":
    unittest.main()

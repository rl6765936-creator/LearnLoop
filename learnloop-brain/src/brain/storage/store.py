"""Store – saves and loads the whole brain state through a Database adapter.

What is stored (brain-design §5): knowledge graph, evidence, content and chunks, the
APPEND-ONLY change log, idempotency keys, per-source permissions, activities, pending
approval requests, paused workflow states (plain engine) and the inbox.

Encryption: every column marked `enc` in schema.py is encrypted with the Cipher, bound to
table + row + column. Ids, states, dates and kinds stay readable so the database can be
queried; names, texts, answers and history details are encrypted.

save() writes everything in ONE transaction (all or nothing). The change log is only
ever appended to: rows that exist are never updated or deleted.
"""

from __future__ import annotations

import hashlib
import itertools
import base64
import json
from dataclasses import asdict

from brain.activities import Activity
from brain.domain.models import ChangeRecord, Chunk, ContentItem, Edge, Evidence, Skill, StudyContext, Subject, Topic
from brain.storage.db import migrate


class StorageError(RuntimeError):
    pass


def _path_key(source_id: str, rel_path: str) -> str:
    return hashlib.sha256(f"{source_id}\x00{rel_path}".encode("utf-8")).hexdigest()


class Store:
    def __init__(self, db, cipher):
        self.db, self.cipher = db, cipher
        migrate(db)

    # ------------------------------------------------------------ encryption helpers
    def _enc(self, table: str, row: str, column: str, value) -> bytes:
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                                   f"{table}:{row}:{column}")

    def _dec(self, table: str, row: str, column: str, blob):
        return json.loads(self.cipher.decrypt(bytes(blob), f"{table}:{row}:{column}").decode("utf-8"))

    # ------------------------------------------------------------ save
    def save(self, app) -> None:
        repo, center = app.repo, app.supervisor.center
        waiting = [t for t, s in app.supervisor.thread_status.items() if s == "waiting_for_user"]
        paused = getattr(app.supervisor.engine, "_paused", None)
        if waiting and paused is None:
            raise StorageError("pending LangGraph threads need a durable checkpointer "
                               "(PostgreSQL – decision Q-B); use the plain engine for local storage")
        next_id = next(repo._ids)
        repo._ids = itertools.count(next_id)
        db, E = self.db, self._enc
        with db.transaction():
            for table in ("subjects", "topics", "edges", "evidence", "content_items", "chunks",
                          "applied_keys", "source_permissions", "activities", "approval_requests",
                          "paused_threads", "inbox", "meta", "study_contexts", "classification_requests", "skills"):
                db.execute(f"DELETE FROM {table}")          # change_log is NOT in this list
            for s in repo.subjects.values():
                db.execute("INSERT INTO subjects VALUES (?, ?, ?)", (s.id, E("subjects", s.id, "name", s.name), s.approval_id))
            for t in repo.topics.values():
                db.execute("INSERT INTO topics (id, subject_id, name, state, score, confidence) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (t.id, t.subject_id, E("topics", t.id, "name", t.name), t.state, t.score,
                            t.confidence))
            for e in repo.edges:
                db.execute("INSERT INTO edges VALUES (?, ?, ?)", (e.src, e.dst, e.kind))
            for v in repo.evidence.values():
                db.execute("INSERT INTO evidence (id, topic_id, type, authorship, chunk_id, counts_as_user_ability, "
                           "independence, score, occurred_at, source, activity_id, note, context_id, skill_id) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (v.id, v.topic_id, v.type, v.authorship, v.chunk_id, v.counts_as_user_ability,
                            v.independence, v.score, v.occurred_at, v.source, v.activity_id,
                            E("evidence", v.id, "note", v.note), v.context_id, v.skill_id))
            for k in repo.skills.values():
                db.execute("INSERT INTO skills VALUES (?, ?, ?)",
                           (k.id, k.topic_id, E("skills", k.id, "data", {"name": k.name})))
            for c in repo.study_contexts.values():
                db.execute("INSERT INTO study_contexts VALUES (?, ?, ?, ?, ?, ?)",
                           (c.id, c.source_id, c.subject_id, c.study_status, c.decided_by,
                            E("study_contexts", c.id, "data", {"collection": c.collection, "period": c.period})))
            for qid, q in repo.classification_requests.items():
                db.execute("INSERT INTO classification_requests VALUES (?, ?, ?)",
                           (qid, q["status"], E("classification_requests", qid, "data", q)))
            for item in repo.content.values():
                key = _path_key(item.source_id, item.rel_path)
                db.execute("INSERT INTO content_items VALUES (?, ?, ?, ?, ?)",
                           (item.source_id, E("content_items", key, "rel_path", item.rel_path), key,
                            item.sha256, item.status))
            for c in repo.chunks.values():
                db.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                           (c.chunk_id, c.content_sha, E("chunks", c.chunk_id, "location", c.location),
                            E("chunks", c.chunk_id, "text", c.text), c.status))
            known = {row[0] for row in db.query("SELECT seq FROM change_log")}
            for r in repo.history:
                if r.seq in known:
                    continue                                   # append-only
                detail = {"before": r.before, "after": r.after, "reason": r.reason,
                          "evidence_ids": r.evidence_ids}
                db.execute("INSERT INTO change_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (r.seq, r.at, r.entity, r.entity_id, r.action, r.mode, r.approval_id,
                            E("change_log", str(r.seq), "detail", detail)))
            for key in repo._applied:
                db.execute("INSERT INTO applied_keys VALUES (?)", (key,))
            for source, allowed in repo.transfer_allowed.items():
                db.execute("INSERT INTO source_permissions VALUES (?, ?)", (source, allowed))
            for a in getattr(repo, "activities", {}).values():
                db.execute("INSERT INTO activities VALUES (?, ?, ?, ?)",
                           (a.id, a.subject_id, a.status, E("activities", a.id, "data", asdict(a))))
            for rid, r in center._requests.items():
                db.execute("INSERT INTO approval_requests VALUES (?, ?, ?, ?)",
                           (rid, r["thread_id"], rid in center._decisions,
                            E("approval_requests", rid, "data",
                              {"request": r, "decision": center._decisions.get(rid)})))
            for thread, state in (paused or {}).items():
                db.execute("INSERT INTO paused_threads VALUES (?, ?)",
                           (thread, E("paused_threads", thread, "data", state)))
            for pos, env in enumerate(app.ingestion.inbox):
                key = env["idempotency_key"]
                db.execute("INSERT INTO inbox VALUES (?, ?, ?)", (key, pos, E("inbox", key, "data", env)))
            for k, v in (("next_id", str(next_id)), ("thread_status", json.dumps(app.supervisor.thread_status)),
                         ("cipher", self.cipher.name),
                         ("pending_prerequisites", json.dumps(repo.pending_prerequisites)),
                         # ActivityWatch day summaries (app names → encrypted, Q12)
                         ("activity", base64.b64encode(E("meta", "activity", "value",
                                                         getattr(app, "activity_days", {}))).decode("ascii")),
                         ("app_links", base64.b64encode(E("meta", "app_links", "value",
                                                          getattr(app, "app_links", {}))).decode("ascii"))):
                db.execute("INSERT INTO meta VALUES (?, ?)", (k, v))

    # ------------------------------------------------------------ load
    def load(self, app) -> bool:
        """Fill a freshly created app from the database. False if the database is empty."""
        meta = dict(self.db.query("SELECT key, value FROM meta"))
        if not meta:
            return False
        if meta.get("cipher") != self.cipher.name:
            raise StorageError("database was written with a different cipher")
        repo, D = app.repo, self._dec
        q = self.db.query
        repo.subjects = {i: Subject(i, D("subjects", i, "name", n), a) for i, n, a in q("SELECT * FROM subjects")}
        repo.topics = {i: Topic(i, s, D("topics", i, "name", n), st, sc, cf) for i, s, n, st, sc, cf in
                       q("SELECT id, subject_id, name, state, score, confidence FROM topics")}
        repo.edges = {Edge(*row) for row in q("SELECT * FROM edges")}
        repo.evidence = {}
        for (i, t, ty, au, ch, cnt, ind, sc, oc, so, act, note, ctx, sk) in q(
                "SELECT id, topic_id, type, authorship, chunk_id, counts_as_user_ability, independence, score, "
                "occurred_at, source, activity_id, note, context_id, skill_id FROM evidence"):
            repo.evidence[i] = Evidence(i, t, ty, au, ch, bool(cnt), D("evidence", i, "note", note), ind, sc, oc, so,
                                        act, ctx, sk)
        repo.skills = {i: Skill(i, tid, D("skills", i, "data", data)["name"])
                       for i, tid, data in q("SELECT * FROM skills")}
        repo.study_contexts = {}
        for i, src, subj, status, by, data in q("SELECT * FROM study_contexts"):
            d = D("study_contexts", i, "data", data)
            ctx = StudyContext(i, src, d["collection"], subj, status, d["period"], by)
            repo.study_contexts[(src, ctx.collection, subj)] = ctx
        repo.classification_requests = {i: D("classification_requests", i, "data", data)
                                        for i, _st, data in q("SELECT * FROM classification_requests")}
        repo.content = {}
        for source, rel, key, sha, status in q("SELECT * FROM content_items"):
            path = D("content_items", key, "rel_path", rel)
            repo.content[(source, path)] = ContentItem(source, path, sha, status)
        repo.chunks = {i: Chunk(i, sha, D("chunks", i, "location", loc), D("chunks", i, "text", txt), st)
                       for i, sha, loc, txt, st in q("SELECT * FROM chunks")}
        repo.history = []
        for seq, at, entity, eid, action, mode, appr, detail in q("SELECT * FROM change_log ORDER BY seq"):
            d = D("change_log", str(seq), "detail", detail)
            repo.history.append(ChangeRecord(seq, at, entity, eid, action, d["before"], d["after"], d["reason"],
                                             mode, appr, d["evidence_ids"]))
        repo._applied = {row[0] for row in q("SELECT key FROM applied_keys")}
        repo.transfer_allowed = {s: bool(a) for s, a in q("SELECT * FROM source_permissions")}
        repo.activities = {i: Activity(**D("activities", i, "data", data)) for i, _s, _st, data in q("SELECT * FROM activities")}
        repo._ids = itertools.count(int(meta["next_id"]))
        repo.pending_prerequisites = [tuple(x) for x in json.loads(meta.get("pending_prerequisites", "[]"))]
        center = app.supervisor.center
        center._requests, center._decisions = {}, {}
        for rid, _t, _d, data in q("SELECT * FROM approval_requests"):
            d = D("approval_requests", rid, "data", data)
            center._requests[rid] = d["request"]
            if d["decision"] is not None:
                center._decisions[rid] = d["decision"]
        paused = getattr(app.supervisor.engine, "_paused", None)
        if paused is not None:
            paused.clear()
            paused.update({t: D("paused_threads", t, "data", data) for t, data in q("SELECT * FROM paused_threads")})
        app.supervisor.thread_status = json.loads(meta.get("thread_status", "{}"))
        if meta.get("activity"):
            app.activity_days = D("meta", "activity", "value", base64.b64decode(meta["activity"]))
        if meta.get("app_links"):
            app.app_links = D("meta", "app_links", "value", base64.b64decode(meta["app_links"]))
        app.ingestion.inbox = [D("inbox", k, "data", data)
                               for k, _p, data in q("SELECT * FROM inbox ORDER BY position")]
        return True

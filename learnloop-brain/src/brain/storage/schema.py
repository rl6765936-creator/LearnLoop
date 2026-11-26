"""Database schema and migrations – ONE definition for SQLite and PostgreSQL.

Columns marked enc hold ciphertext (BLOB in SQLite, BYTEA in PostgreSQL).
Migrations are applied in order and recorded in schema_migrations; applying twice is safe.
"""

from __future__ import annotations

# (table, [(column, type, extra)]) – type in: text, int, real, bool, enc
TABLES_V1 = [
    ("subjects", [("id", "text", "PRIMARY KEY"), ("name", "enc", ""), ("approval_id", "text", "")]),
    ("topics", [("id", "text", "PRIMARY KEY"), ("subject_id", "text", "NOT NULL"), ("name", "enc", ""),
                ("state", "text", "NOT NULL"), ("score", "real", "")]),
    ("edges", [("src", "text", "NOT NULL"), ("dst", "text", "NOT NULL"), ("kind", "text", "NOT NULL")]),
    ("evidence", [("id", "text", "PRIMARY KEY"), ("topic_id", "text", "NOT NULL"), ("type", "text", "NOT NULL"),
                  ("authorship", "text", "NOT NULL"), ("chunk_id", "text", ""),
                  ("counts_as_user_ability", "bool", "NOT NULL"), ("independence", "text", ""),
                  ("score", "real", ""), ("occurred_at", "text", ""), ("source", "text", ""),
                  ("activity_id", "text", ""), ("note", "enc", "")]),
    ("content_items", [("source_id", "text", "NOT NULL"), ("rel_path", "enc", ""), ("path_key", "text", "NOT NULL"),
                       ("sha256", "text", "NOT NULL"), ("status", "text", "NOT NULL")]),
    ("chunks", [("chunk_id", "text", "PRIMARY KEY"), ("content_sha", "text", "NOT NULL"),
                ("location", "enc", ""), ("text", "enc", ""), ("status", "text", "NOT NULL")]),
    ("change_log", [("seq", "int", "PRIMARY KEY"), ("at", "text", "NOT NULL"), ("entity", "text", "NOT NULL"),
                    ("entity_id", "text", "NOT NULL"), ("action", "text", "NOT NULL"), ("mode", "text", "NOT NULL"),
                    ("approval_id", "text", ""), ("detail", "enc", "")]),
    ("applied_keys", [("key", "text", "PRIMARY KEY")]),
    ("source_permissions", [("source_id", "text", "PRIMARY KEY"), ("transfer_allowed", "bool", "NOT NULL")]),
    ("activities", [("id", "text", "PRIMARY KEY"), ("subject_id", "text", "NOT NULL"), ("status", "text", "NOT NULL"),
                    ("data", "enc", "")]),
    ("approval_requests", [("id", "text", "PRIMARY KEY"), ("thread_id", "text", "NOT NULL"),
                           ("decided", "bool", "NOT NULL"), ("data", "enc", "")]),
    ("paused_threads", [("thread_id", "text", "PRIMARY KEY"), ("data", "enc", "")]),
    ("inbox", [("idempotency_key", "text", "PRIMARY KEY"), ("position", "int", "NOT NULL"), ("data", "enc", "")]),
    ("meta", [("key", "text", "PRIMARY KEY"), ("value", "text", "")]),
]

INDEXES_V1 = [
    "CREATE INDEX IF NOT EXISTS ix_topics_subject ON topics (subject_id)",
    "CREATE INDEX IF NOT EXISTS ix_evidence_topic ON evidence (topic_id)",
    "CREATE INDEX IF NOT EXISTS ix_change_entity ON change_log (entity_id)",
]

TYPES = {
    "sqlite": {"text": "TEXT", "int": "INTEGER", "real": "REAL", "bool": "INTEGER", "enc": "BLOB"},
    "postgres": {"text": "TEXT", "int": "BIGINT", "real": "DOUBLE PRECISION", "bool": "BOOLEAN", "enc": "BYTEA"},
}


def migration_v1(dialect: str) -> list[str]:
    types = TYPES[dialect]
    stmts = []
    for table, cols in TABLES_V1:
        body = ", ".join(f"{c} {types[t]} {extra}".strip() for c, t, extra in cols)
        stmts.append(f"CREATE TABLE IF NOT EXISTS {table} ({body})")
    return stmts + INDEXES_V1


# v2 (2026-11-24): confidence of the approved mastery formula.
ADDED_V2 = {"topics": [("confidence", "real", "")]}


def migration_v2(dialect: str) -> list[str]:
    types = TYPES[dialect]
    return [f"ALTER TABLE {table} ADD COLUMN {c} {types[t]}"
            for table, cols in ADDED_V2.items() for c, t, _x in cols]


# v3 (2026-11-24): study contexts, grouped classification questions, evidence -> context.
TABLES_V3 = [
    ("study_contexts", [("id", "text", "PRIMARY KEY"), ("source_id", "text", "NOT NULL"),
                        ("subject_id", "text", "NOT NULL"), ("study_status", "text", "NOT NULL"),
                        ("decided_by", "text", "NOT NULL"), ("data", "enc", "")]),   # data: folder, period
    ("classification_requests", [("id", "text", "PRIMARY KEY"), ("status", "text", "NOT NULL"),
                                 ("data", "enc", "")]),
]
ADDED_V3 = {"evidence": [("context_id", "text", "")]}


def migration_v3(dialect: str) -> list[str]:
    types = TYPES[dialect]
    stmts = []
    for table, cols in TABLES_V3:
        body = ", ".join(f"{c} {types[t]} {extra}".strip() for c, t, extra in cols)
        stmts.append(f"CREATE TABLE IF NOT EXISTS {table} ({body})")
    return stmts + [f"ALTER TABLE {table} ADD COLUMN {c} {types[t]}"
                    for table, cols in ADDED_V3.items() for c, t, _x in cols]


# v4 (2026-11-24): spec §13 Skill entity (partOf a topic) and evidence -> skill.
TABLES_V4 = [("skills", [("id", "text", "PRIMARY KEY"), ("topic_id", "text", "NOT NULL"), ("data", "enc", "")])]
ADDED_V4 = {"evidence": [("skill_id", "text", "")]}


def migration_v4(dialect: str) -> list[str]:
    types = TYPES[dialect]
    stmts = []
    for table, cols in TABLES_V4:
        body = ", ".join(f"{c} {types[t]} {extra}".strip() for c, t, extra in cols)
        stmts.append(f"CREATE TABLE IF NOT EXISTS {table} ({body})")
    return stmts + [f"ALTER TABLE {table} ADD COLUMN {c} {types[t]}"
                    for table, cols in ADDED_V4.items() for c, t, _x in cols]


MIGRATIONS = [(1, "initial schema", migration_v1), (2, "topic confidence", migration_v2),
              (3, "study contexts", migration_v3), (4, "skills", migration_v4)]


def current_tables() -> list[tuple[str, list]]:
    """Table columns after ALL migrations (used by backup / restore)."""
    return [(t, cols + ADDED_V2.get(t, []) + ADDED_V3.get(t, []) + ADDED_V4.get(t, []))
            for t, cols in TABLES_V1 + TABLES_V3 + TABLES_V4]


def encrypted_columns() -> dict[str, list[str]]:
    return {t: [c for c, typ, _ in cols if typ == "enc"] for t, cols in current_tables()}

# Which column identifies a row – used to bind encrypted values to their row.
ROW_KEY = {"subjects": "id", "topics": "id", "evidence": "id", "content_items": "path_key",
           "chunks": "chunk_id", "change_log": "seq", "activities": "id", "approval_requests": "id",
           "paused_threads": "thread_id", "inbox": "idempotency_key", "study_contexts": "id",
           "classification_requests": "id", "skills": "id"}

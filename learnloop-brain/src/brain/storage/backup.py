"""Encrypted backups (spec §38 APPROVED: automatic encrypted backups of LearnLoop data,
NOT of the original study files).

A backup is the logical content of every table, encrypted AS A WHOLE with a backup cipher
(AES-GCM, authenticated: a changed file is detected and refused). Restoring re-encrypts the
fields with the target database's own cipher, so a backup can move to a new computer.

Chunk TEXTS (filtered copies of study material) are left out by default [RECOMMENDATION]:
they are excerpts of the user's files and can be sent again by the Collector. Scheduling
("automatic") and where backups are kept depend on the server decision (Q-B) and key
management [OPEN].
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from brain.storage.schema import ROW_KEY, current_tables

MAGIC = b"LLBK1"
CONTEXT = "learnloop-backup-v1"


def _rows(store, table: str, cols: list) -> list[dict]:
    names = [c for c, _t, _x in cols]
    out = []
    for row in store.db.query(f"SELECT {', '.join(names)} FROM {table}"):
        rec = dict(zip(names, row))
        key = str(rec[ROW_KEY[table]]) if table in ROW_KEY else ""
        for c, typ, _x in cols:
            if typ == "enc" and rec[c] is not None:
                rec[c] = store._dec(table, key, c, rec[c])
        if table == "meta" and rec.get("key") in ENCRYPTED_META:      # inner layer -> plain, inside the backup cipher
            rec["value"] = {"__plain__": store._dec("meta", rec["key"], "value", base64.b64decode(rec["value"]))}
        out.append(rec)
    return out


ENCRYPTED_META = ("activity", "app_links")


def create_backup(store, backup_cipher, path: Path, include_chunk_text: bool = False) -> dict:
    data = {"format": 1, "tables": {}}
    for table, cols in current_tables():
        rows = _rows(store, table, cols)
        if table == "chunks" and not include_chunk_text:
            for r in rows:
                r["text"] = None
        data["tables"][table] = rows
    blob = json.dumps(data, ensure_ascii=False, default=lambda b: base64.b64encode(b).decode()).encode("utf-8")
    Path(path).write_bytes(MAGIC + backup_cipher.encrypt(blob, CONTEXT))
    return {"tables": len(data["tables"]), "rows": sum(len(r) for r in data["tables"].values()),
            "chunk_text_included": include_chunk_text}


def restore_backup(path: Path, backup_cipher, target_store) -> dict:
    raw = Path(path).read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("not a LearnLoop backup")
    data = json.loads(backup_cipher.decrypt(raw[len(MAGIC):], CONTEXT).decode("utf-8"))
    db, count = target_store.db, 0
    with db.transaction():
        for table, cols in current_tables():
            db.execute(f"DELETE FROM {table}")
            names = [c for c, _t, _x in cols]
            for rec in data["tables"].get(table, []):
                key = str(rec[ROW_KEY[table]]) if table in ROW_KEY else ""
                values = []
                for c, typ, _x in cols:
                    v = rec.get(c)
                    if typ == "enc":
                        v = target_store._enc(table, key, c, v)
                    elif table == "meta" and c == "value" and isinstance(v, dict) and "__plain__" in v:
                        v = base64.b64encode(target_store._enc("meta", rec["key"], "value", v["__plain__"])).decode("ascii")
                    values.append(v)
                db.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                           tuple(values))
                count += 1
    return {"rows": count}

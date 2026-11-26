"""Build TransferEnvelopes (contract: contracts/transfer_envelope.schema.json, shared with
the brain). Pure functions – no file access, no network.

Spec §35 / Q1 (APPROVED): reasonable size -> whole file; very large files -> chunks that
keep their source location (page, slide, lines); grouping is technical only (by file).
"""

from __future__ import annotations

import hashlib

MAX_CHUNK_CHARS = 20_000           # [ASSUMPTION] one chunk = up to ~20k characters
MAX_CHUNKS_PER_ENVELOPE = 40       # [ASSUMPTION] bigger files are split into several envelopes


def source_id(folder: str) -> str:
    """Stable id of the folder source that does NOT reveal the folder path."""
    return "folder-" + hashlib.sha256(folder.encode("utf-8")).hexdigest()[:16]


def make_chunks(segments: list[dict]) -> list[dict]:
    """Join small consecutive segments, split very long ones; keep where the text came from."""
    chunks, buf, locs = [], [], []

    def flush():
        if buf:
            location = locs[0] if len(locs) == 1 else f"{locs[0]} – {locs[-1]}"
            chunks.append({"location": location[:256], "text": "\n\n".join(buf)})
            buf.clear()
            locs.clear()

    for seg in segments:
        text, loc = seg["text"], seg["location"]
        if len(text) > MAX_CHUNK_CHARS:
            flush()
            for i in range(0, len(text), MAX_CHUNK_CHARS):
                part = i // MAX_CHUNK_CHARS + 1
                chunks.append({"location": f"{loc} (part {part})"[:256], "text": text[i:i + MAX_CHUNK_CHARS]})
            continue
        if sum(len(b) for b in buf) + len(text) > MAX_CHUNK_CHARS:
            flush()
        buf.append(text)
        locs.append(loc)
    flush()
    for i, c in enumerate(chunks):
        c.update({"chunk_id": f"c{i}", "index": i, "total": len(chunks)})
    return chunks


def _key(src: str, grant: str | None, sha: str, change: str, first: int) -> str:
    return hashlib.sha256(f"{src}|{grant}|{sha}|{change}|{first}".encode("utf-8")).hexdigest()


def build_envelopes(entry: dict, src: str, grant_id: str | None, change: str,
                    previous_rel_path: str | None = None, context: dict | None = None) -> list[dict]:
    """One outbox entry -> one or more envelopes (several only for very large files)."""
    chunks = make_chunks(entry.get("segments", []))
    groups = [chunks[i:i + MAX_CHUNKS_PER_ENVELOPE] for i in range(0, len(chunks), MAX_CHUNKS_PER_ENVELOPE)] or [[]]
    text_size = sum(len(c["text"]) for c in chunks)
    envelopes = []
    for group in groups:
        first = group[0]["index"] if group else 0
        key = _key(src, grant_id, entry["sha256"], change, first)
        env = {
            "schema_version": 1,
            "envelope_id": "env-" + key[:16],
            "idempotency_key": key,
            "source": {"type": "folder", "source_id": src, "grant_id": grant_id},
            "file": {"rel_path": entry["source"], "sha256": entry["sha256"], "size": text_size,
                     "change": change},
            "filter": {"status": entry.get("filter_status", "clean"),
                       "redactions": [{"kind": r["kind"], "location": r["location"]}
                                      for r in entry.get("redactions", [])]},
            "extract": {"reader": entry.get("reader", ""), "warnings": list(entry.get("warnings", []))},
            "chunks": group,
            "content_trust": "untrusted_user_file",
        }
        if previous_rel_path:
            env["file"]["previous_rel_path"] = previous_rel_path
        if context:
            # Explicit user classification + technical queue + file date (decision 2026-11-24).
            env["context"] = dict(context)
        envelopes.append(env)
    return envelopes


def removal_envelope(rel_path: str, sha: str, src: str, grant_id: str | None) -> dict:
    key = _key(src, grant_id, sha, "removed:" + rel_path, 0)
    return {"schema_version": 1, "envelope_id": "env-" + key[:16], "idempotency_key": key,
            "source": {"type": "folder", "source_id": src, "grant_id": grant_id},
            "file": {"rel_path": rel_path, "sha256": sha, "size": 0, "change": "removed"},
            "filter": {"status": "clean", "redactions": []}, "extract": {"reader": "", "warnings": []},
            "chunks": [], "content_trust": "untrusted_user_file"}

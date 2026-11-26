"""Live mode – the brain on HER computer with her own material (decisions 2026-11-24, Q8, Q16).

What it does:
- Opens (or creates) the encrypted local database  local_data/learnloop.db.
- The database key is protected by Windows DPAPI, with a recovery code shown ONCE at first run
  (storage/keystore.py). No development key file is used here.
- Creates a random local token (local_data/ingest.token). Only the local Collector reads it; the
  browser UI never gets it, so a web page cannot push material into the brain.
- Chooses the AI model from local_data/providers.json. If the provider is not approved for real
  material (paid tier + network + both locks), the brain HOLDS the material: it is received,
  stored encrypted, and nothing is sent anywhere (HoldModel).

No external server, no PostgreSQL (Q8). The API key is read by the server process from an
environment variable only and is never sent to the browser.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from brain.app import LearnLoopApp

LOCAL_DATA = Path(__file__).resolve().parents[2] / "local_data"


class HoldModel:
    """Stands in for an AI provider that is not approved yet. It is never asked anything:
    run_cycle sees hold_reason() and keeps the queue."""

    def __init__(self, reason: str):
        self._reason = reason

    budget = None                                # no paid provider -> no budget to show

    def hold_reason(self) -> str:
        return self._reason

    def __getattr__(self, name):
        # Any AI call would be a defect. AttributeError (not RuntimeError) so that plain
        # "does it have X?" checks – getattr(model, "x", None) – keep working (found 25.11).
        raise AttributeError(f"HoldModel has no '{name}' – material is on hold, no AI is called")


def provider_ready(cfg) -> str | None:
    """None when the configuration may receive REAL material; otherwise the reason it may not."""
    if cfg.provider == "fake":
        return "no_provider"
    if cfg.transport != "network" or not cfg.allow_network:
        return "network_locked"
    if cfg.account_tier == "free":
        return "free_tier_not_for_real_material"     # Q5: paid tier only
    return None


def live_model(cfg):
    reason = provider_ready(cfg)
    if reason:
        return HoldModel(reason)
    from brain.providers import build_gateway
    return build_gateway(cfg, purpose="application")


def ingest_token(directory: Path = LOCAL_DATA) -> str:
    path = Path(directory) / "ingest.token"
    if path.exists():
        return path.read_text(encoding="ascii").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="ascii")
    return token


def open_key(directory: Path = LOCAL_DATA, protector=None, recovery_code: str | None = None,
             create: bool = True):
    """Returns (key, new_recovery_code_or_None). The code is returned ONLY on first creation."""
    from brain.storage.keystore import KeyStore, KeyStoreError
    ks = KeyStore(Path(directory) / "keys", protector)
    if recovery_code:
        return ks.recover(recovery_code), None
    if ks.exists():
        return ks.load(), None
    if not create:
        raise KeyStoreError("no key on this computer – run with --recover and the recovery code")
    return ks.create()


def build_live_app(cfg, directory: Path = LOCAL_DATA, protector=None, recovery_code=None,
                   clock=None) -> tuple[LearnLoopApp, str | None]:
    from brain.storage.crypto import AesGcmCipher
    from brain.storage.db import SqliteDatabase
    from brain.storage.store import Store
    key, new_code = open_key(directory, protector, recovery_code)
    store = Store(SqliteDatabase(str(Path(directory) / "learnloop.db")), AesGcmCipher(key))
    kwargs = {"clock": clock} if clock else {}
    app = LearnLoopApp(live_model(cfg), engine="plain", store=store, **kwargs)
    app.mode = "live"
    return app, new_code


# --------------------------------------------------------------------------- backup / restore
def backup(app, key: bytes, path: Path, include_chunk_text: bool = True, directory: Path | None = None) -> dict:
    """Encrypted with the same data key – opening it needs this Windows user OR the recovery code.
    Default: WITH the stored text, so a restore really brings everything back."""
    from brain.storage.backup import create_backup
    from brain.storage.crypto import AesGcmCipher
    app.commit()
    info = create_backup(app.store, AesGcmCipher(key), Path(path), include_chunk_text)
    # The wrapped key travels WITH the backup (useless without the paper code); the code never does.
    rec = LOCAL_DATA / "keys" / "key.recovery" if directory is None else Path(directory) / "keys" / "key.recovery"
    if rec.exists():
        Path(str(path) + ".recovery").write_bytes(rec.read_bytes())
        info["recovery_file"] = Path(str(path) + ".recovery").name
    return info


def install_recovery_file(backup_path: Path, directory: Path = LOCAL_DATA) -> None:
    """New computer: put the wrapped key that came with the backup into keys/ (never overwrites)."""
    src, dst = Path(str(backup_path) + ".recovery"), Path(directory) / "keys" / "key.recovery"
    if dst.exists() or not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())


def restore(path: Path, key: bytes, directory: Path = LOCAL_DATA) -> dict:
    """Into a NEW database file only – never over an existing one (no deletion capability)."""
    from brain.storage.backup import restore_backup
    from brain.storage.crypto import AesGcmCipher
    from brain.storage.db import SqliteDatabase
    from brain.storage.store import Store
    target = Path(directory) / "learnloop.db"
    if target.exists():
        raise FileExistsError("a database already exists here – restore into an empty folder")
    store = Store(SqliteDatabase(str(target)), AesGcmCipher(key))
    return restore_backup(Path(path), AesGcmCipher(key), store)


def main(argv=None):
    """python -m brain.live --backup FILE | --restore FILE [--recover]"""
    import argparse
    import getpass
    from brain.providers import load_config
    p = argparse.ArgumentParser(prog="brain.live")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--backup", metavar="FILE")
    g.add_argument("--restore", metavar="FILE")
    p.add_argument("--recover", action="store_true", help="ask for the recovery code (new computer)")
    a = p.parse_args(argv)
    code = getpass.getpass("קוד שחזור: ") if a.recover else None
    if a.restore and a.recover:
        install_recovery_file(Path(a.restore))
    key, _ = open_key(LOCAL_DATA, recovery_code=code, create=False)
    if a.restore:
        print(restore(Path(a.restore), key))
        return 0
    if not (LOCAL_DATA / "learnloop.db").exists():
        print("לא נמצא מאגר לגיבוי."); return 2
    app, _ = build_live_app(load_config(None))
    print(backup(app, key, Path(a.backup)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

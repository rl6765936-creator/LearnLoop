"""Small LOCAL web server for development: serves the UI and the JSON API.

    python -m brain.api.server --demo            (synthetic data, FakeModel, in memory)
    python -m brain.api.server --demo --db FILE  (saved, encrypted with a DEVELOPMENT key)

Safety: listens on 127.0.0.1 only; accepts only Host localhost/127.0.0.1 (DNS-rebinding
guard); no CORS headers; every POST needs the header X-LearnLoop: 1 (a web page from another
site cannot add it without CORS); request bodies are limited. Not a production server
(backend framework, authentication and deployment are OPEN).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread

from brain.api.routes import handle

UI_DIR = Path(__file__).resolve().parents[3] / "ui"
MAX_BODY = 256 * 1024
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


MAX_ENVELOPE = 4 * 1024 * 1024       # one Collector envelope (up to 40 chunks of 20,000 chars)


def make_handler(app, lock: Lock | None = None, ingest_token: str | None = None):
    lock = lock or Lock()             # one request at a time: the app is single-user

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # no request logs (paths may contain ids only, but keep quiet)
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ALLOWED_HOSTS

        def _send(self, status: int, payload: dict | bytes, content_type="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            if self.path.startswith("/api/"):
                with lock:
                    return self._send(*handle(app, "GET", self.path))
            rel = self.path.split("?", 1)[0].lstrip("/") or "index.html"
            target = (UI_DIR / rel).resolve()
            if not str(target).startswith(str(UI_DIR.resolve())) or not target.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".js":
                ctype = "text/javascript"
            self._send(200, target.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-LearnLoop") != "1":
                return self._send(403, {"error": "forbidden"})
            length = int(self.headers.get("Content-Length") or 0)
            if self.path.startswith("/api/ingest"):
                return self._ingest(length)
            if length > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            with lock:
                self._send(*handle(app, "POST", self.path, body if isinstance(body, dict) else {}))

        def _ingest(self, length: int):
            """Local Collector -> brain (decision 2026-11-24, Q-D). Only with the shared local token,
            which the browser UI never has. Answers carry no content."""
            import hmac
            given = self.headers.get("X-LearnLoop-Token") or ""
            if not ingest_token or not hmac.compare_digest(given, ingest_token):
                return self._send(403, {"error": "forbidden"})
            if length > MAX_ENVELOPE:
                return self._send(413, {"error": "too large"})
            raw = self.rfile.read(length)
            with lock:
                if self.path == "/api/ingest":
                    ack = app.receive_from_collector(raw)
                    app.commit()
                    return self._send(200, ack)
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    return self._send(400, {"error": "invalid JSON"})
                if self.path == "/api/ingest/activity":
                    ack = app.receive_activity(body)
                    app.commit()
                    return self._send(200, ack)
                if self.path == "/api/ingest/revoke":
                    app.revoke_from_collector(str(body.get("source_id", "")))
                    app.commit()
                    return self._send(200, {"ok": True})
                if self.path == "/api/ingest/classifications":
                    return self._send(200, {"decided": app.decided_classifications()})
                return self._send(404, {"error": "not found"})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(prog="brain.api.server")
    parser.add_argument("--demo", action="store_true", help="synthetic data with the FakeModel")
    parser.add_argument("--live", action="store_true",
                        help="her own data: encrypted local database, DPAPI key, local Collector link")
    parser.add_argument("--recover", action="store_true", help="--live on a new computer: ask for the recovery code")
    parser.add_argument("--db", help="SQLite file (encrypted with a DEVELOPMENT key)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--providers", help="provider config JSON (default local_data/providers.json; "
                                            "absent = FakeModel). Real network use is not approved yet.")
    args = parser.parse_args(argv)
    if args.live == args.demo:
        parser.error("choose exactly one: --demo or --live")
    if args.live:
        return _main_live(args)
    from brain.demo import build_demo_app, demo_model
    from brain.providers import load_config
    cfg = load_config(args.providers)
    app = build_demo_app(db_path=args.db, model=demo_model(cfg))
    print(f"model: {cfg.provider} (transport: {cfg.transport if cfg.provider != 'fake' else '-'})")
    lock, stop = Lock(), Event()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app, lock))
    start_scheduler(app, lock, stop)
    print(f"LearnLoop (demo, synthetic data) – open http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


def _main_live(args):
    import getpass
    from brain.live import build_live_app, ingest_token, provider_ready
    from brain.providers import load_config
    cfg = load_config(args.providers)
    code = getpass.getpass("קוד שחזור: ") if args.recover else None
    app, new_code = build_live_app(cfg, recovery_code=code)
    if new_code:
        print("=" * 60)
        print("קוד שחזור לנתוני LearnLoop (לא קשור ל-Windows; שום דבר במחשב לא השתנה).")
        print("בשימוש רגיל במחשב הזה לא תצטרכי אותו – LearnLoop נפתח לבד.")
        print("הוא נדרש רק במחשב חדש או אחרי התקנה מחדש של Windows, כדי לפתוח את")
        print("הנתונים ש-LearnLoop שמר (מפת ידע, היסטוריה, תשובות). קבצי הלימודים שלך לא מושפעים.")
        print("רשמי אותו על נייר ושמרי בנפרד מהמחשב:")
        print("   " + new_code)
        print("=" * 60)
        input("לאחר שרשמת את הקוד, הקישי Enter להמשך...")
    hold = provider_ready(cfg)
    print("AI: " + ("ממתין – " + hold + " (שום חומר לא נשלח)" if hold else f"{cfg.provider} (תקציב חודשי ${cfg.monthly_budget_usd})"))
    lock, stop = Lock(), Event()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app, lock, ingest_token()))
    start_scheduler(app, lock, stop)
    print(f"LearnLoop – http://127.0.0.1:{args.port}  (Ctrl+C לעצירה)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        with lock:
            app.commit()
        server.server_close()


def start_scheduler(app, lock: Lock, stop: Event, every_s: float = 60.0) -> Thread:
    """spec §35: check once a minute whether the two-hour cycle or an early trigger is due.
    Runs only while this program runs ("while the computer is running")."""
    from brain.scheduler import CycleScheduler
    scheduler = CycleScheduler(app)
    app.scheduler = scheduler

    def loop():
        while not stop.wait(every_s):
            with lock:
                try:
                    scheduler.tick()
                except Exception as e:          # never crash the server; record the error kind only
                    app.repo.record("cycle", "scheduled", "error", None, {"error": type(e).__name__},
                                    "scheduler error", "autonomous")

    thread = Thread(target=loop, name="learnloop-scheduler", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    main()

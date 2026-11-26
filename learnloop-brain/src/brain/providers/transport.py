"""Transports: the only code that could ever send bytes to a provider.

    BlockedTransport   DEFAULT. Raises NetworkDisabled. Nothing leaves the computer.
    MockTransport      Tests and offline runs: a Python function plays the provider.
    NetworkTransport   Real HTTPS via the standard library. Built ONLY by
                       config.build_transport() when BOTH locks are open:
                       providers.json has "allow_network": true AND the environment
                       variable LEARNLOOP_ALLOW_NETWORK=1 is set. Not used in this stage
                       [APPROVED restriction: no real API calls until separately approved].

The API key is read from an environment variable at send time, never from code, Git or
the config file, and is never logged (Secret hides it in repr).
"""

from __future__ import annotations

import json
import re
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

from brain.providers.base import (ConfigError, HttpRequest, HttpResponse, MalformedOutput, NetworkDisabled,
                                  NetworkUnreachable, ProviderTimeout, Secret, ServerError)


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class BlockedTransport:
    name = "blocked"

    def send(self, request: HttpRequest) -> HttpResponse:
        raise NetworkDisabled("real provider calls are not approved in this stage")


class MockTransport:
    """`handler(request) -> HttpResponse` or raises. Records requests (for assertions)."""

    name = "mock"

    def __init__(self, handler: Callable[[HttpRequest], HttpResponse]):
        self.handler = handler
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.handler(request)


class NetworkTransport:
    """Real HTTPS. Constructed only through config.build_transport() (two locks)."""

    name = "network"

    def __init__(self, key_env: str, timeout_s: float = 60.0, _opener=urllib.request.urlopen,
                 tls_mode: str = "default"):
        value = os.environ.get(key_env, "")
        if not value:
            raise ConfigError(f"environment variable {key_env} is not set")
        if not value.isascii() or any(c.isspace() for c in value.strip()) or len(value.strip()) < 20:
            # e.g. the placeholder text from the instructions was pasted instead of the key
            raise ConfigError(f"{key_env} does not look like an API key (placeholder text? spaces?) – "
                              "paste the key itself from the provider console")
        value = value.strip()
        self._key = Secret(value)
        self.timeout_s, self._open = timeout_s, _opener
        self._ctx = tls_context(tls_mode)

    def send(self, request: HttpRequest) -> HttpResponse:
        if not request.url.startswith("https://"):
            raise ConfigError("only https endpoints are allowed")
        headers = dict(request.headers)
        if request.auth_header:
            headers[request.auth_header] = request.auth_prefix + self._key.reveal()
        data = json.dumps(request.body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(request.url, data=data, headers=headers, method=request.method)
        try:
            kwargs = {"context": self._ctx} if self._ctx is not None else {}
            with self._open(req, timeout=self.timeout_s, **kwargs) as r:
                return HttpResponse(r.status, _json(r.read()), dict(r.headers))
        except urllib.error.HTTPError as e:      # 4xx/5xx – the body is NOT kept (may echo input)
            body = {}
            if 400 <= e.code < 500 and e.code not in (401, 403):
                # Only the provider's short explanation survives, already cleaned (no Hebrew,
                # ≤200 chars) – it says WHY a request shape was rejected (e.g. a schema keyword).
                from brain.providers.adapters import provider_hint
                try:
                    raw = json.loads(e.read(20_000) or b"{}")
                except (ValueError, OSError):
                    raw = {}
                hint = provider_hint(raw)
                err = raw.get("error") if isinstance(raw, dict) else None
                code = err.get("code") if isinstance(err, dict) else None
                # the provider's machine-readable error CODE (e.g. "insufficient_quota") – an
                # identifier, never content; kept only if it looks like one
                code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,50}", code) else None
                if hint or code:
                    body = {"error": {k: v for k, v in (("message", hint), ("code", code)) if v}}
            return HttpResponse(e.code, body, dict(e.headers or {}))
        except (socket.timeout, TimeoutError):
            raise ProviderTimeout("request timed out") from None
        except urllib.error.URLError as e:
            # Only a CATEGORY is reported (no address, no content): enough to diagnose.
            problem = connection_problem(e.reason)
            if problem.startswith(("tls", "dns")):
                raise NetworkUnreachable("connection failed: " + problem) from None
            raise ServerError("connection failed: " + problem) from None


TLS_MODES = ("default", "windows_store", "windows_store_non_strict")


def tls_context(mode: str):
    """How HTTPS certificates are checked. The certificate is ALWAYS verified – no mode turns
    verification off.
      default                   Python's normal check (None = let urllib decide).
      windows_store             verify with the operating system's own verifier (package
                                'truststore'), exactly like the browser – needed when a filter or
                                antivirus installs its own root certificate in Windows.
      windows_store_non_strict  Python's check against the Windows certificate store, without the
                                extra 'strict' rules Python 3.13 added (some filter certificates
                                miss optional fields). Chain, expiry and host name are still checked.
    """
    import ssl
    if mode == "default":
        return None
    if mode == "windows_store":
        try:
            import truststore
        except ImportError:
            raise ConfigError("tls_mode windows_store needs: pip install truststore") from None
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if mode == "windows_store_non_strict":
        ctx = ssl.create_default_context()
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx
    raise ConfigError("unknown tls_mode")


def connection_problem(reason) -> str:
    import ssl
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "tls_certificate (a filter/antivirus/proxy may inspect HTTPS – its certificate is not trusted by Python)"
    if isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, socket.gaierror):
        return "dns (the address could not be resolved – no internet or the domain is blocked)"
    if isinstance(reason, ConnectionRefusedError):
        return "refused"
    if isinstance(reason, ConnectionResetError):
        return "reset (connection cut – often a filter or firewall)"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    return type(reason).__name__


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MalformedOutput("response body is not JSON") from None


def monotonic_ms() -> float:
    return time.monotonic() * 1000.0

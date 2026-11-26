"""Which HTTPS check works on THIS computer? Sends nothing but a connection handshake.

    python -m brain.providers.tls_check

Opens a TLS connection to the provider hosts (no request, no key, no data) once per mode and
prints which modes succeed. Certificate verification stays ON in every mode.
"""

from __future__ import annotations

import socket
import sys

from brain.providers.base import ConfigError
from brain.providers.transport import TLS_MODES, connection_problem, tls_context

HOSTS = {"gemini": "generativelanguage.googleapis.com", "openai": "api.openai.com",
         "anthropic": "api.anthropic.com"}


def check(host: str, mode: str, timeout: float = 10.0) -> str:
    import ssl
    try:
        ctx = tls_context(mode) or ssl.create_default_context()
    except ConfigError as e:
        return f"not available ({e})"
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host):
                return "OK"
    except Exception as e:                                # noqa: BLE001 – category only
        return "failed: " + connection_problem(e)


def main(argv: list[str]) -> int:
    providers = argv or ["gemini"]
    print("HTTPS check – only a handshake, nothing is sent. Certificate verification is always on.")
    for p in providers:
        host = HOSTS.get(p)
        if not host:
            print(f"unknown provider {p}")
            return 2
        for mode in TLS_MODES:
            print(f"  {p:10} {mode:26} {check(host, mode)}")
    print('If only a non-default mode says OK, put it in local_data\\providers.json, e.g. "tls_mode": "windows_store"')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

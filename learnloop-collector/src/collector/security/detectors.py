"""Find secrets and high-risk personal data in text.

Two levels of certainty (spec, APPROVED):
  DEFINITE  -> the value is removed and replaced with [REDACTED:<kind>]; the rest continues.
  UNCERTAIN -> the whole file is held for the user's review (fail closed).

Scope decided by the user (option B): secrets + Israeli ID, credit card, bank account / IBAN.
Emails and phone numbers are NOT redacted.

IMPORTANT: no detector catches 100% of secrets. This is one layer of defense,
together with the built-in exclusions (.env, keys...) in PathGuard.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

DEFINITE = "definite"
UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int
    certainty: str


# ---------- secrets with a known format (definite) ----------
_KNOWN_TOKENS = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)")),
    ("api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),                 # Anthropic
    ("api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),           # OpenAI
    ("api_key", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),                # GitHub
    ("api_key", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    ("api_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),                       # AWS
    ("api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),                     # Google
    ("api_key", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),              # Slack
    ("api_key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}")),               # Stripe
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),  # JWT
]

# password = value   /   סיסמה: value      (only the VALUE is redacted, group "v")
_PASSWORD = re.compile(
    r"(?i)(?:\b[\w.-]*(?:password|passwd|pwd)|\bpass\b|סיסמה|סיסמא)['\"]?\s*[:=]\s*(?P<q>['\"]?)(?P<v>[^\s'\",;)]+)")

# api_key = "value", secret: value, access_token = value ...
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b[\w.-]*(?:api[_-]?key|secret|access[_-]?token|auth[_-]?token|client[_-]?secret|private[_-]?key)[\w.-]*"
    r"['\"]?\s*[:=]\s*(?P<q>['\"]?)(?P<v>[^\s'\",;)]{8,})")

# scheme://user:PASSWORD@host
_CONNECTION_STRING = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:(?P<v>[^\s@/]+)@", re.IGNORECASE)

_PLACEHOLDERS = {"none", "null", "nil", "true", "false", "input()", "getpass()", "xxx", "xxxx",
                 "****", "******", "your_password", "password", "changeme", "example", "..."}


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return (not v or v in _PLACEHOLDERS or v.startswith(("<", "{", "$", "%", "os.", "env", "input("))
            or set(v) <= {"*", "x", "."})


# ---------- personal data (definite, verified by check digits) ----------
_ID_CANDIDATE = re.compile(r"(?<![\d.,])\d{9}(?![\d.,])")
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_IBAN_CANDIDATE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")
_BANK_ACCOUNT = re.compile(r"(?i)(?:חשבון|ח-ן|account)\D{0,15}(?P<v>\d[\d-]{4,14}\d)")


def israeli_id_valid(digits: str) -> bool:
    """Israeli ID check digit (Luhn-like algorithm used by the Population Registry)."""
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n - 9 if n > 9 else n
    return total % 10 == 0 and digits != "000000000"


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iban_valid(iban: str) -> bool:
    s = iban.replace(" ", "")
    rearranged = s[4:] + s[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return int(number) % 97 == 1


# ---------- random-looking strings without context (uncertain) ----------
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{32,200}(?![A-Za-z0-9+/=_-])")


def shannon_entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def _looks_random(token: str) -> bool:
    classes = sum([any(c.islower() for c in token), any(c.isupper() for c in token),
                   any(c.isdigit() for c in token)])
    return classes == 3 and shannon_entropy(token) >= 4.3


# ---------- main entry ----------
def find_sensitive(text: str) -> list[Finding]:
    findings: list[Finding] = []

    for kind, pattern in _KNOWN_TOKENS:
        for m in pattern.finditer(text):
            findings.append(Finding(kind, m.start(), m.end(), DEFINITE))

    for kind, pattern in (("password", _PASSWORD), ("secret", _SECRET_ASSIGNMENT)):
        for m in pattern.finditer(text):
            if not _is_placeholder(m.group("v")):
                findings.append(Finding(kind, m.start("v"), m.end("v"), DEFINITE))

    for m in _CONNECTION_STRING.finditer(text):
        if not _is_placeholder(m.group("v")):
            findings.append(Finding("password", m.start("v"), m.end("v"), DEFINITE))

    for m in _ID_CANDIDATE.finditer(text):
        if israeli_id_valid(m.group()):
            findings.append(Finding("israeli_id", m.start(), m.end(), DEFINITE))

    for m in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            findings.append(Finding("credit_card", m.start(), m.end(), DEFINITE))

    for m in _IBAN_CANDIDATE.finditer(text):
        if iban_valid(m.group()):
            findings.append(Finding("bank_account", m.start(), m.end(), DEFINITE))

    for m in _BANK_ACCOUNT.finditer(text):
        findings.append(Finding("bank_account", m.start("v"), m.end("v"), DEFINITE))

    definite_spans = [(f.start, f.end) for f in findings]
    for m in _LONG_TOKEN.finditer(text):
        if any(s <= m.start() < e or s < m.end() <= e for s, e in definite_spans):
            continue  # already handled as a definite secret
        if text[max(0, m.start() - 7):m.start()].lower().endswith("base64,"):
            continue  # embedded image data, not a secret
        if _looks_random(m.group()):
            findings.append(Finding("possible_secret", m.start(), m.end(), UNCERTAIN))

    return findings

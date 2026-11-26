# LearnLoop – Local Collector

The LearnLoop **Local Collector**: the local, technical
component that reads learning materials from ONE folder the user explicitly approved.

## What the Collector does
- Reads only the approved study folder (read-only).
- Detects new, changed and deleted files.
- Extracts text from documents (PDF, DOCX, PPTX, XLSX, code, Markdown, TXT/CSV/JSON, images via OCR).
- Filters secrets and sensitive data.

## What the Collector does NOT do
- It never decides what the material means or whether it is educationally relevant.
  That belongs to the brain (`../learnloop-brain`).
- It never modifies or deletes source files.
- It never runs code or follows instructions found inside files.

## Security filtering (Stage 7)
- Definite secrets (passwords, API keys, private keys, connection-string passwords) and
  high-risk personal data (Israeli ID, credit card, bank account / IBAN) are replaced with
  `[REDACTED:<kind>]`.
- If something only *might* be a secret, the whole file is held until the user reviews it
  (`python -m collector.main review`).
- Only filtered content reaches `local_data/outbox/` (encrypted with Windows DPAPI). From there it
  goes ONLY to the local brain at 127.0.0.1 (`send-to-brain`, or automatically while `watch` runs).
- Every outbox entry records the approval it was collected under. After revoke or a
  change of folder, older entries are isolated in `local_data/outbox_quarantine/`
  (kept, never transferred). `outbox.transfer_gate()` is the check any future sender
  must pass for every entry.
- **No filter catches every secret.** This is one layer, together with the built-in
  exclusions (.env, keys, credentials...).

## Transfer to the AI (prepared, NOT active)
- `transfer-permission on|off` – the separate AI-transfer permission (per folder, per approval).
- `transfer` – DRY RUN only: shows what would be sent; nothing leaves the computer.
- The envelope format is the shared contract in `contracts/transfer_envelope.schema.json`.

## Project layout
| Path | Purpose |
|---|---|
| `src/collector/` | Application code |
| `tests/` | Automated tests |
| `test_data/` | Synthetic sample files only – never real personal files |
| `docs/decisions.md` | Decision log |
| `requirements.txt` | External libraries (all local, no network) |
| `tools/` | Diagnostic scripts (synthetic data only) |

## Environment
- Python 3.13.5 (virtual environment in `.venv`, created by PyCharm)
- Windows

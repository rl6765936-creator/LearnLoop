# LearnLoop – Brain

The AI side of LearnLoop: understands study material, keeps the evidence-based knowledge
graph, asks the user when required, offers short activities and the next step.
Design: `claude/brain-design.md`. Decisions: `docs/decisions.md`. Settings and their status: `src/brain/settings.py`.

**Runs on the user's own computer** (`--live`): encrypted local SQLite, key protected by Windows
DPAPI plus a recovery code. The AI provider is OpenAI `gpt-6-luna`, used only after the synthetic
evaluation passed and the user approved (26.11.2026). Every send passes a monthly budget check
and two network locks. `--demo` runs the whole UI on synthetic data with a `FakeModel`.

## Layout
| Path | Purpose |
|---|---|
| `contracts/` | JSON Schemas: envelope (shared with the Collector), model analysis, activity, grading, approval decision |
| `src/brain/app.py` | `LearnLoopApp` – the whole brain as one application service |
| `src/brain/supervisor.py` | Supervisor: routes events, one thread per batch, approvals ↔ pause/resume, cycle summary |
| `src/brain/ingestion/` | Workflow steps; LangGraph graph; engines (LangGraph / plain) |
| `src/brain/ingestion_api.py` | Ingestion API: receives envelope bytes, validates, permission + duplicate checks |
| `src/brain/policy.py`, `settings.py` | When the user must approve; all tunable values with status labels |
| `src/brain/domain/` | Entities, append-only history, repository, understanding assessment (MasteryModel) |
| `src/brain/activities.py` | Quizzes, games, summaries, challenges, projects; grading, feedback, next step |
| `src/brain/approvals.py` | Approval Center |
| `src/brain/storage/` | SQLite / PostgreSQL schema + migrations, Store, AES-GCM encryption, encrypted backups |
| `src/brain/providers/` | Provider adapters (Anthropic, OpenAI, Gemini), gateway, config, capability map, offline simulator |
| `src/brain/evaluation/` | Synthetic evaluation, before/after thresholds, metadata-only telemetry, LangSmith adapter (off) |
| `src/brain/api/` | HTTP routes (framework-free) + local development server |
| `src/brain/demo.py` | Synthetic demo data |
| `ui/` | Web UI (plain JS modules: tokens, components, store, API client) |
| `tests/` | Synthetic data only |

## Principle: the model proposes, the code decides
Model output is validated against strict schemas without approval / mastery / permission
fields. Approvals, permissions, grading of closed questions and every write are ordinary code.

## Setup and tests (Windows, PowerShell)
```
cd LearnLoop\learnloop-brain
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"; python -m unittest discover -s tests
node --test ui/tests/format_store.test.mjs        # only if Node.js is installed
```

## Try it (demo, synthetic data)
```
$env:PYTHONPATH = "src"; python -m brain.api.server --demo
# open http://127.0.0.1:8765   (add --db local_data\demo.db to keep the state, encrypted with a DEVELOPMENT key)
```

## Evaluation
```
python -m brain.evaluation baseline
python -m brain.evaluation after --compare baseline
```

## Study classification (decision 2026-11-24)
Three separate dimensions: Study Status (completed / active / not_studied / unknown – set explicitly by
the user), Assessment Status (assessed / not assessed – from her own answers), Mastery Status (the four
approved states). `src/brain/domain/study.py`. Unclassified material creates ONE grouped question per
subject + folder (`/api/classifications`), never per file. Material marked finished gets the approved
§21 path (`/api/subjects/<id>/track`): broad quiz → project/activity → knowledge picture → weekly refresh.

## AI providers (local development only)
Switch provider by configuration – business code does not change.
```
copy providers.example.json local_data\providers.json     # edit "provider": fake | anthropic | openai | gemini
$env:PYTHONPATH = "src"; python -m brain.api.server --demo        # uses local_data\providers.json if present
```
`"transport"`: `blocked` (default – any call fails with network_disabled), `mock` (offline simulator in the
provider's own response format). The file never contains keys – only names of environment variables.

Provider comparison on synthetic Hebrew scenarios (offline):
```
python -m brain.evaluation.providers --mock          # report in local_data\eval\providers\
```

### Activating real-provider evaluation later (NOT approved yet – needs your explicit approval)
1. Approve: sending SYNTHETIC data to the chosen providers, the privacy requirements
   (paid tier only, no training, ZDR where offered, OpenAI store=false) and the providers to test.
2. Create the keys in the provider consoles yourself (paid / billing-enabled accounts; for Gemini never the free tier).
   Put them ONLY in environment variables of your shell, e.g. `$env:ANTHROPIC_API_KEY = "..."`. Never in files or Git.
3. In `local_data\providers.json`: set real model names in `"models"`, set `"allow_network": true`,
   set `"account_tier"` (`"free"` allows ONLY the synthetic evaluation; real study material needs `"paid"`), and check
   `anthropic_version`, `max_input_chars` and `output_tokens` against the provider documentation of that day.
4. In the same shell: `$env:LEARNLOOP_ALLOW_NETWORK = "1"`.
5. Optional prices: `local_data\prices.json` → `{"anthropic": {"input_per_mtok": 0, "output_per_mtok": 0}, ...}`.
6. Run `python -m brain.evaluation.providers --real first-run --providers anthropic,openai,gemini`.
   Only the scenarios in `src/brain/evaluation/providers/scenarios_he.py` are sent.
7. Rate Hebrew quality by hand in `review_first-run.md`; compare in `first-run.md`.
8. Afterwards remove the variables (`Remove-Item Env:LEARNLOOP_ALLOW_NETWORK`, and the keys) and set
   `"allow_network": false` again. Real study material needs a SEPARATE approval (transfer design, Q-D).

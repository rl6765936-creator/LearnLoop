# LearnLoop – all adjustable numbers (settings)

Decision 2026-11-24 (Q3, APPROVED): every number marked ASSUMPTION lives in ONE place – `src/brain/settings.py` – and can be changed there without changing code. The mastery formula itself is APPROVED and is not changed here.

| Setting | Value | Status | Why |
|---|---|---|---|
| `language` | `he` | RECOMMENDATION | language of all user-facing texts (he / en) |
| `policy.low_confidence` | `0.6` | ASSUMPTION | below this the user is asked (spec: low_confidence, no number) |
| `policy.major_new_topics` | `5` | ASSUMPTION | more new topics in one batch = major_graph |
| `policy.autonomous_new_topics` | `True` | ASSUMPTION – to confirm | new topic under an EXISTING subject treated as classify |
| `policy.analysis_window_chars` | `90000` | ASSUMPTION | spec §35: a very large file is analysed in parts of at most this many characters, each part told what earlier parts contained |
| `policy.autonomous_prerequisites` | `True` | ASSUMPTION – to confirm | prerequisite edges proposed from material added without asking |
| `mastery.model` | `bayesian` | APPROVED | approved Bayesian formula; 'provisional' kept only for comparison |
| `mastery.type_weights` | `{'quiz': 1.0, 'explain': 1.5, 'apply': 1.5, 'transfer': 2.0, 'retention': 1.5, 'debug': 1.5, 'analyze': 1.5, 'exposure': 0.0}` | APPROVED | weight of each evidence type (exposure = 0: never understanding) |
| `mastery.independence_factor` | `{'independent': 1.0, 'hint': 0.7, 'substantial_help': 0.4, 'saw_solution': 0.1, None: 1.0}` | APPROVED | help lowers the weight: hint 0.7, much help 0.4, solution seen 0.1 |
| `mastery.half_life_days` | `30.0` | APPROVED | a 30-day-old result counts half |
| `mastery.prior` | `1.0` | APPROVED | start from 50/50: one point for and one against |
| `mastery.understood_score` | `0.7` | APPROVED | score needed for 'understood' (plus the safety conditions) |
| `mastery.reinforce_score` | `0.45` | APPROVED | below this -> 'reinforce' |
| `mastery.keep_understood_score` | `0.55` | APPROVED | an understood topic stays understood until below this |
| `mastery.reinforce_min_weight` | `1.0` | ASSUMPTION | 'some evidence' before 'reinforce' can be decided |
| `mastery.time_alone_lowers_state` | `False` | APPROVED | time alone never lowers a state; only a failed retention check does |
| `mastery.show_confidence` | `True` | APPROVED | show how much evidence a score is based on |
| `mastery.retention_days` | `21` | ASSUMPTION | after this without evidence a retention check is SUGGESTED (no state change) |
| `activities.unfinished_quiz_days` | `2` | APPROVED | spec §6: unfinished quiz accessible for two days, then red indicator |
| `activities.max_items` | `5` | RECOMMENDATION | spec: activities short and focused |
| `activities.refresh_days` | `7` | APPROVED | spec §21: weekly refresher for subjects already studied |
| `activities.placement_max_topics` | `8` | ASSUMPTION | broad initial quiz: at most this many topics at once |
| `schedule.interval_hours` | `2` | APPROVED | spec §35: routine AI processing every two hours while the computer runs |
| `schedule.accumulation_files` | `20` | ASSUMPTION | spec §35 early trigger 'significant accumulation' – files waiting |
| `schedule.accumulation_chars` | `300000` | ASSUMPTION | … or characters waiting |
| `schedule.quiet_minutes` | `20` | ASSUMPTION | spec §35: while she studies (activity answered in the last N minutes) routine processing waits |
| `strengths.min_answers` | `3` | ASSUMPTION | spec §42: an ability counts as a strength after at least N of her answers |
| `strengths.min_average` | `0.8` | ASSUMPTION | … with at least this average result |
| `strengths.min_progress` | `0.25` | ASSUMPTION | progress = her results rose at least this much |

## AI provider and budget – `local_data/providers.json`

| Setting | Default | Status |
|---|---|---|
| `monthly_budget_usd` | 0.80 | APPROVED (Q4) |
| `models.openai` | gpt-6-luna | APPROVED (Q4) |
| `task_models` | {} (terra only where tests show need) | APPROVED rule (Q4) |
| `allowed_models.openai` | gpt-6-luna, gpt-6.1-terra (sol refused) | APPROVED (Q4) |
| `prices` | list prices, USD per 1M tokens | ASSUMPTION – check against the provider's price page |
| `tls_mode` | default (her computer: windows_store) | RECOMMENDATION |
| `min_interval_s`, `rate_limit_wait_s` | 5, 20 | ASSUMPTION |

## Collector – `local_data/config.json`

| Setting | Default | Status |
|---|---|---|
| `backfill_daily_chars` | 300,000 – a ceiling, not a target | APPROVED (Q7) |
| `brain_link` | null (port + path of the brain's ingest.token) | APPROVED design (Q8) |

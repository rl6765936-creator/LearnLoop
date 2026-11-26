# Privacy with an AI provider (decisions 2026-11-24, Q4, Q5, Q8 – APPROVED)

What is promised, and what is not.

- **Paid tier only for real study material.** In live mode a free-tier configuration is refused: the material is received, stored encrypted on her computer, and WAITS. Free Gemini is used only for the approved SYNTHETIC evaluation.
- **No training on the data.** Paid API tiers of OpenAI do not use API data for training by default.
- **`store=false`** on every OpenAI Responses API call (in code: `OpenAIAdapter`).
- **No files and no persistent stores at the provider.** LearnLoop never uploads files, vector stores or assistants; it sends one request at a time.
- **No zero-retention promise.** Until Zero Data Retention is approved for the account, assume OpenAI's abuse-monitoring logs may keep request content for **up to 30 days**.
- **Budget.** LearnLoop's own limit is $0.80 per month, checked BEFORE every send with a cost estimate; she also sets a $1 project limit in the OpenAI console as a second protection. When the budget is used up, AI sends stop until the next month; nothing queued is deleted.
- **The API key** is read only by the local server process, from an environment variable. It is never written to a file by LearnLoop, never sent to the browser, never logged.
- **Before every send** the Collector re-checks the transfer permission and the filter result (secrets removed; unclear files stopped), and the brain re-checks the budget.
- **Code signing:** none for now (Q17). Windows may show an "unknown publisher" warning when starting a packaged program.

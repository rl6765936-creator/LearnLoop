"""Capability map per provider – the three providers are NOT treated as identical.

Every entry has a status:
  "documented"   read in the provider's public documentation on 24.11.2026 (re-check at activation)
  "assumption"   our interpretation or a value that depends on the account / model chosen
  "to_measure"   can only be known by running the evaluation (needs approval for real calls)
  "contract"     depends on the account's agreement with the provider (e.g. ZDR)

Code uses this map: schema_tools.ALLOWED follows `structured_output.schema_keywords`, and the
adapters follow `statefulness` (OpenAI store=false) and `limitations`.
"""

from __future__ import annotations

CHECKED_ON = "2026-11-24"

CAPABILITIES: dict[str, dict] = {
    "anthropic": {
        "api": {"value": "Messages API – POST /v1/messages; headers x-api-key, anthropic-version",
                "status": "documented"},
        "structured_output": {
            "value": "Native JSON Schema output: output_config.format = {type: json_schema, schema}. "
                     "Answer arrives as a text block containing JSON.",
            "schema_keywords": "Unsupported: minimum/maximum, minLength/maxLength, maxItems, minItems>1, "
                               "oneOf, not, if/then, recursive schemas; additionalProperties must be false. "
                               "Unsupported keywords → HTTP 400 (so we strip them).",
            "status": "documented"},
        "hebrew": {"value": "Multilingual model; quality on OUR Hebrew material unknown.", "status": "to_measure"},
        "context_limits": {"value": "Depends on the chosen model; set max_input_chars / output_tokens at "
                                    "activation from the model page.", "status": "assumption"},
        "statefulness": {"value": "Stateless – each request carries everything; nothing kept between calls.",
                         "status": "documented"},
        "data_retention": {"value": "Limited retention under commercial terms; Zero Data Retention by agreement. "
                                    "Structured outputs are ZDR-eligible (schema cached up to 24h, not prompts "
                                    "or outputs). Some features are outside ZDR (e.g. Files API) – we do not use them.",
                           "status": "contract"},
        "training": {"value": "Commercial API data is not used for training by default.", "status": "documented"},
        "privacy_settings": {"value": "No per-request storage flag needed (stateless). Request ZDR for the org.",
                             "status": "contract"},
        "limitations": ["stop_reason 'refusal' → ContentRefused", "stop_reason 'max_tokens' → TruncatedOutput",
                        "HTTP 529 overloaded → retryable",
                        "anthropic-version value must be checked at activation (docs show newer values)"],
    },
    "openai": {
        "api": {"value": "Responses API – POST /v1/responses; Authorization: Bearer", "status": "documented"},
        "structured_output": {
            "value": "text.format = {type: json_schema, name, strict: true, schema}. Output in output[].content[] "
                     "items of type output_text; a refusal is a separate content item of type 'refusal'.",
            "schema_keywords": "Strict mode: every property must be in `required` and additionalProperties false; "
                               "optional fields become nullable (we remove the nulls afterwards). We send only "
                               "core keywords + min/max items + numeric range.",
            "status": "documented"},
        "hebrew": {"value": "Multilingual model; quality on OUR Hebrew material unknown.", "status": "to_measure"},
        "context_limits": {"value": "Depends on the chosen model; set at activation.", "status": "assumption"},
        "statefulness": {"value": "STATEFUL by default (responses kept as application state, 30 days). We send "
                                  "store=false and never use previous_response_id.", "status": "documented"},
        "data_retention": {"value": "Abuse-monitoring logs retained for a limited period; Zero Data Retention needs "
                                    "OpenAI approval.", "status": "contract"},
        "training": {"value": "API data is not used for training unless the organization opts in.",
                     "status": "documented"},
        "privacy_settings": {"value": "store=false on every request (enforced in the adapter); org data-sharing "
                                      "opt-in must stay OFF; request ZDR if available.", "status": "documented"},
        "limitations": ["status 'incomplete' + max_output_tokens → TruncatedOutput",
                        "incomplete + content_filter → ContentRefused", "refusal item → ContentRefused",
                        "some models reject `temperature` – default is to not send it",
                        "first request with a new schema is slower (schema processing)"],
    },
    "gemini": {
        "api": {"value": "POST v1beta/models/{model}:generateContent; header x-goog-api-key", "status": "documented"},
        "structured_output": {
            "value": "generationConfig.responseMimeType = application/json + responseJsonSchema (JSON Schema "
                     "subset). Text in candidates[0].content.parts[].text.",
            "schema_keywords": "Subset: type, properties, required, items, enum, anyOf, min/maxItems, "
                               "minimum/maximum, additionalProperties; no if/then, no string lengths.",
            "status": "documented"},
        "hebrew": {"value": "Multilingual model; quality on OUR Hebrew material unknown.", "status": "to_measure"},
        "context_limits": {"value": "Depends on the chosen model; set at activation.", "status": "assumption"},
        "statefulness": {"value": "generateContent is stateless.", "status": "documented"},
        "data_retention": {"value": "Paid tier: prompts/responses logged for a limited period for abuse "
                                    "monitoring.", "status": "documented"},
        "training": {"value": "PAID tier: not used to improve products. FREE tier / unpaid AI Studio: MAY be "
                              "used (and reviewed by humans) – forbidden for LearnLoop.", "status": "documented"},
        "privacy_settings": {"value": "Use a billing-enabled (paid) project only. Never the free tier.",
                             "status": "documented"},
        "limitations": ["finishReason SAFETY/RECITATION/BLOCKLIST/PROHIBITED_CONTENT/SPII → ContentRefused",
                        "promptFeedback.blockReason → ContentRefused", "MAX_TOKENS → TruncatedOutput",
                        "thought parts are ignored when reading the answer"],
    },
}

# Requirements LearnLoop places on ANY provider before real use [RECOMMENDATION – see
# ai-provider-comparison.md §4.1; not approved yet].
PRIVACY_REQUIREMENTS = [
    "paid tier only, never a free tier",
    "no training on our data (opt-outs confirmed in the account)",
    "Zero Data Retention requested where offered",
    "no provider-side state (OpenAI store=false)",
    "content leaves the computer only after local filtering AND transfer permission",
]

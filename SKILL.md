---
name: "search-governor"
description: "Provider-neutral aggregated search through one governed search entry"
---

# 聚合搜索 / Search Governor

Use Search Governor as the single search entry whenever manually registered providers are available.

## Workflow

1. Call the Agent integration's `search-governor` search provider for ordinary fast search, or run `sg search`.
2. Do not call internal provider adapters directly.
3. Treat returned summaries as the governed initial result set.
4. When a returned result has deferred body content, use the integration's status/read companion tools if available.
5. If `auth_required` is returned, ask the user to complete the relevant local authentication or verification.
6. Route full and deep Agent requests through the generated OpenClaw Skill; do not maintain a second copy of the Search Governor runtime inside the Agent workspace.

## Modes

- `fast`: governed summary search with optional deferred body fetch.
- `full`: synchronous body expansion and evidence evaluation.
- `deep`: full mode plus a brief-driven evidence article; requires a configured analysis backend unless explicit fallback is allowed.

Providers are registered manually through the subprocess adapter contract. Agent tools, APIs, scripts, browser flows, and crawlers all use the same request-JSON to Candidate-JSONL boundary.

Search mode owns the total provider budget; a provider preset owns only provider selection and allocation weights. Special platform providers must be explicitly requested and must not be inferred or added to ordinary presets.

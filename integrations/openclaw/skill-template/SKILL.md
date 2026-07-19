---
name: "openclaw-search-governor"
description: "Route OpenClaw search requests through Search Governor fast, full, or deep mode, including explicitly requested registered platform providers"
---

# 聚合搜索 / Search Governor

Use Search Governor as OpenClaw's single governed search entry. Never invoke an internal provider adapter directly.

## Route requests

1. For an ordinary quick search, call OpenClaw `web_search` with provider `search-governor`. The plugin uses fast mode and the operator-configured `speed` preset.
2. For a comprehensive, full-text, evidence, comparison, or multi-source request, run `{{SG_BIN}} search <query> --mode full --format json`.
3. For an explicit deep-research request, build the required brief and run `{{SG_BIN}} search <query> --mode deep` with `--point-question`, `--goal`, `--boundaries`, and `--output-use`. Do not add `--allow-analysis-fallback` unless the user explicitly permits degraded output.
4. Add `--providers <registered-id>` only when the user explicitly requests a special platform or provider. Do not infer special providers from the topic and do not add them to ordinary presets.
5. After plugin fast search, use `search_governor_status` and `search_governor_read` for Search Governor's cleaned body cache when needed.

## Preserve boundaries

- Treat status/read as post-search body helpers, not additional search entries.
- Do not register `search-governor` as an internal provider.
- Do not replace or disable OpenClaw's built-in `web_fetch`.
- Surface `auth_required`, verification, missing-model, and provider failures instead of silently changing modes or sources.

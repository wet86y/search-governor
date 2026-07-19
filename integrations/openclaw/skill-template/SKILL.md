---
name: "openclaw-search-governor"
description: "Use 聚合搜索/Search Governor for web search, multi-source retrieval, full-text evidence, deep research, local knowledge, or explicitly requested platform search{{LOCAL_DESCRIPTION_SUFFIX}}; routes OpenClaw through fast, full, and deep governed modes"
---

# 聚合搜索 / Search Governor

Search Governor is OpenClaw's single governed search entry. It keeps every
registered web search tool, API, Skill wrapper, knowledge base, browser flow,
or crawler behind one pipeline: collect, normalize, deduplicate, allocate the
mode budget, rerank, fetch or expand bodies, clean and deduplicate content,
evaluate evidence, and optionally produce a deep evidence article.

Never invoke an internal provider adapter directly. A provider is a source
inside Search Governor, not another Agent-facing search tool.

## Primary interfaces

- Ordinary search: OpenClaw `web_search` provider `search-governor`.
- Full/deep search: `{{SG_BIN}} search` through this Skill.
- Post-search body helpers: `search_governor_status` and
  `search_governor_read`.
- CLI body fallback when companion tools are unavailable:
  `{{SG_BIN}} read --cache-key <cacheKey> --format markdown` or
  `{{SG_BIN}} read --url <url> --format markdown`.

The status/read helpers do not create a second search entry. The integration
does not replace OpenClaw's built-in `web_fetch`.

## Route requests

Choose the mode from user intent, not from query complexity alone:

1. `fast`: ordinary web lookup, quick fact finding, or a request for a few
   links. Call OpenClaw `web_search` with provider `search-governor`. The plugin
   uses the `speed` preset; the fast mode policy owns total budget 15 and return
   count 5.
2. `full`: the user asks for comprehensive, multi-source, comparative,
   full-text, source-quality, or evidence-oriented results. Run:
   `{{SG_BIN}} search <query> --mode full --format json`.
3. `deep`: the user explicitly asks for deep research, a research report, or a
   cited evidence article. First derive a brief, then run:
   `{{SG_BIN}} search <query> --mode deep --point-question <question> --goal <goal> --boundaries <scope> --output-use <use> --format json`.
4. A named special source such as a knowledge base, academic source, or
   platform crawler is used only when the user explicitly requests that source.
   Add `--providers <registered-id>`; do not infer it from the topic or add it
   to an ordinary preset.

Mode determines total search budget, returned result count, body processing,
and analysis level. Preset determines only provider selection, weights, and
budget allocation. Do not add hidden budget overrides. The normal full/deep
default is the operator's `total` preset and total budget 40.

## Fast result workflow

Treat the initial `web_search` response as summary-first. Each result may have
`searchGovernor.runId`, `index`, `fetchStatus`, `cacheKey`, and helper tool
names.

1. If snippets answer the request, cite or summarize the governed results.
2. If a returned page body is needed, prefer `search_governor_read` with one of:
   - `run_id` plus `index` and, when useful, a small `wait_ms`;
   - `cache_key`;
   - `url`.
3. If `fetchStatus` is `queued`, call `search_governor_status` or read with a
   short `wait_ms` before choosing another fetch path.
4. If the companion tools are not directly exposed and tool discovery exists,
   discover `search_governor_read` and `search_governor_status` by name.
5. If those tools remain unavailable, use the CLI read fallback above.
6. Prefer Search Governor's read path for its own results because it preserves
   cleaned content and blocked/auth/fallback status.

## Full and deep workflow

- `full` synchronously expands or fetches top bodies, reranks with body
  evidence when configured, deduplicates cleaned content, and evaluates source
  quality. Use the returned structured evidence; do not rerun individual
  providers to fill perceived gaps.
- `deep` includes the full pipeline and requires the four brief fields. Make
  `point-question` the exact question the article must answer; use `goal` for
  the decision or task, `boundaries` for time/domain/exclusions, and
  `output-use` for the expected deliverable.
- A missing deep analysis capability is an explicit capability error. Do not
  add `--allow-analysis-fallback` unless the user knowingly permits a degraded,
  labeled evidence outline.
- If full mode reports that model analysis was skipped, present the collected
  evidence and the skip status honestly; do not claim LLM analysis ran.

## Fetch and content policy

Search Governor owns the body pipeline:

1. Prefer provider-declared inline content or native fetch.
2. Otherwise use direct HTTP fetch for externally fetchable URLs.
3. Use the configured OpenClaw browser fallback only for declared blocked,
   rate-limited, or empty-content failures.
4. Do not browser-fallback for DNS failure, refused/reset connections, timeout,
   or other network-unreachable failures.
5. Clean fetched text and remove duplicate URL/content fingerprints before
   evidence evaluation and output.
6. Surface CAPTCHA, anti-bot, or forced login as `auth_required`. Ask the user
   to complete authentication or verification in the configured browser profile
   before retrying.

## Preserve boundaries

- Treat status/read as post-search body helpers, not additional search entries.
- Do not register `search-governor` as an internal provider.
- Do not call files under `managed_sources/` directly from the Agent.
- Do not replace or disable OpenClaw's built-in `web_fetch`.
- Surface `auth_required`, verification, missing-model, timeout, and provider
  failures instead of silently changing modes, sources, or search tools.
- Never expose adapter diagnostics or local credentials as search content.

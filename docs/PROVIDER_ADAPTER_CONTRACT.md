# Provider Adapter Contract

## Registration

Providers are never auto-discovered. Production has one runtime namespace and one registry: a provider participates only when it appears in the operator-owned `managed_sources/sources.json`. The tracked `examples/managed_sources/` tree is contract documentation and test input, not a second runtime registry.

Each registry item contains:

```json
{"id": "example", "path": "example/source.json", "enabled": false}
```

The registry ID must equal `source.json.id`. Duplicate IDs across the public and local registries are fatal. Manifest paths must remain inside their registry root. `search-governor` is reserved and cannot be registered as an upstream provider.

## Process protocol

The core starts the manifest `entrypoint` with the provider directory as cwd.

- stdin: exactly one UTF-8 JSON request object.
- stdout: zero or more UTF-8 JSON objects, one Candidate per line.
- stderr: human diagnostics and optional `SG_REPORT_JSON=<object>` lines.
- exit `0`: process completed; empty output is still reported as no candidates.
- non-zero exit: provider failure.

The request can contain `query`, provider counts, date/freshness hints, locale, language, domain filters, timeout, and the provider manifest as `source_config`.

## Candidate contract

Required after normalization:

- `title`: non-empty string.
- `url`: non-empty URL or stable local document reference.

Recommended fields:

- `snippet`, `provider`, `rank`, `published_at`, `language`, `content_kind`, `raw_score`, `extra`.

The adapter must report only parameters it actually applied. A declared capability that was requested but not reported is surfaced as a contract diagnostic.

## Agent tools as providers

An Agent search tool is not a special core executor. Its integration must expose a stable command or API, and a local adapter maps Search Governor's request to that interface and maps results back to Candidate JSONL.

For OpenClaw web search providers, a local adapter may use:

```text
openclaw infer web search --json --provider <provider-id> --query <query> --limit <count>
```

Never set `<provider-id>` to `search-governor`, because that recursively calls the aggregator.

## Native body expansion

A manifest may declare `capabilities.native_fetch` with an adapter entrypoint. It receives a JSON object containing the Candidate, declared capabilities, and fetch budget, then returns one JSON object with `fetched_content`, optional `fetched_title`, and diagnostics.

## Validation

```bash
python3 scripts/validate_source.py examples/managed_sources/mock/source.json
SG_SOURCES_DIR=examples/managed_sources sg search "contract test" --providers mock --allow-disabled-sources \
  --allow-rule-fallback --no-fetch --format json
```

# Changelog

## Unreleased

## 0.1.3

- Add a one-command local release workflow that validates a clean committed `HEAD`, runs the full checks, generates the private-extended OpenClaw Skill, atomically switches `current`, restarts the Gateway, and verifies the stable CLI and plugin load.
- Use the system Python 3.12 runtime directly for dependency-free releases instead of duplicating a virtual environment inside every immutable release.
- Keep local release retention idempotent so redeploying the same `HEAD` preserves the existing rollback target.
- Keep local release retention independent from GitHub Release history; the local publisher performs no remote Git or GitHub operations.

## 0.1.2

- Block private, local, reserved, and otherwise unsafe body-fetch targets, including DNS results and HTTP redirect destinations, without browser fallback.
- Collect all selected Provider adapters concurrently while preserving report order, per-Provider timeouts, and failure isolation.
- Redesign the project homepage around multi-source governance, the three search modes, body processing, and Agent integration.
- Fix split-layout path resolution for fast deferred body fetching and the relative OpenClaw browser fallback script.
- Add regression coverage proving release-owned helper scripts remain separate from persistent runtime data.
- Keep only the current and immediately previous immutable local releases while retaining a stable `current` plugin entry.

## 0.1.1

- Restore the OpenClaw fast compatibility policy: fast budget 15 with the operator-defined `speed` preset.
- Add a generated thin OpenClaw Skill for full/deep routing and explicit local platform providers.
- Restore sensitive fetch concurrency 3 and document native, HTTP, browser-fallback, and crawler boundaries.
- Add strict preset validation and regression coverage for template allocation, Skill paths, and plugin arguments.

## 0.1.0

- Initial public release of the provider-neutral Search Governor core.
- Manual subprocess provider registration and Candidate JSONL contract.
- Rule-first fast mode with optional model reranking and analysis.
- OpenClaw web search, status, and body-read integration.

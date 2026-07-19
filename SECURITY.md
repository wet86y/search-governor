# Security Policy

Do not report credentials through public issues. Report suspected credential exposure privately to the repository owner through GitHub.

Provider credentials belong in `config/.env` or provider-specific local secret stores. Browser profiles, cookies, run data, local adapters, and model overrides must remain in ignored paths.

Before every source push or local release, run `scripts/check.sh`. The public-tree check included there verifies that tracked source does not contain private runtime data or credential material. GitHub is source-only and does not receive release archives or other uploaded assets.

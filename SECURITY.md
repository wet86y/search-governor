# Security Policy

Do not report credentials through public issues. Report suspected credential exposure privately to the repository owner through GitHub.

Provider credentials belong in `config/.env` or provider-specific local secret stores. Browser profiles, cookies, run data, local adapters, and model overrides must remain in ignored paths.

Before every release, run `scripts/check-public-tree.sh` and build the release archive only with `scripts/export_bundle.sh`.

# Contributing

Contributions to the provider-neutral core, adapter contract, tests, and Agent integrations are welcome.

Do not submit real provider credentials, private adapters, browser state, scraped data, or provider-specific weighting from a local deployment. New public examples must use mock endpoints and generic identifiers.

Run `scripts/check.sh` before opening a pull request.

## Publication boundary

Runtime releases are built locally from a committed `HEAD` with `scripts/publish_local_release.py`. GitHub is source-only: push commits to `main`, but do not create or push version tags, create GitHub Releases, or upload release assets. The GitHub Actions workflow validates source changes and is not a publishing pipeline.

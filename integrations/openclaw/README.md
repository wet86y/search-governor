# OpenClaw Integration

This is the only Agent integration implemented and verified in Search Governor v0.1.1. It registers:

- web search provider `search-governor`
- tool `search_governor_status`
- tool `search_governor_read`

The provider calls the single `sg search` entry and uses the operator's manually registered local providers. No upstream provider IDs, credentials, or weights are embedded in this plugin. It selects the operator-defined `speed` preset and relies on fast mode for the total budget; it does not use the debug budget override.

Status and read are post-search body helpers, not separate search entries. The plugin does not replace OpenClaw's built-in `web_fetch`.

Full and deep Agent routing is supplied by the generated thin Skill under `skill-template/`. Build it with `python3 scripts/build_openclaw_skill.py`; local platform-provider rules belong in the Git-ignored `integrations.local/openclaw-skill.local.md` extension. Install it with `openclaw skills install build.local/openclaw-search-governor --as openclaw-search-governor --force`. The atomic deploy helper remains available for pre-install archiving and rollback preparation.

```bash
openclaw plugins install --link \
  /home/lenovo/.local/share/search-governor/integrations/openclaw --force
openclaw plugins inspect openclaw-search-governor-websearch --runtime
openclaw infer web search --provider search-governor --query "Search Governor" --limit 2 --json
```

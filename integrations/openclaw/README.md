# OpenClaw Integration

This is the only Agent integration implemented and verified in Search Governor v0.1.3. It registers:

- web search provider `search-governor`
- tool `search_governor_status`
- tool `search_governor_read`

The provider calls the single `sg search` entry and uses the operator's manually registered local providers. No upstream provider IDs, credentials, or weights are embedded in this plugin. It selects the operator-defined `speed` preset and relies on fast mode for the total budget; it does not use the debug budget override.

Status and read are post-search body helpers, not separate search entries. The plugin does not replace OpenClaw's built-in `web_fetch`.

Full and deep Agent routing, fast-result body reading, fetch boundaries, and error/degradation behavior are supplied by the generated Agent contract Skill under `skill-template/`. Build it from the installed release; local platform-provider rules belong in `runtime/integrations/openclaw/local/skill-routes.md`. The generated Skill is written to `runtime/data/staging/`. The atomic deploy helper remains available for pre-install archiving and rollback preparation when the OpenClaw installer is unavailable.

```bash
openclaw plugins install --link \
  ~/.local/share/search-governor/current/integrations/openclaw --force
openclaw plugins inspect openclaw-search-governor-websearch --runtime
openclaw infer web search --provider search-governor --query "Search Governor" --limit 2 --json
```

Register the plugin through the stable `current` path once. After a local release switches `current`, restart the OpenClaw Gateway and inspect the loaded runtime; do not reinstall the plugin or copy release-owned plugin code into `runtime`.

For routine local publishing, use `scripts/publish_local_release.py` from a clean, committed checkout. It builds the immutable release from `HEAD`, regenerates and atomically deploys this Skill with the private local extension, switches `current`, restarts the Gateway, and verifies that this plugin loaded. It does not create tags, push commits, or call GitHub.

```bash
python3 ~/.local/share/search-governor/current/scripts/build_openclaw_skill.py \
  --root ~/.local/share/search-governor/current \
  --runtime-root ~/.local/share/search-governor/runtime \
  --sg-bin ~/.local/bin/sg
openclaw skills install ~/.local/share/search-governor/runtime/data/staging/openclaw-search-governor \
  --as openclaw-search-governor --force
```

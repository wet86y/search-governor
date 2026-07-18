# OpenClaw Integration

This is the only Agent integration implemented and verified in Search Governor v0.1.0. It registers:

- web search provider `search-governor`
- tool `search_governor_status`
- tool `search_governor_read`

The provider calls the single `sg search` entry and uses the operator's manually registered local providers. No upstream provider IDs, presets, credentials, or weights are embedded in this plugin.

Status and read are post-search body helpers, not separate search entries. The plugin does not replace OpenClaw's built-in `web_fetch`.

```bash
openclaw plugins install --link \
  /home/lenovo/.local/share/search-governor/integrations/openclaw --force
openclaw plugins inspect openclaw-search-governor-websearch --runtime
openclaw infer web search --provider search-governor --query "Search Governor" --limit 2 --json
```

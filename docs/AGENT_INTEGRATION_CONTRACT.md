# Agent Integration Contract

An Agent integration registers Search Governor outward as one search capability. It does not own the internal provider registry.

An integration must:

1. Invoke the repository's `bin/sg search` entry.
2. Pass query and requested result count without choosing private provider IDs in public code.
3. Preserve Search Governor run IDs and result indexes.
4. Expose optional body status/read helpers without presenting them as additional search entries.
5. Surface configuration, authentication, and no-provider errors without silently falling back to an ungoverned search path.
6. Never register Search Governor as one of its own upstream providers.

OpenClaw is the only integration implemented and verified in v0.1.1. Other Agent runtimes may implement the same contract later.

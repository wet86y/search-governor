"use strict";

const assert = require("node:assert");
const plugin = require("../integrations/openclaw/index.js");

const providers = [];
const tools = [];
plugin.register({
  registerWebSearchProvider(provider) {
    providers.push(provider);
  },
  registerTool(tool) {
    tools.push(tool);
  },
});

assert.deepStrictEqual(providers.map((item) => item.id), ["search-governor"]);
assert.deepStrictEqual(
  tools.map((item) => item.name).sort(),
  ["search_governor_read", "search_governor_status"],
);
assert.ok(!providers[0].createTool().description.includes("preset=speed"));
console.log("OpenClaw plugin contract ok");

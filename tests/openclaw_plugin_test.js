"use strict";

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const plugin = require("../integrations/openclaw/index.js");
const pluginSource = fs.readFileSync(path.join(__dirname, "..", "integrations", "openclaw", "index.js"), "utf8");

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
assert.match(pluginSource, /"--mode",\s*"fast",\s*"--preset",\s*"speed"/);
assert.ok(!pluginSource.includes("--provider-total-budget"));
assert.ok(providers[0].createTool().description.includes("speed provider mix"));
console.log("OpenClaw plugin contract ok");

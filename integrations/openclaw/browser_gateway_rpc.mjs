#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function resolveRuntime(openclawBin) {
  const resolved = fs.realpathSync(openclawBin);
  let current = path.dirname(resolved);
  while (true) {
    const runtime = path.join(current, "dist", "gateway-rpc.runtime.js");
    if (fs.existsSync(runtime)) return runtime;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new Error(`OpenClaw gateway RPC runtime not found beside ${resolved}`);
}

const openclawBin = process.argv[2];
if (!openclawBin) throw new Error("OpenClaw executable path is required");
const request = await readStdin();
const runtime = await import(pathToFileURL(resolveRuntime(openclawBin)).href);
const result = await runtime.callGatewayFromCliRuntime(
  "browser.request",
  { timeout: String(request.timeoutMs ?? 30000) },
  request,
  { scopes: ["operator.admin"] },
);
process.stdout.write(`${JSON.stringify(result)}\n`);

"use strict";

const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const PROVIDER_ID = "search-governor";
const PLUGIN_ID = "openclaw-search-governor-websearch";
const DEFAULT_COUNT = 5;
const MAX_COUNT = 10;
const MAX_WAIT_MS = 15000;
const SKILL_ROOT = path.resolve(__dirname, "..", "..");
const SG_BIN = path.join(SKILL_ROOT, "bin", "sg");
const RUNS_DIR = path.join(SKILL_ROOT, "data", "runs");

function readStringParam(args, key) {
  const value = args?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function readCountParam(args) {
  const raw = args?.count ?? args?.limit ?? DEFAULT_COUNT;
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return DEFAULT_COUNT;
  }
  return Math.max(1, Math.min(MAX_COUNT, Math.trunc(value)));
}

function readNonNegativeIntegerParam(args, key, fallback, max) {
  const raw = args?.[key] ?? fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    return fallback;
  }
  return Math.max(0, Math.min(max, Math.trunc(value)));
}

function jsonResult(value) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(value, null, 2),
      },
    ],
    details: value,
  };
}

function sanitizeExternalContentText(value) {
  return String(value ?? "")
    .replace(/\u0000/g, "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");
}

function wrapWebContent(content) {
  const markerId = crypto.randomUUID();
  const sanitized = sanitizeExternalContentText(content);
  return [
    `<openclaw_external_content id="${markerId}">`,
    "Source: Web search",
    "---",
    sanitized,
    `</openclaw_external_content id="${markerId}">`,
  ].join("\n");
}

function hostnameFromUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return undefined;
  }
}

function normalizeUrl(url) {
  try {
    const parsed = new URL(String(url || "").trim());
    parsed.protocol = parsed.protocol.toLowerCase();
    parsed.hostname = parsed.hostname.toLowerCase().replace(/^www\./, "");
    parsed.hash = "";
    const entries = [];
    for (const [key, value] of parsed.searchParams.entries()) {
      const lowerKey = key.toLowerCase();
      if (
        lowerKey === "fbclid" ||
        lowerKey === "gclid" ||
        lowerKey === "yclid" ||
        lowerKey === "mc_cid" ||
        lowerKey === "mc_eid" ||
        lowerKey === "igshid" ||
        lowerKey === "spm" ||
        lowerKey.startsWith("utm_")
      ) {
        continue;
      }
      entries.push([key, value]);
    }
    entries.sort(([left], [right]) => left.localeCompare(right));
    parsed.search = "";
    for (const [key, value] of entries) {
      parsed.searchParams.append(key, value);
    }
    if (parsed.pathname !== "/") {
      parsed.pathname = parsed.pathname.replace(/\/+$/, "");
    }
    return parsed.toString();
  } catch {
    return String(url || "");
  }
}

function cacheKeyForUrl(url) {
  return crypto.createHash("sha1").update(normalizeUrl(url), "utf8").digest("hex");
}

function safeRunDir(runId) {
  if (!/^\d{8}-\d{6}-[a-f0-9]{6}$/.test(String(runId || ""))) {
    return null;
  }
  const runDir = path.resolve(RUNS_DIR, runId);
  return runDir.startsWith(path.resolve(RUNS_DIR) + path.sep) ? runDir : null;
}

function readJsonFile(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function readJsonlFile(file) {
  try {
    return fs
      .readFileSync(file, "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

function compactPipelineStatus(pipeline) {
  if (!pipeline || typeof pipeline !== "object") {
    return undefined;
  }
  return {
    runId: pipeline.run_id,
    collected: pipeline.collected,
    afterDedupe: pipeline.after_dedupe,
    returned: pipeline.returned,
    fetchMode: pipeline.fetch_mode,
    fetchEnabled: pipeline.fetch_enabled,
    fetchedOk: pipeline.fetched_ok,
    fetchedFailed: pipeline.fetched_failed,
    fetchAuthRequired: pipeline.fetch_auth_required,
    deferredFetchStarted: Boolean(pipeline.deferred_fetch?.started),
    deferredFetchPid: pipeline.deferred_fetch?.pid,
    rerankerOk: pipeline.reranker_ok,
    contentCleanup: pipeline.content_cleanup
      ? {
          enabled: pipeline.content_cleanup.enabled,
          processed: pipeline.content_cleanup.processed,
          originalChars: pipeline.content_cleanup.original_chars,
          cleanedChars: pipeline.content_cleanup.cleaned_chars,
        }
      : undefined,
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runSearchGovernorCommand(args, signal) {
  return new Promise((resolve, reject) => {
    const child = spawn(SG_BIN, args, {
      cwd: SKILL_ROOT,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
      signal,
    });

    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code, exitSignal) => {
      if (code !== 0) {
        const message = stderr.trim() || `Search Governor exited with code ${code}`;
        reject(new Error(exitSignal ? `${message} (${exitSignal})` : message));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (err) {
        reject(new Error(`Search Governor returned invalid JSON: ${err.message}`));
      }
    });
  });
}

function runSearchGovernor(query, count, signal) {
  return runSearchGovernorCommand(
    [
      "search",
      query,
      "--mode",
      "fast",
      "--return-count",
      String(count),
      "--format",
      "json",
    ],
    signal,
  );
}

function readSearchGovernorContent(params, signal) {
  const args = ["read", "--format", "json"];
  if (params.cacheKey) {
    args.push("--cache-key", params.cacheKey);
  } else if (params.url) {
    args.push("--url", params.url);
  } else {
    throw new Error("cache_key or url is required");
  }
  return runSearchGovernorCommand(args, signal);
}

function buildRunStatus(runId) {
  const runDir = safeRunDir(runId);
  if (!runDir || !fs.existsSync(runDir)) {
    return {
      ok: false,
      runId,
      error: "run not found",
    };
  }

  const runJson = readJsonFile(path.join(runDir, "run.json")) || {};
  const initialRows = readJsonlFile(path.join(runDir, "fetch_status.jsonl"));
  const deferredRows = readJsonlFile(path.join(runDir, "deferred_fetch_status.jsonl"));
  const deferredLog = readJsonFile(path.join(runDir, "deferred_fetch.log"));
  const rerankedRows = readJsonlFile(path.join(runDir, "reranked.jsonl"));
  const returned = Number(runJson?.pipeline?.returned || initialRows.length || deferredRows.length || 0);
  const resultCount = Math.max(returned, initialRows.length, deferredRows.length);

  const results = [];
  for (let i = 0; i < resultCount; i += 1) {
    const initial = initialRows[i] || {};
    const deferred = deferredRows[i] || {};
    const candidate = rerankedRows[i] || {};
    const row = Object.keys(deferred).length ? deferred : initial;
    const url = row.url || initial.url || candidate.url;
    const cacheKey = row.cache_key || row.extra?.fetch_cache_key || candidate.extra?.fetch_cache_key || (url ? cacheKeyForUrl(url) : undefined);
    results.push({
      index: i,
      url,
      title: row.fetched_title || row.title || candidate.title,
      provider: row.provider || candidate.provider,
      initialStatus: initial.status || candidate.fetch_status,
      fetchStatus: row.fetch_status || initial.status || candidate.fetch_status,
      fetchError: row.fetch_error || initial.error || candidate.fetch_error,
      cacheKey,
      hasContent: Boolean(row.fetched_content),
      contentChars: typeof row.fetched_content === "string" ? row.fetched_content.length : 0,
    });
  }

  return {
    ok: true,
    runId,
    runDir,
    deferred: deferredLog || { ok: false, pending: !fs.existsSync(path.join(runDir, "deferred_fetch.log")) },
    pipeline: compactPipelineStatus(runJson.pipeline),
    results,
  };
}

async function waitForRunResult(runId, index, waitMs) {
  const deadline = Date.now() + waitMs;
  let status = buildRunStatus(runId);
  while (waitMs > 0 && status.ok) {
    const row = status.results[index];
    if (row && row.fetchStatus && row.fetchStatus !== "queued") {
      return { status, row };
    }
    if (Date.now() >= deadline) {
      break;
    }
    await sleep(Math.min(500, Math.max(0, deadline - Date.now())));
    status = buildRunStatus(runId);
  }
  return { status, row: status.ok ? status.results[index] : undefined };
}

function toWebSearchResult(item, index, runId) {
  const url = typeof item?.url === "string" ? item.url : "";
  const title = typeof item?.title === "string" ? item.title : url;
  const snippet = typeof item?.snippet === "string" ? item.snippet : "";
  const domain = typeof item?.domain === "string" ? item.domain : hostnameFromUrl(url);
  const fetchStatus = typeof item?.fetch_status === "string" ? item.fetch_status : undefined;
  const contentSource = item?.content_source && typeof item.content_source === "object" ? item.content_source : undefined;
  const cacheKey =
    typeof item?.extra?.fetch_cache_key === "string" ? item.extra.fetch_cache_key : url ? cacheKeyForUrl(url) : undefined;
  return {
    title: title ? wrapWebContent(title) : "",
    url,
    description: snippet ? wrapWebContent(snippet) : "",
    siteName: domain,
    provider: typeof item?.provider === "string" ? item.provider : undefined,
    searchGovernor: {
      runId,
      index,
      fetchStatus,
      fetchError: typeof item?.fetch_error === "string" ? item.fetch_error : undefined,
      cacheKey,
      contentSource,
    },
  };
}

const searchGovernorProvider = {
  id: PROVIDER_ID,
  label: "Search Governor",
  hint: "Governed aggregated search using manually registered local providers.",
  requiresCredential: false,
  envVars: [],
  placeholder: "",
  signupUrl: "",
  docsUrl: "https://github.com/wet86y/search-governor#readme",
  credentialPath: `plugins.entries.${PLUGIN_ID}.config.apiKey`,
  getCredentialValue: () => undefined,
  setCredentialValue: () => {},
  createTool: () => ({
    description:
      "Search through Search Governor's manually registered providers. Results are normalized, deduplicated, ranked, and optionally fetched through one governed search entry. Use search_governor_status and search_governor_read for body content after the search.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        query: {
          type: "string",
          description: "Search query string.",
        },
        count: {
          type: "integer",
          minimum: 1,
          maximum: MAX_COUNT,
          description: "Number of search results to return.",
        },
      },
      required: ["query"],
    },
    execute: async (args, context) => {
      const query = readStringParam(args, "query");
      if (!query) {
        return {
          error: "invalid_query",
          message: "query must be a non-empty string",
        };
      }

      const count = readCountParam(args);
      const startedAt = Date.now();
      const payload = await runSearchGovernor(query, count, context?.signal);
      const top = Array.isArray(payload?.top) ? payload.top : [];
      const results = top.slice(0, count).map((item, index) => toWebSearchResult(item, index, payload?.run_id));

      return {
        query,
        provider: PROVIDER_ID,
        count: results.length,
        tookMs: Date.now() - startedAt,
        results,
        searchGovernor: {
          runId: payload?.run_id,
          runDir: payload?.run_dir,
          mode: "fast",
          preset: payload?.pipeline?.provider_preset,
          pipeline: payload?.pipeline,
          instructions:
            "For full body: search_governor_read({ run_id: runId, index, wait_ms }) or search_governor_read({ cache_key }).",

          tools: {
            status: "search_governor_status",
            read: "search_governor_read",
          },
        },
      };
    },
  }),
};

function createSearchGovernorStatusTool() {
  return {
    name: "search_governor_status",
    label: "Search Governor Status",
    description:
      "Inspect async body-fetch status for a Search Governor web_search run. Use this after search-governor web_search when full page content is needed, when result.searchGovernor.fetchStatus is queued, or before deciding whether search_governor_read can return the cached cleaned body.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        run_id: {
          type: "string",
          description: "Search Governor run id returned by web_search.",
        },
      },
      required: ["run_id"],
    },
    execute: async (_toolCallId, rawParams) => {
      const runId = readStringParam(rawParams, "run_id");
      return jsonResult(buildRunStatus(runId));
    },
  };
}

function createSearchGovernorReadTool() {
  return {
    name: "search_governor_read",
    label: "Search Governor Read",
    description:
      "Read cleaned full page body content captured by Search Governor async fetch after a search-governor web_search result. Prefer this over generic web_fetch for Search Governor results. Accepts result.searchGovernor.cacheKey as cache_key, result URL, or result.searchGovernor.runId plus result.searchGovernor.index. If content is queued, set wait_ms to briefly wait for completion.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        cache_key: {
          type: "string",
          description: "Fetch cache key from a search-governor result/status entry.",
        },
        url: {
          type: "string",
          description: "Result URL to read from Search Governor fetch cache.",
        },
        run_id: {
          type: "string",
          description: "Search Governor run id returned by web_search.",
        },
        index: {
          type: "integer",
          minimum: 0,
          description: "Zero-based result index within the Search Governor run.",
        },
        wait_ms: {
          type: "integer",
          minimum: 0,
          maximum: MAX_WAIT_MS,
          description: "Optional time to wait for async fetch completion.",
        },
      },
    },
    execute: async (_toolCallId, rawParams) => {
      const waitMs = readNonNegativeIntegerParam(rawParams, "wait_ms", 0, MAX_WAIT_MS);
      let cacheKey = readStringParam(rawParams, "cache_key");
      let url = readStringParam(rawParams, "url");
      const runId = readStringParam(rawParams, "run_id");
      const hasIndex = rawParams && rawParams.index !== undefined;
      const index = hasIndex ? readNonNegativeIntegerParam(rawParams, "index", 0, 100) : undefined;
      let runStatus;
      let runRow;

      if (!cacheKey && !url && runId && index !== undefined) {
        const resolved = await waitForRunResult(runId, index, waitMs);
        runStatus = resolved.status;
        runRow = resolved.row;
        cacheKey = runRow?.cacheKey || "";
        url = runRow?.url || "";
      }

      if (!cacheKey && !url) {
        return jsonResult({
          ok: false,
          error: "cache_key, url, or run_id + index is required",
        });
      }

      try {
        const payload = await readSearchGovernorContent({ cacheKey, url }, undefined);
        const content = payload?.fetched_content || payload?.snippet || "";
        return jsonResult({
          ok: true,
          runId: runId || undefined,
          index,
          cacheKey: payload?.cache_key || cacheKey || undefined,
          url: payload?.url || url || undefined,
          title: payload?.fetched_title || payload?.title,
          provider: payload?.provider,
          fetchStatus: payload?.fetch_status,
          fetchError: payload?.fetch_error,
          content: content ? wrapWebContent(content) : "",
          contentChars: content.length,
          status: runStatus ? { deferred: runStatus.deferred, result: runRow } : undefined,
        });
      } catch (err) {
        return jsonResult({
          ok: false,
          runId: runId || undefined,
          index,
          cacheKey: cacheKey || undefined,
          url: url || undefined,
          status: runStatus ? { deferred: runStatus.deferred, result: runRow } : undefined,
          error: err.message,
        });
      }
    },
  };
}

const plugin = {
  id: PLUGIN_ID,
  register(api) {
    api.registerWebSearchProvider(searchGovernorProvider);
    api.registerTool(createSearchGovernorStatusTool(), { name: "search_governor_status" });
    api.registerTool(createSearchGovernorReadTool(), { name: "search_governor_read" });
  },
};

module.exports = plugin;
module.exports.default = plugin;

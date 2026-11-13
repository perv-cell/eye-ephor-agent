import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import path from "path";
import type { ChannelSummary, LogBatch, LogRecord } from "./types";

type Listener = (batch: LogBatch) => void;

type Persisted = {
  logs: LogRecord[];
  seenBatches: string[];
};

const MAX_LOGS = 5_000;
const DATA_DIR = path.join(process.cwd(), ".data");
const DATA_FILE = path.join(DATA_DIR, "logs.json");

type RuntimeState = {
  logs: LogRecord[];
  seenBatches: Set<string>;
  listeners: Set<Listener>;
  loaded: boolean;
};

const runtimeKey = "__eye_ephor_log_runtime__";

function runtime(): RuntimeState {
  const g = globalThis as typeof globalThis & { [runtimeKey]?: RuntimeState };
  if (!g[runtimeKey]) {
    g[runtimeKey] = {
      logs: [],
      seenBatches: new Set<string>(),
      listeners: new Set<Listener>(),
      loaded: false,
    };
  }
  return g[runtimeKey]!;
}

function ensureLoaded(): RuntimeState {
  const state = runtime();
  if (state.loaded) return state;
  try {
    if (existsSync(DATA_FILE)) {
      const raw = JSON.parse(readFileSync(DATA_FILE, "utf8")) as Persisted;
      state.logs = Array.isArray(raw.logs) ? raw.logs : [];
      state.seenBatches = new Set(Array.isArray(raw.seenBatches) ? raw.seenBatches : []);
    }
  } catch {
    state.logs = [];
    state.seenBatches = new Set();
  }
  state.loaded = true;
  return state;
}

function persist(state: RuntimeState): void {
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
  const payload: Persisted = {
    logs: state.logs,
    seenBatches: [...state.seenBatches].slice(-2000),
  };
  writeFileSync(DATA_FILE, JSON.stringify(payload));
}

function normalizeRecord(
  raw: LogRecord,
  meta: { batch_id: string; agent_id: string; user_id?: string | null; host: string; os: string },
): LogRecord {
  return {
    ...raw,
    id: raw.id || `${meta.batch_id}-${raw.ts}-${raw.message.slice(0, 24)}`,
    host: raw.host || meta.host,
    os: raw.os || meta.os,
    batch_id: meta.batch_id,
    agent_id: meta.agent_id,
    user_id: meta.user_id ?? null,
  };
}

export function ingestBatch(batch: LogBatch): { accepted: boolean; count: number } {
  const state = ensureLoaded();
  if (!batch?.batch_id || !Array.isArray(batch.groups)) {
    return { accepted: false, count: 0 };
  }
  if (state.seenBatches.has(batch.batch_id)) {
    return { accepted: true, count: 0 };
  }
  state.seenBatches.add(batch.batch_id);

  const flat =
    batch.records && batch.records.length > 0
      ? batch.records
      : batch.groups.flatMap((group) => group.logs);

  const meta = {
    batch_id: batch.batch_id,
    agent_id: batch.agent_id,
    user_id: batch.user_id,
    host: batch.host,
    os: batch.os,
  };
  const incoming = flat.map((item) => normalizeRecord(item, meta));
  state.logs.push(...incoming);
  if (state.logs.length > MAX_LOGS) {
    state.logs.splice(0, state.logs.length - MAX_LOGS);
  }
  persist(state);

  for (const listener of state.listeners) {
    try {
      listener(batch);
    } catch {
      // ignore broken subscribers
    }
  }
  return { accepted: true, count: incoming.length };
}

export function getLogs(): LogRecord[] {
  return ensureLoaded().logs.slice();
}

export function getChannelSummaries(): ChannelSummary[] {
  const map = new Map<string, ChannelSummary>();
  for (const log of ensureLoaded().logs) {
    const key = `${log.source}::${log.channel}`;
    const existing = map.get(key);
    if (!existing) {
      map.set(key, {
        key,
        source: log.source,
        channel: log.channel,
        count: 1,
        latestTs: log.ts,
        levels: { [log.level]: 1 },
      });
      continue;
    }
    existing.count += 1;
    existing.levels[log.level] = (existing.levels[log.level] || 0) + 1;
    if (log.ts > existing.latestTs) existing.latestTs = log.ts;
  }
  return [...map.values()].sort((a, b) => b.latestTs.localeCompare(a.latestTs));
}

export function subscribe(listener: Listener): () => void {
  const state = ensureLoaded();
  state.listeners.add(listener);
  return () => {
    state.listeners.delete(listener);
  };
}

export function parseSseBody(body: string): LogBatch | null {
  const dataLine = body
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .find((line) => line.startsWith("data:"));
  if (!dataLine) return null;
  const json = dataLine.replace(/^data:\s?/, "");
  try {
    return JSON.parse(json) as LogBatch;
  } catch {
    return null;
  }
}

import type { Freshness, LevelFilter, LogRecord } from "./types";

const SEVERITY: Record<string, number> = {
  emergency: 7,
  alert: 6,
  critical: 5,
  error: 4,
  warning: 3,
  notice: 2,
  info: 1,
  debug: 0,
};

export function freshnessMs(value: Freshness): number | null {
  switch (value) {
    case "1m":
      return 60_000;
    case "5m":
      return 5 * 60_000;
    case "15m":
      return 15 * 60_000;
    case "1h":
      return 60 * 60_000;
    case "6h":
      return 6 * 60 * 60_000;
    default:
      return null;
  }
}

export function levelRank(level: string): number {
  return SEVERITY[level.toLowerCase()] ?? 1;
}

export function matchesLevel(level: string, filter: LevelFilter): boolean {
  if (filter === "all") return true;
  if (filter === "error+") return levelRank(level) >= 4;
  if (filter === "warning+") return levelRank(level) >= 3;
  if (filter === "info+") return levelRank(level) >= 1;
  return level.toLowerCase() === filter.toLowerCase();
}

export function channelKey(source: string, channel: string): string {
  return `${source}::${channel}`;
}

export function filterLogs(
  logs: LogRecord[],
  opts: {
    channelKey: string | null;
    freshness: Freshness;
    level: LevelFilter;
    query: string;
    limit: number;
  },
): LogRecord[] {
  const windowMs = freshnessMs(opts.freshness);
  const now = Date.now();
  const q = opts.query.trim().toLowerCase();
  const filtered = logs.filter((log) => {
    if (opts.channelKey && channelKey(log.source, log.channel) !== opts.channelKey) {
      return false;
    }
    if (windowMs !== null) {
      const ts = Date.parse(log.ts);
      if (!Number.isNaN(ts) && now - ts > windowMs) return false;
    }
    if (!matchesLevel(log.level, opts.level)) return false;
    if (q) {
      const hay = `${log.message} ${log.process ?? ""} ${log.channel} ${log.source}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  return filtered.slice(-opts.limit).reverse();
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function shortChannel(channel: string): string {
  if (channel.length <= 42) return channel;
  const parts = channel.split(/[\\/]/);
  return parts[parts.length - 1] || channel.slice(-42);
}

export function sourceLabel(source: string): string {
  switch (source) {
    case "journal":
      return "Journald";
    case "file":
      return "Файлы";
    case "windows-event":
      return "Windows Event";
    case "unified":
      return "macOS Unified";
    default:
      return source;
  }
}

export function levelClass(level: string): string {
  const rank = levelRank(level);
  if (rank >= 5) return "lvl-critical";
  if (rank >= 4) return "lvl-error";
  if (rank >= 3) return "lvl-warning";
  if (rank >= 2) return "lvl-notice";
  if (rank >= 1) return "lvl-info";
  return "lvl-debug";
}

const FEATURE_LABELS: Record<string, string> = {
  logs_per_minute: "логов/мин",
  unique_ips_per_minute: "уник. IP/мин",
  error_rate_per_minute: "доля ошибок/мин",
  char_diversity: "уник. символов",
  has_sql_keywords: "SQL-признаки",
  has_xss_keywords: "XSS-признаки",
  has_shell_keywords: "shell-признаки",
  has_path_traversal: "path traversal",
  has_base64: "base64",
  has_url_encoding: "URL-encoding",
  has_auth_failure: "auth failure",
  has_priv_keywords: "priv-esc",
  entropy: "энтропия",
  level_severity: "severity",
};

const FEATURE_ORDER = [
  "logs_per_minute",
  "unique_ips_per_minute",
  "error_rate_per_minute",
  "char_diversity",
  "entropy",
  "level_severity",
  "has_sql_keywords",
  "has_xss_keywords",
  "has_shell_keywords",
  "has_path_traversal",
  "has_base64",
  "has_url_encoding",
  "has_auth_failure",
  "has_priv_keywords",
  "has_ipv4",
  "has_ipv6",
  "has_url",
  "has_email",
  "has_path",
  "has_hex",
];

export function featureLabel(key: string): string {
  return FEATURE_LABELS[key] || key;
}

export function orderedFeatureEntries(
  features: Record<string, string | number | boolean | null | undefined>,
): [string, string | number | boolean | null | undefined][] {
  const remaining = new Map(Object.entries(features));
  const ordered: [string, string | number | boolean | null | undefined][] = [];
  for (const key of FEATURE_ORDER) {
    if (!remaining.has(key)) continue;
    ordered.push([key, remaining.get(key)]);
    remaining.delete(key);
  }
  for (const entry of remaining) ordered.push(entry);
  return ordered;
}

export type LogFeatures = {
  message_len: number;
  message_bytes: number;
  line_count: number;
  token_count: number;
  avg_token_len: number;
  digit_ratio: number;
  alpha_ratio: number;
  space_ratio: number;
  special_ratio: number;
  uppercase_ratio: number;
  entropy: number;
  char_diversity: number;
  level_severity: number;
  hour_utc: number;
  minute_utc: number;
  second_utc: number;
  weekday: number;
  logs_per_minute: number;
  unique_ips_per_minute: number;
  error_rate_per_minute: number;
  has_ipv4: boolean;
  has_ipv6: boolean;
  has_url: boolean;
  has_email: boolean;
  has_path: boolean;
  has_hex: boolean;
  has_sql_keywords: boolean;
  has_xss_keywords: boolean;
  has_shell_keywords: boolean;
  has_path_traversal: boolean;
  has_base64: boolean;
  has_url_encoding: boolean;
  has_auth_failure: boolean;
  has_priv_keywords: boolean;
  pid_present: boolean;
  process_len: number;
};

export type LogRecord = {
  id: string;
  ts: string;
  collected_at?: string;
  host: string;
  os: string;
  source: string;
  channel: string;
  level: string;
  message: string;
  process?: string | null;
  pid?: number | null;
  uid?: number | null;
  gid?: number | null;
  executable?: string | null;
  cmdline?: string | null;
  facility?: string | null;
  unit?: string | null;
  container?: string | null;
  tags?: string[];
  fields?: Record<string, string | number | boolean | null>;
  features?: LogFeatures;
  batch_id?: string;
  agent_id?: string;
  user_id?: string | null;
};

export type LogGroup = {
  source: string;
  channel: string;
  count: number;
  logs: LogRecord[];
};

export type LogBatch = {
  batch_id: string;
  agent_id: string;
  user_id?: string | null;
  host: string;
  os: string;
  sent_at: string;
  count: number;
  schema_version?: number;
  purpose?: string[];
  groups: LogGroup[];
  records?: LogRecord[];
};

export type ChannelSummary = {
  key: string;
  source: string;
  channel: string;
  count: number;
  latestTs: string;
  levels: Record<string, number>;
};

export type Freshness = "1m" | "5m" | "15m" | "1h" | "6h" | "all";
export type LevelFilter = "all" | "error+" | "warning+" | "info+" | string;

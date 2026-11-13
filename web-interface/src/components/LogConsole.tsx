"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import {
  channelKey,
  featureLabel,
  filterLogs,
  formatTime,
  levelClass,
  orderedFeatureEntries,
  shortChannel,
  sourceLabel,
} from "@/lib/format";
import type {
  ChannelSummary,
  Freshness,
  LevelFilter,
  LogBatch,
  LogRecord,
} from "@/lib/types";
import { AuthPanel } from "@/components/AuthPanel";

type Snapshot = {
  count: number;
  channels: ChannelSummary[];
  logs: LogRecord[];
};

const FRESHNESS: { id: Freshness; label: string }[] = [
  { id: "1m", label: "1 мин" },
  { id: "5m", label: "5 мин" },
  { id: "15m", label: "15 мин" },
  { id: "1h", label: "1 ч" },
  { id: "6h", label: "6 ч" },
  { id: "all", label: "Все" },
];

const LEVELS: { id: LevelFilter; label: string }[] = [
  { id: "all", label: "Все уровни" },
  { id: "error+", label: "Error+" },
  { id: "warning+", label: "Warning+" },
  { id: "info+", label: "Info+" },
  { id: "debug", label: "Debug" },
];

export function LogConsole() {
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [channels, setChannels] = useState<ChannelSummary[]>([]);
  const [connected, setConnected] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [freshness, setFreshness] = useState<Freshness>("15m");
  const [level, setLevel] = useState<LevelFilter>("all");
  const [limit, setLimit] = useState(200);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [modalLog, setModalLog] = useState<LogRecord | null>(null);
  const [, startTransition] = useTransition();

  useEffect(() => {
    const source = new EventSource("/api/logs/stream");
    source.addEventListener("open", () => setConnected(true));
    source.addEventListener("error", () => setConnected(false));
    source.addEventListener("snapshot", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as Snapshot;
      startTransition(() => {
        setLogs(data.logs);
        setChannels(data.channels);
      });
    });
    source.addEventListener("log-batch", (event) => {
      const batch = JSON.parse((event as MessageEvent).data) as LogBatch;
      const incoming =
        batch.records && batch.records.length > 0
          ? batch.records
          : batch.groups.flatMap((group) => group.logs);
      startTransition(() => {
        setLogs((prev) => {
          const next = [
            ...prev,
            ...incoming.map((item) => ({
              ...item,
              host: item.host || batch.host,
              os: item.os || batch.os,
              batch_id: batch.batch_id,
              agent_id: batch.agent_id,
              user_id: batch.user_id,
            })),
          ];
          return next.length > 5000 ? next.slice(next.length - 5000) : next;
        });
        setChannels((prev) => mergeChannels(prev, incoming));
      });
    });
    return () => source.close();
  }, []);

  useEffect(() => {
    if (!modalLog) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setModalLog(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [modalLog]);

  const visible = useMemo(
    () =>
      filterLogs(logs, {
        channelKey: selectedKey,
        freshness,
        level,
        query,
        limit,
      }),
    [logs, selectedKey, freshness, level, query, limit],
  );

  const selected = visible.find((item) => item.id === selectedId) || visible[0] || null;
  const grouped = useMemo(() => groupSidebar(channels), [channels]);

  return (
    <div className="console">
      <header className="topbar">
        <div className="brand-block">
          <img
            className="brand-mark"
            src="/brand-mark.png"
            alt=""
            width={36}
            height={36}
            aria-hidden
          />
          <div>
            <p className="brand">Око Эфора</p>
            <p className="brand-sub">Живые логи системы</p>
          </div>
        </div>

        <div className="controls">
          <label className="control">
            <span>Свежесть</span>
            <div className="chip-row">
              {FRESHNESS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={freshness === item.id ? "chip active" : "chip"}
                  onClick={() => setFreshness(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </label>

          <label className="control">
            <span>Уровень</span>
            <select value={level} onChange={(event) => setLevel(event.target.value as LevelFilter)}>
              {LEVELS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <label className="control grow">
            <span>Поиск</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="сообщение, процесс, канал…"
            />
          </label>

          <label className="control">
            <span>Длина выдачи · {limit}</span>
            <input
              type="range"
              min={50}
              max={1000}
              step={50}
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
            />
          </label>
        </div>

        <div className="status">
          <AuthPanel />
          <span className={connected ? "dot on" : "dot"} />
          <span>{connected ? "поток открыт" : "нет связи"}</span>
          <span className="count">{logs.length} в буфере</span>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <button
            type="button"
            className={!selectedKey ? "side-item active" : "side-item"}
            onClick={() => setSelectedKey(null)}
          >
            <span>Все источники</span>
            <strong>{logs.length}</strong>
          </button>
          {grouped.map((group) => (
            <div key={group.source} className="side-group">
              <p className="side-title">{sourceLabel(group.source)}</p>
              {group.items.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={selectedKey === item.key ? "side-item active" : "side-item"}
                  onClick={() => setSelectedKey(item.key)}
                  title={item.channel}
                >
                  <span>{shortChannel(item.channel)}</span>
                  <strong>{item.count}</strong>
                </button>
              ))}
            </div>
          ))}
        </aside>

        <section className="main">
          <div className="list-head">
            <h1>
              {selectedKey
                ? shortChannel(selectedKey.split("::")[1] || selectedKey)
                : "Все логи"}
            </h1>
            <p>{visible.length} записей по фильтрам</p>
          </div>

          <div className="list">
            {visible.length === 0 ? (
              <div className="empty">
                Пока нет записей. Запустите агент:{" "}
                <code>python -m src.main</code> из каталога <code>agent</code>.
              </div>
            ) : (
              visible.map((log) => (
                <button
                  key={log.id}
                  type="button"
                  className={selected?.id === log.id ? "row active" : "row"}
                  onClick={() => {
                    setSelectedId(log.id);
                    setModalLog(log);
                  }}
                >
                  <time>{formatTime(log.ts)}</time>
                  <span className={`lvl ${levelClass(log.level)}`}>{log.level}</span>
                  <span className="proc">{log.process || "—"}</span>
                  <span className="msg">{log.message}</span>
                </button>
              ))
            )}
          </div>
        </section>

        <aside className="detail">
          {selected ? <LogDetail log={selected} /> : <div className="empty">Выберите запись</div>}
        </aside>
      </div>

      {modalLog ? (
        <div
          className="log-modal-backdrop"
          role="presentation"
          onClick={() => setModalLog(null)}
        >
          <div
            className="log-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Полная информация лога"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="log-modal-head">
              <p className="detail-kicker">Полная запись</p>
              <button
                type="button"
                className="log-modal-close"
                onClick={() => setModalLog(null)}
                aria-label="Закрыть"
              >
                ×
              </button>
            </div>
            <LogDetail log={modalLog} />
            <pre className="fields log-modal-raw">
              {JSON.stringify(modalLog, null, 2)}
            </pre>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function LogDetail({ log }: { log: LogRecord }) {
  return (
    <div className="detail-card">
      <p className="detail-kicker">Подробности</p>
      <h2>{shortChannel(log.channel)}</h2>
      <p className={`lvl ${levelClass(log.level)}`}>{log.level}</p>
      <pre className="message">{log.message}</pre>
      <dl>
        <div>
          <dt>id</dt>
          <dd>{log.id}</dd>
        </div>
        <div>
          <dt>время</dt>
          <dd>{log.ts}</dd>
        </div>
        <div>
          <dt>собрано</dt>
          <dd>{log.collected_at || "—"}</dd>
        </div>
        <div>
          <dt>host / os</dt>
          <dd>
            {log.host} · {log.os}
          </dd>
        </div>
        <div>
          <dt>source</dt>
          <dd>{log.source}</dd>
        </div>
        <div>
          <dt>process / pid</dt>
          <dd>
            {log.process || "—"} / {log.pid ?? "—"}
          </dd>
        </div>
        <div>
          <dt>executable</dt>
          <dd>{log.executable || "—"}</dd>
        </div>
        <div>
          <dt>unit / facility</dt>
          <dd>
            {log.unit || "—"} / {log.facility || "—"}
          </dd>
        </div>
        <div>
          <dt>tags</dt>
          <dd>{(log.tags || []).join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>user / agent</dt>
          <dd>
            {log.user_id || "—"} / {log.agent_id || "—"}
          </dd>
        </div>
      </dl>
      {log.features ? (
        <>
          <p className="detail-kicker">Признаки Isolation Forest</p>
          <div className="features">
            {orderedFeatureEntries(log.features).map(([key, value]) => (
              <div
                key={key}
                className={value === true ? "feature feature-flag" : "feature"}
                title={key}
              >
                <span>{featureLabel(key)}</span>
                <strong>{String(value)}</strong>
              </div>
            ))}
          </div>
        </>
      ) : null}
      {log.fields && Object.keys(log.fields).length > 0 ? (
        <>
          <p className="detail-kicker">Поля источника</p>
          <pre className="fields">{JSON.stringify(log.fields, null, 2)}</pre>
        </>
      ) : null}
    </div>
  );
}

function mergeChannels(prev: ChannelSummary[], incoming: LogRecord[]): ChannelSummary[] {
  const map = new Map(prev.map((item) => [item.key, { ...item, levels: { ...item.levels } }]));
  for (const log of incoming) {
    const key = channelKey(log.source, log.channel);
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

function groupSidebar(channels: ChannelSummary[]) {
  const order: string[] = [];
  const buckets = new Map<string, ChannelSummary[]>();
  for (const item of channels) {
    if (!buckets.has(item.source)) {
      order.push(item.source);
      buckets.set(item.source, []);
    }
    buckets.get(item.source)!.push(item);
  }
  return order.map((source) => ({ source, items: buckets.get(source)! }));
}

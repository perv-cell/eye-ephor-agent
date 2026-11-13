"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import type { AuthSessionResponse } from "@/lib/auth/types";

export function AuthPanel() {
  const [session, setSession] = useState<AuthSessionResponse | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    const response = await fetch("/api/auth/session", { cache: "no-store" });
    const data = (await response.json()) as AuthSessionResponse;
    setSession(data);
    if (data.need_reauth) setOpen(true);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function onLogin(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey }),
      });
      const data = (await response.json()) as AuthSessionResponse & { code?: string };
      if (!response.ok || !data.ok) {
        setError(data.auth_message || "Не удалось войти");
        setSession(data);
        return;
      }
      setSession(data);
      setApiKey("");
      setOpen(false);
    } catch {
      setError("Нет связи с сервисом авторизации");
    } finally {
      setBusy(false);
    }
  }

  async function onLogout() {
    setBusy(true);
    try {
      const response = await fetch("/api/auth/logout", { method: "POST" });
      const data = (await response.json()) as AuthSessionResponse;
      setSession(data);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  if (!session) {
    return <div className="auth-chip muted">загрузка сессии…</div>;
  }

  const isUser = session.mode === "user" && !session.need_reauth;

  return (
    <div className="auth-panel">
      <button
        type="button"
        className={isUser ? "auth-chip user" : "auth-chip guest"}
        onClick={() => setOpen((value) => !value)}
      >
        {isUser
          ? `Аккаунт · ${session.display_name || session.user_id}`
          : session.need_reauth
            ? "Нужен повторный вход"
            : `Гость · ${session.quota?.remaining ?? "—"}/${session.quota?.limit ?? "—"}`}
      </button>

      {open ? (
        <div className="auth-popover">
          <p className="auth-message">{session.auth_message}</p>
          <p className="auth-meta">
            модель: <code>{session.model}</code>
            {isUser ? " · обучение включено" : " · общая модель"}
          </p>
          {session.devices && session.devices.length > 0 ? (
            <ul className="auth-devices">
              {session.devices.map((device) => (
                <li key={device.device_id}>
                  {device.name}
                  {device.device_id === session.device_id ? " (это устройство)" : ""}
                </li>
              ))}
            </ul>
          ) : null}

          {isUser ? (
            <button type="button" className="auth-btn ghost" onClick={() => void onLogout()} disabled={busy}>
              Выйти
            </button>
          ) : (
            <form className="auth-form" onSubmit={(event) => void onLogin(event)}>
              <label>
                <span>API-ключ</span>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="вставьте секретный ключ"
                  autoComplete="off"
                  required
                  minLength={8}
                />
              </label>
              <p className="auth-meta">
                Ключ не сохраняется на диск: после входа остаётся только сессия устройства.
              </p>
              {error ? <p className="auth-error">{error}</p> : null}
              <button type="submit" className="auth-btn" disabled={busy || apiKey.trim().length < 8}>
                {busy ? "Проверка…" : "Войти и включить обучение"}
              </button>
            </form>
          )}
        </div>
      ) : null}
    </div>
  );
}

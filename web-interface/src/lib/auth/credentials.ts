import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { AUTH_DIR, CREDENTIALS_FILE } from "./paths";
import type { CredentialsFile } from "./types";

function ensureDir() {
  if (!existsSync(AUTH_DIR)) mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 });
}

export function readCredentials(): CredentialsFile | null {
  if (!existsSync(CREDENTIALS_FILE)) return null;
  try {
    const raw = JSON.parse(readFileSync(CREDENTIALS_FILE, "utf8")) as CredentialsFile & {
      api_key?: string;
    };
    // Миграция: выкидываем сырой ключ из старых файлов при чтении в память
    if ("api_key" in raw) delete raw.api_key;
    return raw;
  } catch {
    return null;
  }
}

export function writeCredentials(data: CredentialsFile): void {
  ensureDir();
  const safe: CredentialsFile = {
    version: 2,
    device_id: data.device_id,
    device_name: data.device_name,
    mode: data.mode,
    user_id: data.user_id,
    display_name: data.display_name,
    training_allowed: data.training_allowed,
    model: data.model,
    quota: data.quota,
    session_token: data.session_token,
    session_expires_at: data.session_expires_at,
    auth_message: data.auth_message,
    saved_at: data.saved_at,
    need_reauth: data.need_reauth,
  };
  writeFileSync(CREDENTIALS_FILE, JSON.stringify(safe, null, 2), {
    encoding: "utf8",
    mode: 0o600,
  });
}

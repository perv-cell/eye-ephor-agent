import { createHash, randomBytes } from "crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import {
  AUTH_DIR,
  GUEST_DAILY_LIMIT,
  MAX_DEVICES_PER_KEY,
  REGISTRY_FILE,
  SESSION_TTL_MS,
} from "./paths";
import type { DeviceInfo, QuotaInfo } from "./types";

type RegistryDevice = DeviceInfo & {
  session_token_hash: string;
  expires_at: string;
};

type KeyRecord = {
  user_id: string;
  display_name: string;
  devices: RegistryDevice[];
};

type GuestRecord = {
  day: string;
  used: number;
  limit: number;
};

type SessionIndex = {
  key_hash: string;
  device_id: string;
  user_id: string;
  expires_at: string;
};

type Registry = {
  version: 2;
  keys: Record<string, KeyRecord>;
  guests: Record<string, GuestRecord>;
  /** sha256(session_token) → метаданные; сам token в реестре не храним */
  sessions: Record<string, SessionIndex>;
};

function ensureDir() {
  if (!existsSync(AUTH_DIR)) mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 });
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function emptyRegistry(): Registry {
  return { version: 2, keys: {}, guests: {}, sessions: {} };
}

function load(): Registry {
  ensureDir();
  if (!existsSync(REGISTRY_FILE)) return emptyRegistry();
  try {
    const raw = JSON.parse(readFileSync(REGISTRY_FILE, "utf8")) as Partial<Registry>;
    return {
      version: 2,
      keys: raw.keys || {},
      guests: raw.guests || {},
      sessions: raw.sessions || {},
    };
  } catch {
    return emptyRegistry();
  }
}

function save(registry: Registry) {
  ensureDir();
  writeFileSync(REGISTRY_FILE, JSON.stringify(registry, null, 2), {
    encoding: "utf8",
    mode: 0o600,
  });
}

export function hashSecret(value: string): string {
  return createHash("sha256").update(value.trim()).digest("hex");
}

export function hashApiKey(apiKey: string): string {
  return hashSecret(apiKey);
}

function userIdFromKey(apiKey: string): string {
  return `usr_${hashApiKey(apiKey).slice(0, 16)}`;
}

function newSessionToken(): string {
  return `ses_${randomBytes(32).toString("base64url")}`;
}

function expiresAt(from = Date.now()): string {
  return new Date(from + SESSION_TTL_MS).toISOString();
}

function purgeSession(registry: Registry, tokenHash: string | undefined) {
  if (!tokenHash) return;
  const meta = registry.sessions[tokenHash];
  delete registry.sessions[tokenHash];
  if (!meta) return;
  const record = registry.keys[meta.key_hash];
  if (!record) return;
  const device = record.devices.find((item) => item.device_id === meta.device_id);
  if (device && device.session_token_hash === tokenHash) {
    device.session_token_hash = "";
    device.expires_at = new Date(0).toISOString();
  }
}

function publicDevices(record: KeyRecord): DeviceInfo[] {
  return record.devices.map(({ device_id, name, last_seen }) => ({
    device_id,
    name,
    last_seen,
  }));
}

export function ensureGuest(deviceId: string): {
  quota: QuotaInfo;
  model: string;
  message: string;
} {
  const registry = load();
  const day = today();
  let guest = registry.guests[deviceId];
  if (!guest || guest.day !== day) {
    guest = { day, used: 0, limit: GUEST_DAILY_LIMIT };
    registry.guests[deviceId] = guest;
    save(registry);
  }
  return {
    quota: {
      remaining: Math.max(0, guest.limit - guest.used),
      limit: guest.limit,
      period: "day",
    },
    model: "base",
    message:
      "Гостевой режим: общая модель, без персонального обучения. Введите API-ключ для своей модели.",
  };
}

export function consumeGuestRequest(deviceId: string): {
  ok: boolean;
  quota: QuotaInfo;
  message: string;
} {
  const registry = load();
  const day = today();
  let guest = registry.guests[deviceId];
  if (!guest || guest.day !== day) {
    guest = { day, used: 0, limit: GUEST_DAILY_LIMIT };
  }
  if (guest.used >= guest.limit) {
    registry.guests[deviceId] = guest;
    save(registry);
    return {
      ok: false,
      quota: { remaining: 0, limit: guest.limit, period: "day" },
      message:
        "Лимит гостевых запросов исчерпан. Введите API-ключ для продолжения и персональной модели.",
    };
  }
  guest.used += 1;
  registry.guests[deviceId] = guest;
  save(registry);
  return {
    ok: true,
    quota: {
      remaining: Math.max(0, guest.limit - guest.used),
      limit: guest.limit,
      period: "day",
    },
    message: `Запрос принят общей моделью. Осталось ${guest.limit - guest.used} сегодня.`,
  };
}

export function loginWithApiKey(input: {
  apiKey: string;
  deviceId: string;
  deviceName: string;
}): {
  ok: boolean;
  user_id: string;
  display_name: string;
  training_allowed: boolean;
  model: string;
  session_token: string;
  session_expires_at: string;
  devices: DeviceInfo[];
  evicted_device_id: string | null;
  message: string;
  code?: string;
} {
  const apiKey = input.apiKey.trim();
  if (!apiKey || apiKey.length < 8) {
    return {
      ok: false,
      user_id: "",
      display_name: "",
      training_allowed: false,
      model: "base",
      session_token: "",
      session_expires_at: "",
      devices: [],
      evicted_device_id: null,
      message: "API-ключ слишком короткий или пустой.",
      code: "invalid_api_key",
    };
  }

  const registry = load();
  const keyHash = hashApiKey(apiKey);
  let record = registry.keys[keyHash];
  if (!record) {
    record = {
      user_id: userIdFromKey(apiKey),
      display_name: `user-${userIdFromKey(apiKey).slice(-6)}`,
      devices: [],
    };
    registry.keys[keyHash] = record;
  }

  const now = new Date().toISOString();
  const token = newSessionToken();
  const tokenHash = hashSecret(token);
  const exp = expiresAt();
  let evicted: string | null = null;
  const existing = record.devices.find((item) => item.device_id === input.deviceId);

  if (existing) {
    purgeSession(registry, existing.session_token_hash);
    existing.last_seen = now;
    existing.name = input.deviceName || existing.name;
    existing.session_token_hash = tokenHash;
    existing.expires_at = exp;
  } else {
    if (record.devices.length >= MAX_DEVICES_PER_KEY) {
      record.devices.sort((a, b) => a.last_seen.localeCompare(b.last_seen));
      const removed = record.devices.shift();
      if (removed) {
        evicted = removed.device_id;
        purgeSession(registry, removed.session_token_hash);
      }
    }
    record.devices.push({
      device_id: input.deviceId,
      name: input.deviceName || "device",
      last_seen: now,
      session_token_hash: tokenHash,
      expires_at: exp,
    });
  }

  registry.sessions[tokenHash] = {
    key_hash: keyHash,
    device_id: input.deviceId,
    user_id: record.user_id,
    expires_at: exp,
  };
  save(registry);

  const message = evicted
    ? `Ключ принят. Устройство заняло слот (${record.devices.length}/${MAX_DEVICES_PER_KEY}); предыдущее отключено и должно войти заново.`
    : `Ключ принят. Выдана device-сессия (модель user:${record.user_id}). Устройств: ${record.devices.length}/${MAX_DEVICES_PER_KEY}.`;

  return {
    ok: true,
    user_id: record.user_id,
    display_name: record.display_name,
    training_allowed: true,
    model: `user:${record.user_id}`,
    session_token: token,
    session_expires_at: exp,
    devices: publicDevices(record),
    evicted_device_id: evicted,
    message,
  };
}

export function validateSession(input: {
  deviceId: string;
  sessionToken?: string;
}): {
  ok: boolean;
  need_reauth: boolean;
  user_id?: string;
  display_name?: string;
  training_allowed: boolean;
  model: string;
  session_expires_at?: string;
  devices: DeviceInfo[];
  message: string;
  code?: string;
} {
  const token = input.sessionToken?.trim() || "";
  if (!token) {
    return {
      ok: false,
      need_reauth: true,
      training_allowed: false,
      model: "base",
      devices: [],
      message: "Нет активной сессии. Введите API-ключ.",
      code: "missing_session",
    };
  }

  const registry = load();
  const tokenHash = hashSecret(token);
  const meta = registry.sessions[tokenHash];
  if (!meta) {
    return {
      ok: false,
      need_reauth: true,
      training_allowed: false,
      model: "base",
      devices: [],
      message: "Сессия недействительна или отозвана. Введите API-ключ заново.",
      code: "session_revoked",
    };
  }

  if (meta.device_id !== input.deviceId) {
    return {
      ok: false,
      need_reauth: true,
      training_allowed: false,
      model: "base",
      devices: [],
      message: "Сессия привязана к другому устройству.",
      code: "device_mismatch",
    };
  }

  if (Date.parse(meta.expires_at) <= Date.now()) {
    purgeSession(registry, tokenHash);
    save(registry);
    return {
      ok: false,
      need_reauth: true,
      training_allowed: false,
      model: "base",
      devices: [],
      message: "Сессия истекла. Введите API-ключ заново.",
      code: "session_expired",
    };
  }

  const record = registry.keys[meta.key_hash];
  if (!record) {
    purgeSession(registry, tokenHash);
    save(registry);
    return {
      ok: false,
      need_reauth: true,
      training_allowed: false,
      model: "base",
      devices: [],
      message: "Аккаунт не найден. Введите API-ключ заново.",
      code: "invalid_api_key",
    };
  }

  const device = record.devices.find((item) => item.device_id === input.deviceId);
  if (!device || device.session_token_hash !== tokenHash) {
    purgeSession(registry, tokenHash);
    save(registry);
    return {
      ok: false,
      need_reauth: true,
      user_id: record.user_id,
      display_name: record.display_name,
      training_allowed: false,
      model: "base",
      devices: publicDevices(record),
      message:
        "Это устройство отключено (лимит 2 слота) или сессия сброшена. Введите API-ключ снова.",
      code: "device_evicted",
    };
  }

  device.last_seen = new Date().toISOString();
  save(registry);

  return {
    ok: true,
    need_reauth: false,
    user_id: record.user_id,
    display_name: record.display_name,
    training_allowed: true,
    model: `user:${record.user_id}`,
    session_expires_at: meta.expires_at,
    devices: publicDevices(record),
    message: `Аккаунт активен. Персональная модель user:${record.user_id}. Устройств: ${record.devices.length}/${MAX_DEVICES_PER_KEY}.`,
  };
}

/** Ротация: старый token отзывается, выдаётся новый (anti copy-reuse). */
export function rotateSession(input: {
  deviceId: string;
  sessionToken: string;
}): {
  ok: boolean;
  session_token?: string;
  session_expires_at?: string;
  user_id?: string;
  display_name?: string;
  model?: string;
  devices?: DeviceInfo[];
  message: string;
  code?: string;
} {
  const check = validateSession(input);
  if (!check.ok || !check.user_id) {
    return {
      ok: false,
      message: check.message,
      code: check.code,
    };
  }

  const registry = load();
  const oldHash = hashSecret(input.sessionToken);
  const meta = registry.sessions[oldHash];
  if (!meta) {
    return { ok: false, message: "Сессия не найдена", code: "session_revoked" };
  }
  const record = registry.keys[meta.key_hash];
  if (!record) {
    return { ok: false, message: "Аккаунт не найден", code: "invalid_api_key" };
  }
  const device = record.devices.find((item) => item.device_id === input.deviceId);
  if (!device) {
    return { ok: false, message: "Устройство отключено", code: "device_evicted" };
  }

  purgeSession(registry, oldHash);
  const token = newSessionToken();
  const tokenHash = hashSecret(token);
  const exp = expiresAt();
  device.session_token_hash = tokenHash;
  device.expires_at = exp;
  device.last_seen = new Date().toISOString();
  registry.sessions[tokenHash] = {
    key_hash: meta.key_hash,
    device_id: input.deviceId,
    user_id: record.user_id,
    expires_at: exp,
  };
  save(registry);

  return {
    ok: true,
    session_token: token,
    session_expires_at: exp,
    user_id: record.user_id,
    display_name: record.display_name,
    model: `user:${record.user_id}`,
    devices: publicDevices(record),
    message: "Сессия обновлена.",
  };
}

export function logoutSession(input: { deviceId: string; sessionToken?: string }): void {
  if (!input.sessionToken) return;
  const registry = load();
  const tokenHash = hashSecret(input.sessionToken);
  const meta = registry.sessions[tokenHash];
  if (!meta || meta.device_id !== input.deviceId) {
    purgeSession(registry, tokenHash);
    save(registry);
    return;
  }
  const record = registry.keys[meta.key_hash];
  purgeSession(registry, tokenHash);
  if (record) {
    record.devices = record.devices.filter((item) => item.device_id !== input.deviceId);
    if (record.devices.length === 0) delete registry.keys[meta.key_hash];
    else registry.keys[meta.key_hash] = record;
  }
  save(registry);
}

/** @deprecated use logoutSession */
export function logoutDevice(input: { apiKey: string; deviceId: string }): void {
  const registry = load();
  const keyHash = hashApiKey(input.apiKey);
  const record = registry.keys[keyHash];
  if (!record) return;
  for (const device of record.devices) {
    if (device.device_id === input.deviceId) purgeSession(registry, device.session_token_hash);
  }
  record.devices = record.devices.filter((item) => item.device_id !== input.deviceId);
  if (record.devices.length === 0) delete registry.keys[keyHash];
  else registry.keys[keyHash] = record;
  save(registry);
}
import { homedir } from "os";
import path from "path";

export const AUTH_DIR = path.join(homedir(), ".oko-efora");
export const CREDENTIALS_FILE = path.join(AUTH_DIR, "credentials.json");
export const DEVICE_FILE = path.join(AUTH_DIR, "device.json");
export const REGISTRY_FILE = path.join(AUTH_DIR, "auth-registry.json");

export const MAX_DEVICES_PER_KEY = 2;
export const GUEST_DAILY_LIMIT = 50;
/** Локальная сессия живёт 30 дней; сервер может задать своё. */
export const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { hostname } from "os";
import { randomUUID } from "crypto";
import { AUTH_DIR, DEVICE_FILE } from "./paths";

type DeviceFile = {
  device_id: string;
  device_name: string;
  created_at: string;
};

function ensureDir() {
  if (!existsSync(AUTH_DIR)) mkdirSync(AUTH_DIR, { recursive: true, mode: 0o700 });
}

export function getOrCreateDevice(): DeviceFile {
  ensureDir();
  if (existsSync(DEVICE_FILE)) {
    try {
      const raw = JSON.parse(readFileSync(DEVICE_FILE, "utf8")) as DeviceFile;
      if (raw.device_id) return raw;
    } catch {
      // recreate below
    }
  }
  const device: DeviceFile = {
    device_id: `dev_${randomUUID().replace(/-/g, "")}`,
    device_name: hostname() || "device",
    created_at: new Date().toISOString(),
  };
  writeFileSync(DEVICE_FILE, JSON.stringify(device, null, 2), { encoding: "utf8", mode: 0o600 });
  return device;
}

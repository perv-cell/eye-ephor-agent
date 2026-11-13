import { getOrCreateDevice } from "@/lib/auth/device";
import { readCredentials, writeCredentials } from "@/lib/auth/credentials";
import { ensureGuest, logoutSession } from "@/lib/auth/registry";
import type { AuthSessionResponse, CredentialsFile } from "@/lib/auth/types";

export async function POST() {
  const device = getOrCreateDevice();
  const saved = readCredentials();
  if (saved?.session_token) {
    logoutSession({ deviceId: device.device_id, sessionToken: saved.session_token });
  }

  const guest = ensureGuest(device.device_id);
  const credentials: CredentialsFile = {
    version: 2,
    device_id: device.device_id,
    device_name: device.device_name,
    mode: "guest",
    training_allowed: false,
    model: guest.model,
    quota: guest.quota,
    auth_message: "Вы вышли. Снова гостевой режим с общей моделью.",
    saved_at: new Date().toISOString(),
    need_reauth: false,
  };
  writeCredentials(credentials);

  const body: AuthSessionResponse = {
    ok: true,
    mode: "guest",
    device_id: device.device_id,
    training_allowed: false,
    model: guest.model,
    quota: guest.quota,
    auth_message: credentials.auth_message,
    need_reauth: false,
  };
  return Response.json(body);
}

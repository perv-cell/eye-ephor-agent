import { getOrCreateDevice } from "@/lib/auth/device";
import { readCredentials, writeCredentials } from "@/lib/auth/credentials";
import { rotateSession } from "@/lib/auth/registry";
import type { AuthSessionResponse, CredentialsFile } from "@/lib/auth/types";

export async function POST() {
  const device = getOrCreateDevice();
  const saved = readCredentials();
  if (!saved?.session_token || saved.mode !== "user") {
    return Response.json(
      {
        ok: false,
        mode: "guest",
        device_id: device.device_id,
        training_allowed: false,
        model: "base",
        auth_message: "Нет сессии для обновления",
        need_reauth: true,
      } satisfies AuthSessionResponse,
      { status: 401 },
    );
  }

  const rotated = rotateSession({
    deviceId: device.device_id,
    sessionToken: saved.session_token,
  });

  if (!rotated.ok || !rotated.session_token) {
    return Response.json(
      {
        ok: false,
        mode: "guest",
        device_id: device.device_id,
        training_allowed: false,
        model: "base",
        auth_message: rotated.message,
        need_reauth: true,
        code: rotated.code,
      } satisfies AuthSessionResponse & { code?: string },
      { status: 401 },
    );
  }

  const credentials: CredentialsFile = {
    version: 2,
    device_id: device.device_id,
    device_name: device.device_name,
    mode: "user",
    user_id: rotated.user_id,
    display_name: rotated.display_name,
    training_allowed: true,
    model: rotated.model || `user:${rotated.user_id}`,
    session_token: rotated.session_token,
    session_expires_at: rotated.session_expires_at,
    auth_message: rotated.message,
    saved_at: new Date().toISOString(),
    need_reauth: false,
  };
  writeCredentials(credentials);

  const body: AuthSessionResponse = {
    ok: true,
    mode: "user",
    device_id: device.device_id,
    user_id: rotated.user_id,
    display_name: rotated.display_name,
    training_allowed: true,
    model: credentials.model,
    auth_message: rotated.message,
    need_reauth: false,
    session_expires_at: rotated.session_expires_at,
    devices: rotated.devices,
  };
  return Response.json(body);
}

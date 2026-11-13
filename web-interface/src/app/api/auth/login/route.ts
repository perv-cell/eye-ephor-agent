import { getOrCreateDevice } from "@/lib/auth/device";
import { writeCredentials } from "@/lib/auth/credentials";
import { loginWithApiKey } from "@/lib/auth/registry";
import { authBackendMode, remoteLogin } from "@/lib/auth/remote";
import type { AuthSessionResponse, CredentialsFile } from "@/lib/auth/types";

export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as { api_key?: string } | null;
  const apiKey = body?.api_key?.trim() || "";
  const device = getOrCreateDevice();

  let userId = "";
  let displayName = "";
  let trainingAllowed = true;
  let model = "base";
  let sessionToken = "";
  let sessionExpiresAt = "";
  let message = "";
  let devices: AuthSessionResponse["devices"] = [];
  let evicted: string | null = null;

  if (authBackendMode() === "remote") {
    const remote = await remoteLogin({
      apiKey,
      deviceId: device.device_id,
      deviceName: device.device_name,
    });
    if (!remote || !remote.ok) {
      return Response.json(
        {
          ok: false,
          mode: "guest",
          device_id: device.device_id,
          training_allowed: false,
          model: "base",
          auth_message: remote?.message || "Сервер отклонил ключ",
          need_reauth: true,
          code: remote && "code" in remote ? remote.code : "invalid_api_key",
        } satisfies AuthSessionResponse & { code?: string },
        { status: 400 },
      );
    }
    if (!remote.session_token) {
      return Response.json(
        {
          ok: false,
          mode: "guest",
          device_id: device.device_id,
          training_allowed: false,
          model: "base",
          auth_message: "Сервер не выдал session_token",
          need_reauth: true,
          code: "missing_session",
        } satisfies AuthSessionResponse & { code?: string },
        { status: 502 },
      );
    }
    userId = remote.user_id;
    displayName = remote.display_name || remote.user_id;
    trainingAllowed = remote.training_allowed !== false;
    model = remote.model || `user:${remote.user_id}`;
    sessionToken = remote.session_token;
    sessionExpiresAt = remote.session_expires_at || "";
    message = remote.message || "Ключ принят.";
    devices = remote.devices || [];
    evicted = remote.evicted_device_id ?? null;
  } else {
    const result = loginWithApiKey({
      apiKey,
      deviceId: device.device_id,
      deviceName: device.device_name,
    });
    if (!result.ok) {
      return Response.json(
        {
          ok: false,
          mode: "guest",
          device_id: device.device_id,
          training_allowed: false,
          model: "base",
          auth_message: result.message,
          need_reauth: true,
          code: result.code,
        } satisfies AuthSessionResponse & { code?: string },
        { status: 400 },
      );
    }
    userId = result.user_id;
    displayName = result.display_name;
    trainingAllowed = result.training_allowed;
    model = result.model;
    sessionToken = result.session_token;
    sessionExpiresAt = result.session_expires_at;
    message = result.message;
    devices = result.devices;
    evicted = result.evicted_device_id;
  }

  // API-ключ намеренно НЕ сохраняем — только session_token
  const credentials: CredentialsFile = {
    version: 2,
    device_id: device.device_id,
    device_name: device.device_name,
    mode: "user",
    user_id: userId,
    display_name: displayName,
    training_allowed: trainingAllowed,
    model,
    session_token: sessionToken,
    session_expires_at: sessionExpiresAt || undefined,
    auth_message: message,
    saved_at: new Date().toISOString(),
    need_reauth: false,
  };
  writeCredentials(credentials);

  const response: AuthSessionResponse = {
    ok: true,
    mode: "user",
    device_id: device.device_id,
    user_id: userId,
    display_name: displayName,
    training_allowed: trainingAllowed,
    model,
    auth_message: message,
    need_reauth: false,
    session_expires_at: sessionExpiresAt || undefined,
    devices,
    evicted_device_id: evicted,
  };
  return Response.json(response);
}

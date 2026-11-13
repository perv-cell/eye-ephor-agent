import { getOrCreateDevice } from "@/lib/auth/device";
import { readCredentials, writeCredentials } from "@/lib/auth/credentials";
import { ensureGuest, validateSession } from "@/lib/auth/registry";
import { authBackendMode, remoteSession } from "@/lib/auth/remote";
import type { AuthSessionResponse, CredentialsFile } from "@/lib/auth/types";

function guestPayload(
  device: { device_id: string; device_name: string },
  message: string,
  needReauth: boolean,
  devices?: AuthSessionResponse["devices"],
): { credentials: CredentialsFile; body: AuthSessionResponse } {
  const guest = ensureGuest(device.device_id);
  const credentials: CredentialsFile = {
    version: 2,
    device_id: device.device_id,
    device_name: device.device_name,
    mode: "guest",
    training_allowed: false,
    model: guest.model,
    quota: guest.quota,
    auth_message: message || guest.message,
    saved_at: new Date().toISOString(),
    need_reauth: needReauth,
  };
  const body: AuthSessionResponse = {
    ok: true,
    mode: "guest",
    device_id: device.device_id,
    training_allowed: false,
    model: guest.model,
    quota: guest.quota,
    auth_message: credentials.auth_message,
    need_reauth: needReauth,
    devices,
  };
  return { credentials, body };
}

export async function GET() {
  const device = getOrCreateDevice();
  const saved = readCredentials();

  // Старые файлы с api_key без session — принудительный re-auth
  if (saved?.mode === "user" && !saved.session_token) {
    const { credentials, body } = guestPayload(
      device,
      "Нужен повторный вход: сессия обновлена до безопасного формата без хранения API-ключа.",
      true,
    );
    writeCredentials(credentials);
    return Response.json(body);
  }

  if (saved?.mode === "user" && saved.session_token) {
    if (authBackendMode() === "remote") {
      const remote = await remoteSession({
        deviceId: device.device_id,
        sessionToken: saved.session_token,
      });
      if (remote?.ok && remote.mode === "user" && !remote.need_reauth) {
        const next: CredentialsFile = {
          version: 2,
          device_id: device.device_id,
          device_name: device.device_name,
          mode: "user",
          user_id: remote.user_id,
          display_name: remote.display_name,
          training_allowed: remote.training_allowed,
          model: remote.model,
          session_token: saved.session_token,
          session_expires_at: remote.session_expires_at,
          auth_message: remote.auth_message,
          saved_at: new Date().toISOString(),
          need_reauth: false,
        };
        writeCredentials(next);
        return Response.json(remote);
      }
      const { credentials, body } = guestPayload(
        device,
        remote?.auth_message || "Сессия на сервере недействительна. Введите API-ключ заново.",
        true,
        remote?.devices,
      );
      writeCredentials(credentials);
      return Response.json(body);
    }

    const check = validateSession({
      deviceId: device.device_id,
      sessionToken: saved.session_token,
    });

    if (check.ok) {
      const next: CredentialsFile = {
        version: 2,
        device_id: device.device_id,
        device_name: device.device_name,
        mode: "user",
        user_id: check.user_id,
        display_name: check.display_name,
        training_allowed: true,
        model: check.model,
        session_token: saved.session_token,
        session_expires_at: check.session_expires_at,
        auth_message: check.message,
        saved_at: new Date().toISOString(),
        need_reauth: false,
      };
      writeCredentials(next);
      const body: AuthSessionResponse = {
        ok: true,
        mode: "user",
        device_id: device.device_id,
        user_id: check.user_id,
        display_name: check.display_name,
        training_allowed: true,
        model: check.model,
        auth_message: check.message,
        need_reauth: false,
        session_expires_at: check.session_expires_at,
        devices: check.devices,
      };
      return Response.json(body);
    }

    const { credentials, body } = guestPayload(device, check.message, true, check.devices);
    writeCredentials(credentials);
    return Response.json(body);
  }

  const { credentials, body } = guestPayload(device, "", false);
  writeCredentials(credentials);
  return Response.json(body);
}

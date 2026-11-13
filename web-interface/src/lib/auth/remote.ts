import type { AuthSessionResponse, DeviceInfo, QuotaInfo } from "./types";

const AUTH_URL = (process.env.AUTH_URL || "").replace(/\/$/, "");

export function authBackendMode(): "remote" | "local" {
  return AUTH_URL ? "remote" : "local";
}

type RemoteLoginOk = {
  ok: true;
  user_id: string;
  display_name?: string;
  training_allowed?: boolean;
  model?: string;
  session_token?: string;
  session_expires_at?: string;
  devices?: DeviceInfo[];
  evicted_device_id?: string | null;
  message?: string;
  quota?: QuotaInfo;
};

type RemoteLoginFail = {
  ok: false;
  message?: string;
  code?: string;
};

export async function remoteLogin(input: {
  apiKey: string;
  deviceId: string;
  deviceName: string;
}): Promise<RemoteLoginOk | RemoteLoginFail | null> {
  if (!AUTH_URL) return null;
  try {
    const response = await fetch(`${AUTH_URL}/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: input.apiKey,
        device_id: input.deviceId,
        device_name: input.deviceName,
      }),
    });
    const data = (await response.json()) as RemoteLoginOk | RemoteLoginFail;
    if (!response.ok) {
      return {
        ok: false,
        message: ("message" in data && data.message) || "Сервер отклонил ключ",
        code: ("code" in data && data.code) || "remote_auth_failed",
      };
    }
    return data;
  } catch {
    return {
      ok: false,
      message: "Нет связи с сервером авторизации",
      code: "auth_unreachable",
    };
  }
}

export async function remoteSession(input: {
  deviceId: string;
  sessionToken: string;
}): Promise<AuthSessionResponse | null> {
  if (!AUTH_URL) return null;
  try {
    const response = await fetch(`${AUTH_URL}/session`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${input.sessionToken}`,
      },
      body: JSON.stringify({
        device_id: input.deviceId,
        session_token: input.sessionToken,
      }),
    });
    if (!response.ok) return null;
    return (await response.json()) as AuthSessionResponse;
  } catch {
    return null;
  }
}

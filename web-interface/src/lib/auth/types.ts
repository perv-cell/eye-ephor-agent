export type AuthMode = "guest" | "user";

export type QuotaInfo = {
  remaining: number;
  limit: number;
  period: "day";
};

export type DeviceInfo = {
  device_id: string;
  name: string;
  last_seen: string;
};

/**
 * Локальный кэш сессии. Сырой API-ключ сюда НЕ пишем —
 * только device-bound session_token (отзываемый на сервере).
 */
export type CredentialsFile = {
  version: 2;
  device_id: string;
  device_name: string;
  mode: AuthMode;
  user_id?: string;
  display_name?: string;
  training_allowed: boolean;
  model: string;
  quota?: QuotaInfo;
  session_token?: string;
  session_expires_at?: string;
  auth_message: string;
  saved_at: string;
  need_reauth?: boolean;
};

export type AuthSessionResponse = {
  ok: boolean;
  mode: AuthMode;
  device_id: string;
  user_id?: string;
  display_name?: string;
  training_allowed: boolean;
  model: string;
  quota?: QuotaInfo;
  auth_message: string;
  need_reauth: boolean;
  session_expires_at?: string;
  devices?: DeviceInfo[];
  evicted_device_id?: string | null;
};

export type LoginRequest = {
  api_key: string;
  device_id: string;
  device_name?: string;
};

export type GuestEnsureRequest = {
  device_id: string;
  device_name?: string;
};

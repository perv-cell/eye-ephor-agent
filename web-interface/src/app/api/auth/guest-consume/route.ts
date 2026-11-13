import { getOrCreateDevice } from "@/lib/auth/device";
import { readCredentials, writeCredentials } from "@/lib/auth/credentials";
import { consumeGuestRequest } from "@/lib/auth/registry";

export async function POST() {
  const device = getOrCreateDevice();
  const saved = readCredentials();
  if (saved?.mode === "user" && saved.training_allowed) {
    return Response.json({
      ok: true,
      mode: "user",
      model: saved.model,
      message: "Персональный режим: квота гостя не расходуется.",
    });
  }

  const result = consumeGuestRequest(device.device_id);
  if (saved) {
    writeCredentials({
      version: 2,
      device_id: saved.device_id,
      device_name: saved.device_name,
      mode: "guest",
      training_allowed: false,
      model: "base",
      quota: result.quota,
      auth_message: result.message,
      saved_at: new Date().toISOString(),
      need_reauth: saved.need_reauth,
    });
  }

  return Response.json(
    {
      ok: result.ok,
      mode: "guest",
      model: "base",
      quota: result.quota,
      message: result.message,
      code: result.ok ? undefined : "quota_exceeded",
    },
    { status: result.ok ? 200 : 429 },
  );
}

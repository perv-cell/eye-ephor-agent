# Контракт Auth / Training API (Око Эфора)

Базовый URL: `{AUTH_URL}` например `https://api.example.com/api/v1/auth`  
Training URL: `{TRAINING_URL}` например `https://api.example.com/api/v1/train/logs`

Клиент **не хранит** сырой `api_key` после логина. На диске только:
- `device_id`
- `session_token` (device-bound, отзывный)
- `user_id`, `model`, метаданные

На сервере хранить **hash(session_token)**, не plaintext. Лимит: **2 устройства** на один API-ключ.

---

## Общие коды ошибок

| code | HTTP | смысл |
|------|------|--------|
| `invalid_api_key` | 400/401 | ключ неверный / отозван |
| `missing_session` | 401 | нет session_token |
| `session_revoked` | 401 | сессия отозвана |
| `session_expired` | 401 | истек TTL |
| `device_mismatch` | 401 | token от другого device |
| `device_evicted` | 401 | устройство вытеснено (лимит 2) |
| `quota_exceeded` | 429 | гостевой лимит |
| `auth_unreachable` | 503 | auth недоступен |

---

## `POST {AUTH_URL}/login`

Обмен API-ключа на device-сессию. При 3-м устройстве — вытеснить самое старое (`evicted_device_id`).

### Request
```json
{
  "api_key": "user-secret-key",
  "device_id": "dev_…",
  "device_name": "laptop-hostname"
}
```

### Response 200
```json
{
  "ok": true,
  "user_id": "usr_…",
  "display_name": "Ivan",
  "training_allowed": true,
  "model": "user:usr_…",
  "session_token": "ses_…",
  "session_expires_at": "2026-12-12T12:00:00Z",
  "devices": [
    { "device_id": "dev_a", "name": "pc1", "last_seen": "…" },
    { "device_id": "dev_b", "name": "pc2", "last_seen": "…" }
  ],
  "evicted_device_id": null,
  "message": "Ключ принят. Персональное обучение включено."
}
```

### Response 400
```json
{
  "ok": false,
  "code": "invalid_api_key",
  "message": "Ключ не найден или отозван."
}
```

Правила сервера:
1. Проверить `api_key`.
2. Найти/создать user.
3. Если `device_id` уже в слотах — ротировать session этого устройства.
4. Иначе если слотов уже 2 — удалить oldest, отозвать его session, вернуть `evicted_device_id`.
5. Выдать новый `session_token`, сохранить только hash + `device_id` + `expires_at`.
6. **Не** требовать и не возвращать `user_id` от клиента как источник истины.

---

## `POST {AUTH_URL}/session`

Проверка текущей сессии (без API-ключа).

### Headers
`Authorization: Bearer {session_token}`

### Request
```json
{
  "device_id": "dev_…",
  "session_token": "ses_…"
}
```

### Response 200 (активна)
```json
{
  "ok": true,
  "mode": "user",
  "device_id": "dev_…",
  "user_id": "usr_…",
  "display_name": "Ivan",
  "training_allowed": true,
  "model": "user:usr_…",
  "session_expires_at": "…",
  "auth_message": "Аккаунт активен…",
  "need_reauth": false,
  "devices": [ … ]
}
```

### Response 401 (нужен re-auth)
```json
{
  "ok": false,
  "mode": "guest",
  "device_id": "dev_…",
  "training_allowed": false,
  "model": "base",
  "auth_message": "Устройство отключено…",
  "need_reauth": true,
  "code": "device_evicted"
}
```

---

## `POST {AUTH_URL}/refresh`

Ротация session (anti reuse при копировании файла).

### Headers
`Authorization: Bearer {old_session_token}`

### Request
```json
{ "device_id": "dev_…", "session_token": "ses_old…" }
```

### Response 200
```json
{
  "ok": true,
  "session_token": "ses_new…",
  "session_expires_at": "…",
  "user_id": "usr_…",
  "display_name": "Ivan",
  "model": "user:usr_…",
  "message": "Сессия обновлена."
}
```

Правила:
- Старый token сразу invalid.
- Если старый token уже был использован после revoke (reuse detection) — отозвать **все** сессии этого user и вернуть `need_reauth`.

---

## `POST {AUTH_URL}/logout`

### Headers
`Authorization: Bearer {session_token}`

### Request
```json
{ "device_id": "dev_…" }
```

### Response 200
```json
{ "ok": true, "message": "Сессия отозвана." }
```

Освобождает device-слот.

---

## Гостевой инференс (без ключа)

### `POST {AUTH_URL}/guest/session` или ensure на клиенте
Клиент создаёт стабильный `device_id` локально.

### `POST {API}/v1/infer` (гость)
```http
X-Device-Id: dev_…
X-Auth-Mode: guest
```

### Response
```json
{
  "ok": true,
  "mode": "guest",
  "model": "base",
  "quota": { "remaining": 17, "limit": 50, "period": "day" },
  "message": "Используется общая модель. Осталось 17 запросов сегодня."
}
```

### 429
```json
{
  "ok": false,
  "code": "quota_exceeded",
  "quota": { "remaining": 0, "limit": 50, "period": "day" },
  "message": "Лимит гостевых запросов исчерпан. Введите API-ключ…"
}
```

---

## `POST {TRAINING_URL}` — дообучение

Только для session с `training_allowed=true`. Идентификация **только по session**, не по api_key в теле.

### Headers
```http
Authorization: Bearer {session_token}
X-Device-Id: dev_…
X-User-Id: usr_…          # informational; сервер сверяет с session
X-Model-Id: user:usr_…
X-Model-Hint: isolation_forest
X-Auth-Mode: session
Content-Type: application/json
```

### Body
```json
{
  "batch_id": "…",
  "agent_id": "…",
  "user_id": "usr_…",
  "host": "…",
  "os": "linux",
  "sent_at": "…",
  "count": 10,
  "schema_version": 2,
  "model_hint": "isolation_forest",
  "records": [ { "id": "…", "features": { … }, "message": "…" } ]
}
```

### Response 202
```json
{
  "ok": true,
  "user_id": "usr_…",
  "model": "user:usr_…",
  "accepted": 10,
  "personalization": { "status": "collecting", "batches": 4 },
  "message": "Батч принят для дообучения вашей модели."
}
```

### Response 401
Сессия битая / device_evicted → клиент ставит `need_reauth` и просит API-ключ снова.

---

## Инференс пользователя

```http
Authorization: Bearer {session_token}
X-Device-Id: dev_…
X-Auth-Mode: session
```

Сервер маршрутизирует в `model = user:{user_id}`; если персональной ещё нет / мало данных — fallback на `base` с полем:
```json
{ "model_used": "base", "personalization": { "status": "warming_up" } }
```

---

## Модель данных на сервере (рекомендация)

```text
users:     id, display_name, api_key_hash, created_at
devices:   user_id, device_id, name, last_seen  (UNIQUE(user_id, device_id), max 2 per user)
sessions:  token_hash, user_id, device_id, expires_at, revoked_at
guests:    device_id, day, used, limit
models:    user_id | "base", path/version, updated_at
```

---

## Локальный кэш клиента `~/.oko-efora/credentials.json` (v2)

```json
{
  "version": 2,
  "device_id": "dev_…",
  "device_name": "host",
  "mode": "user",
  "user_id": "usr_…",
  "display_name": "Ivan",
  "training_allowed": true,
  "model": "user:usr_…",
  "session_token": "ses_…",
  "session_expires_at": "…",
  "auth_message": "…",
  "saved_at": "…",
  "need_reauth": false
}
```

**Запрещено:** поле `api_key` в этом файле.

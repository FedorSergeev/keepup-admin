## Why

Развёртывание без внешнего провайдера держало вход администратора на одном
пароле. Пароль может быть длинным, но он один: утёк — вошли. Второго фактора у
каркаса не было вовсе (keepup-174).

## What Changes

- TOTP по RFC 6238 с параметрами, которые ждёт любое приложение-аутентификатор:
  SHA-1, 30 секунд, шесть цифр. Секрет — в таблице `auth_totp` аккаунта,
  подтверждение — `confirmed_at`, код одноразовый (`last_step`).
- Эндпоинты: `GET /api/auth/mfa`, `POST /api/auth/mfa/totp/start`,
  `POST /api/auth/mfa/totp/confirm`, `POST /api/auth/mfa/totp/disable`,
  `POST /api/auth/mfa/verify`. Десять recovery-кодов хранятся хешами и
  показываются один раз.
- Вход с подтверждённым фактором отвечает `mfa_required` и пятиминутным
  challenge вместо сессии; `verify` меняет код (или recovery-код) на сессию с
  `amr = ["pwd", "otp"]`.
- `MFA_REQUIRED_FOR_ADMINS=true` закрывает административные маршруты сессии без
  `otp`; по умолчанию выключено, чтобы обновление не заперло администратора до
  зачисления. Зачисление идёт через `get_current_user`, не через админ-право,
  поэтому включённая политика не мешает зачислить фактор.
- Клиент (панель приложения) рисует QR из `otpauth_uri`; каркас отвечает
  состоянием и URI.

## Capabilities

### Modified Capabilities

- `auth`: у входа есть второй фактор, и развёртывание может его требовать.

## Impact

- `packages/keepup-auth/keepup_auth/mfa.py`, `mfa_routes.py` (новые),
  `tables.py`, `routes.py`, `dependencies.py`; `builtin/auth.py` объявляет
  таблицу.
- `doc/table-ownership.md`, `tests/security_audit_tests/route_sweep_tests.py`
  (публичный `verify`), `tests/mfa_tests.py`.
- `AGENTS.md` — второй фактор в разделе о входе.

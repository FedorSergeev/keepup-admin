## Why

Серверная часть второго фактора появилась в keepup-174: вход с подтверждённым
TOTP отвечает `mfa_required` и пятиминутным `mfa_token` вместо сессии, а
эндпоинты зачисления и проверки кода уже есть. Оболочка панели этого не знала:
`login()` считал любой 200 успехом, ставил маркер, планировал продление и шёл в
`/api/auth/me` без сессии. Администратор видел «Failed to get user data» и в
панель не попадал; экрана зачисления не было вовсе. Фактор был API-функцией,
которая запирала панель тому, кто её включал (keepup-176).

## What Changes

- `mfa_panel.js` — клиентская половина: поле кода, которое форма входа
  показывает после пароля, и экран «Two-factor authentication» с зачислением,
  подтверждением, recovery-кодами и отключением.
- `login()` различает `mfa_required` и сессию; общий `finishSignIn(data)` —
  единственное место, где вход становится сессией, и он отказывается ставить
  маркер без `access_token`.
- Форма входа в режиме челленджа отправляет код (или recovery-код) в
  `/api/auth/mfa/verify`; «Back to the password» возвращает обычную форму.
- Экран зачисления показывает секрет и `otpauth_uri` (QR рисует клиент, у
  которого есть QR; текст достаточен), recovery-коды — один раз; отключение
  требует действующий код.
- Обе страницы панели (`index_new.html`, `index_nebula.html`) загружают
  `mfa_panel.js` до оболочки.
- Всё строится `createElement`/`textContent`: ни разметки со значениями, ни
  inline-обработчиков.

## Capabilities

### Modified Capabilities

- `auth`: вход по коду и управление вторым фактором доступны из панели.

## Impact

- `packages/keepup-http/keepup_http/static/js/mfa_panel.js` (новый),
  `js/main_new.js`, `index_new.html`, `index_nebula.html`.
- `tests/mfa_panel_tests.py`; `panel_inline_handlers_tests.py` и
  `panel_actions_tests.py` остаются зелёными.
- `tests/security_audit_tests/route_sweep_tests.py` не меняется: новых
  маршрутов нет.

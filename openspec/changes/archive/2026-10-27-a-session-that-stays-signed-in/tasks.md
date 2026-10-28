## 1. Запись сессии

- [x] 1.1 `auth_session`: `remember` и `refresh_hash` (только дайджест) с
  индексом; строка помнящей сессии живёт окно продления, обычной — час.
  Проверка: `stay_signed_in_tests.py`.
- [x] 1.2 `session_lifetime`: `SESSION_REMEMBER_DAYS` (30 дней) и
  `is_renewable(..., remember=True)`; сутки от входа остаются для обычной
  сессии. Проверка: `session_rotation_tests.py`, `stay_signed_in_tests.py`.

## 2. Маршруты

- [x] 2.1 `POST /api/auth/login` принимает `remember` и выдаёт `ss_refresh`
  (HttpOnly, `__Host-` по HTTPS). Cookie помнящей сессии — 30 дней, обычной —
  до закрытия браузера. Проверка: `stay_signed_in_tests.py`,
  `host_prefix_cookies_tests.py`.
- [x] 2.2 `POST /api/auth/refresh`: живой токен — как раньше; истёкший —
  по удостоверению и паре CSRF, без смены `sid`. Проверка:
  `stay_signed_in_tests.py`.
- [x] 2.3 Отказ удостоверению по отзыву, блокировке и истёкшему окну — 401/403;
  cookie гасится. Проверка: `stay_signed_in_tests.py`.
- [x] 2.4 Выход отзывает сессию и при истёкшем токене. Проверка:
  `stay_signed_in_tests.py`.
- [x] 2.5 Второй фактор несёт `remember` через challenge. Проверка:
  `stay_signed_in_tests.py`, `mfa_tests.py`.

## 3. Панель

- [x] 3.1 Галочка на обеих страницах, `remember` в теле входа. Проверка:
  `stay_signed_in_tests.py`.
- [x] 3.2 Продление за 5 минут, `/api/auth/verify` отдаёт `expires_in`,
  загрузка и пробуждение продлевают тихо, повтор при 5xx. Проверка:
  `stay_signed_in_tests.py` (node).

## 4. Набор

- [x] 4.1 `stay_signed_in_tests.py`, `sign_in_screen_tests.py`,
  `session_*_tests.py`, `security_audit_tests` зелёные.

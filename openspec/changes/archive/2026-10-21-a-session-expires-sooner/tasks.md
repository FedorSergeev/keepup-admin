## 1. Сроки

- [x] 1.1 60 минут, 24 часа продления, 5 минут предупреждения; токен без
  `sst` не продлевается. Проверка: `session_rotation_tests.py`.
- [x] 1.2 `iss`/`aud` в токене и отказ чужому сервису. Проверка:
  `session_rotation_tests.py`, `security_audit_tests`.

## 2. Ключ

- [x] 2.1 `SECRET_KEY_PREVIOUS`, `decode_token` для всех проверок подписи.
  Проверка: `session_rotation_tests.py`.

## 3. Набор

- [x] 3.1 `auth_*_tests.py`, `security_audit_tests`, `session_rotation_tests.py`
  зелёные.

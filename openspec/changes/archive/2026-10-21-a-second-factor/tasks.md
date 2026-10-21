## 1. Ядро фактора

- [x] 1.1 `mfa.py`: TOTP, одноразовый шаг, recovery-коды, challenge.
  Проверка: `mfa_tests.py`.
- [x] 1.2 `auth_totp` в `tables.py` и `builtin/auth.py`; владелец в
  `doc/table-ownership.md`. Проверка: `table_ownership_tests.py`.

## 2. Маршруты

- [x] 2.1 start/confirm/disable/status/verify; публичный `verify` назван в
  route sweep. Проверка: `mfa_tests.py`, `security_audit_tests`.
- [x] 2.2 Вход отвечает `mfa_required`; `issue_session_token(..., mfa=True)`
  кладёт `amr`. Проверка: `mfa_tests.py`.

## 3. Политика

- [x] 3.1 `MFA_REQUIRED_FOR_ADMINS` в `get_current_admin`.
  Проверка: `mfa_tests.py`.

## 4. Набор

- [x] 4.1 `mfa_tests.py`, `auth_*_tests.py`, `security_audit_tests` зелёные.

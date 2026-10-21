## 1. Актор

- [x] 1.1 `incoming_requests`: колонки `user_id`, `username`; `start_request`
  и `log_api_request` принимают актора; сброс пишет их.
  Проверка: `audit_actor_tests.py`.
- [x] 1.2 `start_call(actor=...)` и `audited(...)` в ядре; маршруты плагинов
  передают актора вне тела. Проверка: `audit_actor_tests.py`.

## 2. Маршруты каркаса

- [x] 2.1 Профиль, пароль, роли и правка пользователя;
  команда остановки/перезапуска реплики. Проверка: `audit_actor_tests.py`,
  `security_audit_tests`.

## 3. Отказ во входе

- [x] 3.1 Журнал с именем и адресом; `login_attempts.last_address`.
  Проверка: `audit_actor_tests.py`.

## 4. Набор

- [x] 4.1 `audit_*_tests.py`, `auth_*_tests.py`, `security_audit_tests` зелёные.

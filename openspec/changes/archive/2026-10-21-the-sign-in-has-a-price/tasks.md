## 1. Счётчики

- [x] 1.1 `auth_rate_limit.py`: два ведра, атомарный счёт, окно минуты,
  `Retry-After`, уборка старых окон, отказ при недоступной таблице.
  Проверка: `auth_rate_limit_tests.py`.
- [x] 1.2 `tables.py` и `builtin/auth.py`: таблица `auth_rate_limit` объявлена
  возможностью. Проверка: `auth_plugin_tests.py`, `table_ownership_tests.py`.
- [x] 1.3 `routes.py` и `oidc_routes.py`: проверка в login, register, refresh и
  обоих маршрутах OIDC. Проверка: `auth_rate_limit_tests.py`.

## 2. Документы

- [x] 2.1 `doc/table-ownership.md`.

## 3. Набор

- [x] 3.1 `auth_rate_limit_tests.py`, `auth_plugin_tests.py`,
  `table_ownership_tests.py` и security audit зелёные.

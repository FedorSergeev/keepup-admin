## 1. Право на маршрутах

- [x] 1.1 `plugin.py`: право `integration_logs.admin` на четырёх маршрутах,
  `adminOnly` у раздела. Проверка: `integration_log_plugin_tests.py`.
- [x] 1.2 Тесты: право названо у каждого маршрута, клиент получает 403,
  администратор — прежний ответ, анонимный — 401. Проверка:
  `integration_log_plugin_tests.py`.

## 2. Набор

- [x] 2.1 `tests/integration_log_plugin_tests.py` и
  `tests/security_audit_tests/route_sweep_tests.py` зелёные.

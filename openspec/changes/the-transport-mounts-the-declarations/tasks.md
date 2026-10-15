## 1. Транспорт монтирует объявленное

- [x] 1.1 `register_declared_routes`/`register_declared_sockets` — HTTP-половина
  `runtime.mount`. Проверка: `kernel_in_create_app_tests.py` — приложение,
  назвавшее только `plugins_dir`, отвечает на `/api/demo` и `/ws/demo`.
- [x] 1.2 Одна реализация монтирования на оба пути. Проверка:
  `contributions_tests.py`, `route_parameters_tests.py`, `body_limit_tests.py` —
  прежние наборы зелёные.
- [x] 1.3 Адрес, который приложение уже обслуживает, не монтируется дважды.
  Проверка: `kernel_in_create_app_tests.py` — повторный вызов возвращает 0;
  сквозная проверка на полном приложении — 77 маршрутов, ни одной пары
  (путь, методы) дважды.
- [x] 1.4 Отвергнутое объявление останавливает старт. Проверка:
  `kernel_in_create_app_tests.py`.
- [x] 1.5 `create_app` отдаёт регистраторы ядру. Проверка: сквозная проверка на
  проекте, собранном инициализатором, — GET/POST/DELETE `/api/products` без
  `plugin_manager` в `app.py`.

## 2. Документы

- [x] 2.1 CHANGELOG каркаса, OpenSpec.
- [ ] 2.2 Остаток шва: middleware-вклады ждут переезда сборки приложения в
  транспорт.

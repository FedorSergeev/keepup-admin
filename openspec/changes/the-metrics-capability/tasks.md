## 1. Возможность

- [x] 1.1 Дескриптор, объявление таблицы, раздел с иконкой. Проверка:
  `metrics_plugin_tests.py` — вклады и таблица создаются службой.
- [x] 1.2 `/metrics` с `audit: false` и `response_media_type: text/plain`;
  скрейп без реестра — не 500. Проверка: `metrics_plugin_tests.py`.
- [x] 1.3 Сводка панели по таблице, без реестра кластера; кластер —
  необязательное требование. Проверка: `metrics_plugin_tests.py` — пометка
  `degraded` с именами недостающих служб.
- [x] 1.4 Служба `metrics`: `collect`. Проверка: `metrics_plugin_tests.py`.

## 2. Документы

- [x] 2.1 `tests/metrics_plugin_tests.py`.
- [x] 2.2 CHANGELOG каркаса, OpenSpec, трекер региона keepup — задача 112.

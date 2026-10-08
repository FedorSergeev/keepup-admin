## 1. Возможность

- [x] 1.1 Дескриптор и объявление таблицы: одна таблица, объявленная в
  `schema.py`, без второго объявления. Проверка:
  `integration_log_plugin_tests.py` — таблица создаётся службой.
- [x] 1.2 Служба `integration_log`: запись, чтение, одна запись, статистика,
  чистка; тела усечены. Проверка: `integration_log_plugin_tests.py`.
- [x] 1.3 Четыре маршрута данными, с масками. Проверка:
  `integration_log_plugin_tests.py` — маршрут отвечает через нейтральный вызов.
- [x] 1.4 Срок хранения: `POST /cleanup` удаляет старое. Проверка:
  `integration_log_plugin_tests.py`.
- [x] 1.5 Раздел как вклад, с иконкой. Проверка:
  `integration_log_plugin_tests.py`.

## 2. Расхождение фронта и бэкенда

- [x] 2.1 Проверка читает JavaScript раздела и требует, чтобы каждый вызываемый
  путь был объявлен. Проверка: `integration_log_plugin_tests.py` — четыре пути,
  ни одного лишнего.

## 3. Отсутствие хранилища

- [x] 3.1 Без `datasource` плагин не запускается и говорит почему. Проверка:
  `integration_log_plugin_tests.py`.

## 4. Документы

- [x] 4.1 `tests/integration_log_plugin_tests.py`.
- [x] 4.2 CHANGELOG каркаса, OpenSpec, трекер региона keepup — задача 110.

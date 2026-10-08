## 1. Перенос

- [x] 1.1 `db.py` в `packages/keepup-db/keepup_db/manager.py` с реэкспортом
  объявленных имён. Проверка: полный набор.
- [x] 1.2 Подстановка отвечает и как атрибут пакета. Проверка:
  `one_database_manager_tests.py`.
- [x] 1.3 Проверки, читающие модуль по пути, следуют за именем. Проверка:
  `db_leftovers_tests.py`, `leftovers_tests.py`.
- [x] 1.4 Интерпретаторы, поднимаемые проверками, получают расположение путей
  набора. Проверка: `events_split_tests.py`, `log_shipping_split_tests.py`,
  `themes_tests.py`.
- [x] 1.5 `db.py` вычеркнут из списка долга импорта. Проверка:
  `lazy_database_import_tests.py`.

## 2. Документы

- [x] 2.1 CHANGELOG каркаса, OpenSpec, трекер региона keepup — задачи 124 и 125.

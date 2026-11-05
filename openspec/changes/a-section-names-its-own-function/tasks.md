## 1. Имена приведены к скриптам

- [x] 1.1 `builtin/users.py`: `initUsers` → `renderUsers`.
- [x] 1.2 `keepup_cluster/plugin.py`: `initCluster` → `initClusterModule`.
- [x] 1.3 `integration_logs.js`: `initIntegrationLogs` объявлена на верхнем уровне,
  а не внутри `if`.

## 2. Проверка

- [x] 2.1 `tests/panel_section_init_tests.py`: каждая объявленная секция называет
  функцию, объявленную её скриптом; секция без скрипта названа исключением с
  причиной. Проверка ловит все три случая выше.

## 3. Документы

- [x] 3.1 CHANGELOG каркаса, OpenSpec, трекер региона keepup — задача 185.

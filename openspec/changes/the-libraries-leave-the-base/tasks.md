## 1. Библиотеки

- [x] 1.1 Три библиотеки сняты с базового `pyproject.toml`. Проверка:
  `base_package_freedom_tests.py`.
- [x] 1.2 Упаковка различает импорты базы и дистрибутивов. Проверка:
  `packaging_tests.py`.
- [x] 1.3 Пороги читаются отовсюду и аудируются по высшему. Проверка:
  `dependency_floor_tests.py`, `security_audit_tests/audit_findings_tests.py`.
- [x] 1.4 Документ и карта держателей говорят, что уехало. Проверка:
  `distribution_layout_tests.py`.

## 2. Документы

- [x] 2.1 CHANGELOG каркаса, OpenSpec, трекер региона keepup — задача 124.

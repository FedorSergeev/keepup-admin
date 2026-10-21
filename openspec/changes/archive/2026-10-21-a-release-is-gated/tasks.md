## 1. Релиз

- [x] 1.1 Job `gate`, `needs`, `concurrency`, версии `build`/`twine`, SHA
  действий. Проверка: `release_policy_tests.py`.
- [x] 1.2 Job `sbom` (CycloneDX) и артефакт. Проверка:
  `release_policy_tests.py`.

## 2. Политика

- [x] 2.1 `SECURITY.md`: канал, версии, сроки, область.
  Проверка: `release_policy_tests.py`.

## 3. Контейнер

- [x] 3.1 Compose: loopback, `read_only`, `cap_drop`, `no-new-privileges`,
  лимиты, ротация, том журналов. Проверка: `release_policy_tests.py`.

## 4. Набор

- [x] 4.1 `tests/release_policy_tests.py` зелёный.

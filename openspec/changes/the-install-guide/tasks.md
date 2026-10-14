## 1. Страницы

- [x] 1.1 `INSTALL.md`: требования, таблица экстров, установка из репозитория,
  сборка и релиз, прогон набора, тяжёлые проверки, разбор ошибок. Проверка:
  `tests/install_guide_tests.py`.
- [x] 1.2 `README.md`: разделы Install, Build и Tests, ссылка на `INSTALL.md`,
  «Install first, then test» на месте; рассуждение о бейджах убрано, проверка
  за ним в `tests/badges_tests.py` снята. Проверка: `tests/install_guide_tests.py`.

## 2. Проверка за страницами

- [x] 2.1 Имена — дистрибутив, экстры, четырнадцать `packages/*`, версия
  Python — сверяются с `pyproject.toml` и деревом. Проверка:
  `tests/install_guide_tests.py`.
- [x] 2.2 Команды установки и теста — те же, что в `tests.yml`; сборка — что в
  `build.yml`. Проверка там же.

## 3. Документы

- [x] 3.1 CHANGELOG 0.5.0 — пункт о задаче.
- [x] 3.2 OpenSpec-изменение, трекер региона keepup — задача 156.

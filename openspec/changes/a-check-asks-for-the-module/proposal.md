## Why

Перенос журнала событий (`keepup/events.py` → `keepup-audit`, keepup-124) упал не
на коде, а на проверках, которые **читают исходник по пути**: `PACKAGE /
"events.py"`. Модуль, уехавший в дистрибутив, сохраняет старое имя через слой
совместимости, но путь к файлу перестаёт существовать — и перенос выглядит как
сломанная проверка. Это надо было сделать **до** переноса, как карту владения.

## What Changes

- `keepup/tests/repository.py`: `source_of(module_name)` — файл модуля,
  **спрошенный у модуля**, а не собранный из пути.
- Две проверки, читавшие журнал по пути (`events_split_tests.py`,
  `leftovers_tests.py`), спрашивают его по имени.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: проверка, которая читает исходник перенесённого модуля,
  следует за именем, а не за путём.

## Impact

- `tests/repository.py`, `tests/events_split_tests.py`,
  `tests/leftovers_tests.py`.
- Перенос `events.py` и пакета входа — остаток keepup-124.

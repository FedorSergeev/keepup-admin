## Why

Долг импорта отдаётся по модулю. Второй — `metrics_retention.py`: он берёт
менеджер в трёх функциях, и ни в одном месте на уровне модуля он ему не нужен.

## What Changes

- `metrics_retention.py`: менеджер берётся в тех функциях, которые им
  пользуются; запись вычеркнута из списка долга.
- Проверки: срок хранения метрик зелёный, список долга — 31 имя.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: модуль, которому база нужна при вызове, не грузит её при
  импорте.

## Impact

- `keepup/metrics_retention.py`, `tests/lazy_database_import_tests.py`.
- Остальные тридцать один модуль — keepup-127.

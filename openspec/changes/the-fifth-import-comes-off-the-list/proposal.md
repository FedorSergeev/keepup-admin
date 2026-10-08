## Why

Долг 127 отдаётся по модулю. Пятый — `positional_sql.py`: он берёт менеджер при
импорте и пользуется им в трёх функциях.

## What Changes

- `positional_sql.py`: менеджер берётся в трёх функциях (возвращающая вставка,
  пакетное выполнение, сырое соединение); импорт верхнего уровня убран; запись
  вычеркнута из списка долга.
- Проверки: `framework_positional_tests.py` и полный набор.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: помощник позиционных параметров не грузит базу при
  импорте.

## Impact

- `keepup/positional_sql.py`, `tests/lazy_database_import_tests.py`.
- Остальные четыре модуля — keepup-127.

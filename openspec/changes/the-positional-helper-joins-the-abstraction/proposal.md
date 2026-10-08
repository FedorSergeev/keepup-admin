## Why

Помощник позиционных параметров существует ровно из-за менеджера: пул принимает
именованные `:name`, а код, написанный для драйвера, — `?`. Это знание о
менеджере, и место ему рядом с ним.

## What Changes

- `positional_sql.py` переезжает в `packages/keepup-db/keepup_db/positional_sql.py`
  и берёт менеджер из своего пакета.
- Старое имя `keepup.positional_sql` — в карте переезда; проверки, которые им
  пользуются, идут через слой совместимости.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: знание о менеджере живёт с менеджером.

## Impact

- `packages/keepup-db/keepup_db/positional_sql.py`, `keepup/compat.py`,
  `tests/compatibility_tests.py`.

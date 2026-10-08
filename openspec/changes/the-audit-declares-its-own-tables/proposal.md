## Why

Аудит владеет двумя таблицами — входящими вызовами и событиями приложения, — и
обе объявлены в модулях базового пакета (`audit.py`, `events.py`). Возможность их
ведёт, значит объявления должны жить у неё.

## What Changes

- `INCOMING_REQUESTS` и `APP_EVENTS` уезжают в
  `packages/keepup-audit/keepup_audit/tables.py`; `keepup.audit` и `keepup.events`
  их реэкспортируют, поэтому старый путь продолжает их создавать, а читатели
  (`builtin/audit.py`, `events_api.py`, `admin_trail.py`, схема) работают.
- Проверка упаковки знает о ссылке на аудит без зависимости.
- Модули-источники чистятся от имён, которые унесли объявления.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: объявления аудита живут у возможности, а старый путь
  получает их реэкспортом на один выпуск.

## Impact

- `packages/keepup-audit/keepup_audit/tables.py`, `keepup/audit.py`,
  `keepup/events.py`, проверка упаковки.
- Вход и панель — следующие переносы keepup-124.

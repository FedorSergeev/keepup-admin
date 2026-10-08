## Why

Язык объявления таблиц — то, ради чего абстракция и существует, — лежал в базовом
пакете и держал в нём SQLAlchemy наравне с менеджером. Менеджер уехал
(keepup-124); язык едет следом.

## What Changes

- `tables.py` переезжает в `packages/keepup-db/keepup_db/tables.py`; пакет
  реэкспортирует его как `keepup_db.tables`.
- Старое имя `keepup.tables` отвечает через слой совместимости.
- Подстановка теперь **перенаправляет и запись**: `monkeypatch.setattr(tables,
  "metadata", …)` обязан дойти до перенесённого модуля, иначе код под проверкой
  пользуется не тем объектом. Это нашлось на проверках языка таблиц.
- `tables.py` вычеркнут из списка модулей, которым база нужна при импорте.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: подстановка уехавшего модуля перенаправляет и чтение, и
  запись.

## Impact

- `packages/keepup-db/keepup_db/{__init__,tables}.py`, `keepup/compat.py`,
  `tests/{compatibility,tables}_tests.py`.
- Снятие `sqlalchemy` с базового пакета — следующий шаг (его ещё держит
  `schema.py` с объявлениями).

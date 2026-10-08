## Why

Вход владеет двумя таблицами — сессиями панели и попытками входа, — и обе
объявлены в модулях базового пакета (`auth/panel_session.py`,
`auth/login_throttle.py`). Возможность их ведёт, значит объявления живут у неё.

## What Changes

- `AUTH_SESSION` и `LOGIN_ATTEMPTS` уезжают в
  `packages/keepup-auth/keepup_auth/tables.py` вместе с константой имени таблицы,
  которой пользуется объявление; оба модуля реэкспортируют объявления, поэтому
  старый путь продолжает их создавать, а читатели работают.
- Проверка упаковки знает о ссылке на вход без зависимости.
- Модули-источники чистятся от имён, которые унесли объявления.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: объявления входа живут у возможности, а старый путь
  получает их реэкспортом на один выпуск.

## Impact

- `packages/keepup-auth/keepup_auth/tables.py`, `keepup/auth/panel_session.py`,
  `keepup/auth/login_throttle.py`, проверка упаковки.
- Панель (темы и каталог разделов) — следующий перенос keepup-124.

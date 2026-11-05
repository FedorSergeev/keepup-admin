## Why

Оболочка зовёт секцию по имени: `window[module.initFunction]`. Каталог говорит,
какую функцию звать, скрипт секции её объявляет — и никто не сверяет одно с
другим. Имя, которого скрипт не объявляет, — не ошибка нигде: секция просто не
инициализируется, её пункт не появляется в меню, и в журнале нет ничего. Так
было у двух секций каркаса: `builtin/users.py` объявлял `initUsers` для скрипта,
который объявляет `renderUsers`, а `keepup-cluster` — `initCluster` вместо
`initClusterModule`. Оба плагина объявляют свои секции после синхронизации
`sections.json` и перезаписывали верные имена, поэтому «Пользователи» и
«Кластер» отсутствовали в меню любого развёртывания.

## What Changes

- `builtin/users.py` называет `renderUsers`; `keepup_cluster/plugin.py` называет
  `initClusterModule` — так, как эти функции объявлены в своих скриптах.
- `integration_logs.js` объявляет `initIntegrationLogs` на верхнем уровне: внутри
  `if` она глобальна не на каждой машине.
- Новая проверка `tests/panel_section_init_tests.py` требует, чтобы каждая
  объявленная секция называла функцию, которую её скрипт действительно
  объявляет, и чтобы скрипт существовал.

## Capabilities

### New Capabilities

### Modified Capabilities

- `panel`: объявленная секция называет функцию, которая есть в её скрипте.

## Impact

- `builtin/users.py`, `packages/keepup-cluster/keepup_cluster/plugin.py`,
  `packages/keepup-http/keepup_http/static/modules/js/integration_logs.js`;
- проверка: `tests/panel_section_init_tests.py`.

## Why

Ядро собирает вклады (keepup-102), но вызвать обработчик умеет только HTTP:
обёртка маршрута живёт в `plugins/routes.py` и знает `Request`, `Depends` и
`HTTPException`. Значит, транспорт, который не HTTP, невозможен — а именно он
доказывает, что конструктор настоящий: сервер, слушающий только gRPC или свой
протокол, и процесс, который не слушает ничего.

Вторая половина той же дыры: маска запроса разбирается при регистрации
маршрутов, то есть внутри HTTP-пути. Любой другой транспорт получил бы маршрут
без проверки параметров — и объявление, называющее параметр, которого нет в
подписи, доехало бы до обработчика.

## What Changes

- `keepup/kernel/call.py`: `RouteSpec` и `Call` — объявление маршрута и один его
  вызов без транспорта; `admit`/`admitted` (маска и подпись), `invoke` (право,
  вызывающий, тело, ответ), `CallError` (отказ, который транспорт переводит на
  свой язык), `route_kind`.
- `plugins/routes.py` — HTTP-адаптер того же вызова: читает запрос, строит
  `Call`, переводит `CallError` в `HTTPException`, пишет аудит. `accepted_params`
  и `admit_params` остаются публичными именами и делегируют в ядро.
- `keepup/kernel/contributions.py`: маска разбирается и сверяется с подписью при
  сборе вкладов, то есть на старте и для всех транспортов сразу; объявление,
  которое рантайм отвергает, останавливает старт.
- `keepup/kernel/transports.py`: `TransportPlugin` (вид `transport` приносит
  сервер, а не маршруты), `transports_of`, `route_specs_of`, `serve_all`.
- `Runtime.transports()`, `Runtime.route_specs()`, `Runtime.serve_all()` и
  `Runtime.run_forever()`: старт, служба, остановка; развёртывание без
  транспорта — воркер, и это сказано в журнале, а не является ошибкой.
- Образцовый транспорт в наборе проверок: две строки по TCP-сокету, ответ через
  маршрут чужого плагина, отказ в своём словаре.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: вызов маршрута не зависит от транспорта; перевозчик —
  плагин вида `transport`, а развёртывание без перевозчика — воркер.

## Impact

- `keepup/kernel/call.py` (новый), `keepup/kernel/transports.py` (новый),
  `keepup/kernel/lifecycle.py`, `keepup/kernel/contributions.py`,
  `keepup/kernel/__init__.py`, `plugins/routes.py`.
- Тесты: `tests/transport_seam_tests.py` (новый).
- Спецификация (`doc/plugin_constructor.md`, раздел 5.1), CHANGELOG каркаса,
  трекер региона keepup — задача 103 и приёмка.
- Сборка приложения через ядро (`create_app` как обёртка над транспортом,
  HTTP отдельным дистрибутивом, профиль `metrics-only` вместо
  `disable_http_server`) — задача keepup-123: она идёт после того, как
  возможности, чьи маршруты сегодня регистрирует `create_app`, станут плагинами.

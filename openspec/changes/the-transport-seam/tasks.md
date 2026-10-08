## 1. Вызов без транспорта

- [x] 1.1 `keepup/kernel/call.py`: `RouteSpec`, `Call`, `admit`, `admitted`,
  `invoke`, `CallError`, `route_kind`. Проверка: `transport_seam_tests.py` —
  маршрут, объявленный одним плагином, отвечает через транспорт другого.
- [x] 1.2 Право проверяется тем, кто вызвал: маршрут с `permission` без
  проверяющего не выполняется. Проверка: `transport_seam_tests.py` —
  `CallError` с кодом `unchecked_permission`.
- [x] 1.3 `raw_request`-маршрут не вызывается нейтрально. Проверка:
  `transport_seam_tests.py` — `CallError` с кодом `not_invocable`.
- [x] 1.4 Маска применяется у любого транспорта. Проверка:
  `transport_seam_tests.py` — значение, не проходящее маску, отвергнуто с
  именем параметра.

## 2. HTTP как адаптер

- [x] 2.1 `plugins/routes.py` строит `Call` и переводит `CallError` в
  `HTTPException`; аудит и форма ответа остаются здесь. Проверка: прежние
  наборы маршрутов (`plugin_runtime_tests.py`, `route_*_tests.py`) зелёные.
- [x] 2.2 `accepted_params` и `admit_params` остаются публичными именами.
  Проверка: `route_parameters_tests.py`, `route_mask_tests.py`.

## 3. Перевозчик

- [x] 3.1 `keepup/kernel/transports.py`: `TransportPlugin`, `transports_of`,
  `route_specs_of`, `serve_all`. Проверка: `transport_seam_tests.py` —
  транспорт вида `transport` найден, `serve` вызван.
- [x] 3.2 `Runtime.run_forever()`: старт, служба, остановка; без транспорта —
  воркер, который ждёт. Проверка: `transport_seam_tests.py` — служба
  заканчивается вместе с транспортом и рантайм останавливается.

## 4. Объявление маршрута проверяется один раз

- [x] 4.1 Маска разбирается и сверяется с подписью при сборе вкладов.
  Проверка: `contributions_tests.py` — плохое объявление останавливает старт;
  `transport_seam_tests.py` — маска работает без HTTP.

## 5. Документы

- [x] 5.1 `tests/transport_seam_tests.py` — набор проверок, включая
  образцовый не-HTTP транспорт.
- [x] 5.2 `doc/plugin_constructor.md` (раздел 5.1 — что именно несёт вызов),
  CHANGELOG каркаса, OpenSpec, трекер региона keepup — задача 103 и приёмка.

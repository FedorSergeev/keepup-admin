## 1. Дистрибутив

- [x] 1.1 `packages/keepup-gateway`: метаданные, точка входа `gateway`,
  зависимости — FastAPI, Starlette с полом keepup-30, httpx; ни одной
  возможности. Проверка: `distribution_layout_tests.py`,
  `.github/scripts/dependency_graph.py` — граф идёт в одну сторону.
- [x] 1.2 Запись в `plugins/builtin.json`, экстра `gateway` (не в `panel`),
  пути набора и покрытия, `release.yml`, `check.sh`. Проверка:
  `release_tests.py`, `packaging_tests.py`, `local_check_tests.py`.

## 2. Таблица и балансировка

- [x] 2.1 `table.py`: разбор и проверка таблицы, самый длинный префикс на
  границе сегмента. Проверка: `gateway_tests.py` — вложенный маршрут
  побеждает, `/orders-archive` не совпадает с `/orders`, девять видов негодной
  записи называют маршрут.
- [x] 2.2 `balancer.py`: обход по кругу, остывание, остывающие адреса
  пробуются последними. Проверка: `gateway_tests.py` — по кругу, упавший
  обходится и виден в отчёте, все остывают — один всё равно пробуется.

## 3. Проксирование

- [x] 3.1 `headers.py`: hop-by-hop и названные `Connection` не проходят,
  `X-Forwarded-*` и `X-Request-Id` добавляются, `X-Keepup-*` клиента срезаются.
  Проверка: `gateway_tests.py`.
- [x] 3.2 `proxy.py`: тело до предела (413 до соединения), повтор только
  безопасный, ответ потоком с повторяющимися заголовками. Проверка:
  `gateway_tests.py` — GET уходит на второй адрес после 503, POST — нет; 502 и
  504; `Set-Cookie` дважды.

## 4. Край и личность

- [x] 4.1 Вход и право через `keepup.kernel.security`, без импорта
  `keepup-auth`; без системы входа — 401. Проверка: `gateway_tests.py`.
- [x] 4.2 `identity.py`: подпись HMAC-SHA256 и `verify_identity()` на
  стандартной библиотеке; секрет из `${ENV}`. Проверка: `gateway_tests.py` —
  изменённые роли, чужой `X-Request-Id`, чужой секрет и старая подпись
  отвергаются.
- [x] 4.3 Отчёт администратора `GET /api/admin/gateway/routes` под правом
  `gateway.admin`. Проверка: `gateway_tests.py`.

## 5. Документы

- [x] 5.1 `doc/gateway.md`, `doc/distributions.md`, `doc/service-catalogue.md`,
  `INSTALL.md`.
- [ ] 5.2 Запись в CHANGELOG — шагом выпуска 0.6.0: проверка бумаг выпуска
  требует, чтобы журнал открывался версией дерева, а дерево до выпуска — 0.5.0.

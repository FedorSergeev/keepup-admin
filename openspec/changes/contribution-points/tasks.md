## 1. Вклады

- [x] 1.1 `keepup/kernel/contributions.py`: закрытый список видов,
  `Contributions`, `collect(runtime)`, `mount(contributions, registrars)`.
  Проверка: `contributions_tests.py` — каждый вид собирается от своего
  плагина, `mount` отдаёт вид его потребителю и считает отданное.
- [x] 1.2 Сбой одного объявления не роняет старт: геттер, который бросил,
  записан в `errors` с плагином и видом. Проверка: `contributions_tests.py` —
  сломанный раздел не мешает маршрутам и разделам других плагинов.
- [x] 1.3 `MiddlewareSpec` и порядок: внешний слой первым, голый класс получает
  порядок по умолчанию. Проверка: `contributions_tests.py` — три вклада
  выстраиваются по порядку, вклад без порядка оказывается последним.
- [x] 1.4 Вид `transport` собирается по виду плагина, а не по геттеру.
  Проверка: `contributions_tests.py`.
- [x] 1.5 Неизвестный вид в `mount` отвергается: список закрыт. Проверка:
  `contributions_tests.py`.

## 2. Ключи маршрута

- [x] 2.1 `response_media_type`: ответ не-JSON; `Response` из обработчика
  проходит как есть. Проверка: `contributions_tests.py` — `text/plain` в
  заголовке и теле, свой `Response` не обёрнут.
- [x] 2.2 `audit: false`: вызова нет в аудите входящих, по умолчанию — есть.
  Проверка: `contributions_tests.py` — буфер пуст у одного маршрута и хранит
  одну запись у другого.
- [x] 2.3 Отказы объявления: `response_media_type` не строка и
  `response_media_type` на `raw_request`-маршруте. Проверка:
  `contributions_tests.py` — `ValueError` при регистрации.

## 3. Фазы сборки

- [x] 3.1 `registry.load_and_initialize(manager, config_path, environ)` —
  загрузка и инициализация без приложения. Проверка:
  `contributions_tests.py` — плагин инициализирован, приложения нет.
- [x] 3.2 `registry.initialize_plugins(app, manager, ...)` вызывает обе фазы.
  Проверка: `contributions_tests.py` — маршрут зарегистрирован.
- [x] 3.3 Объявление, отвергнутое рантаймом маршрутов, останавливает старт.
  Проверка: `contributions_tests.py` — `ValueError` с путём маршрута, при этом
  фаза загрузки проходит.

## 4. Документы

- [x] 4.1 `tests/contributions_tests.py` — набор проверок.
- [x] 4.2 AGENTS.md (ключи маршрута), CHANGELOG каркаса; OpenSpec; трекер
  региона keepup — задача 102 и приёмка.

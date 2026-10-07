## Why

У панели каркаса нет политики Content-Security-Policy — это остаток находки 18
аудита безопасности после 0.2 (keepup-52), заведённый задачей keepup-93. Без
политики любая пропущенная дыра экранирования исполняет чужой скрипт в сессии
администратора: ровно так сработала хранимая XSS через имя пользователя
(keepup-62), и экранирование — единственное, что её остановило. Политику нельзя
было поставить, пока разметка держалась на встроенном коде: в оболочке, обеих
страницах тем и восьми разделах каркаса около сотни атрибутов
`onclick`/`onchange`/`onsubmit`, а в голове каждой темы — встроенный скрипт
узкой раскладки.

## What Changes

- Политика по умолчанию живёт в `keepup/security.py`: `script-src 'self'` без
  `'unsafe-inline'` — встроенный скрипт и атрибут-обработчик отвергаются;
  `style-src 'self' 'unsafe-inline'` (стили приходят из данных, и Tailwind
  строит правила в `<style>` во время работы); `default-src 'self'`,
  `base-uri`, `object-src 'none'`, `frame-ancestors`, `form-action`,
  `connect-src` — только свой источник.
- `KeepupSettings.content_security_policy` заменяет политику целиком
  (константа публична, чтобы её можно было расширить), `None` не отправляет её
  вовсе; `KeepupSettings.csp_report_only` отправляет её как
  `Content-Security-Policy-Report-Only`. Заголовок, выставленный самим ответом,
  не перезаписывается, как и остальные защитные заголовки.
- Оболочка заводит реестр действий `KeepupActions`: раздел регистрирует
  обработчики, разметка называет действие в `data-action` и передаёт значения
  в `data-*`, а на документе висит по одному слушателю на `click`, `change` и
  `submit`. Функция в разметке не называется никогда, и имя, которого никто не
  регистрировал, не делает ничего.
- Атрибуты-обработчики убраны из обеих страниц тем, из `main_new.js` и из
  восьми разделов каркаса (users, modules_management, themes, cluster, metrics,
  event_manager, integration_logs, background_tasks); bootstrap узкой
  раскладки вынесен в `static/js/layout_bootstrap.js`, который грузят обе
  страницы.
- Страницы панели отдаются с `Cache-Control: no-cache`, как и их ассеты:
  страница, закэшированная до обновления, несёт обработчики, которые политика
  отвергает.

## Capabilities

### New Capabilities

- `panel-security`: политика панели, её источники и то, как приложение её
  настраивает и ослабляет на время перехода.

### Modified Capabilities

## Impact

- `keepup/security.py` (новый), `keepup/settings.py`, `keepup/factory.py`,
  `keepup/web.py`.
- `keepup/static/index_new.html`, `index_nebula.html`, `static/js/main_new.js`,
  `static/js/layout_bootstrap.js` (новый), восемь разделов
  `static/modules/js/`.
- Тесты: `content_security_policy_tests.py`, `panel_inline_handlers_tests.py`,
  `panel_actions_tests.py`, `panel_escaping_tests.py`, `panel_shell_tests.py`.
- CHANGELOG каркаса, AGENTS.md.

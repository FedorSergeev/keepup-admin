## 1. Политика

- [x] 1.1 `keepup/security.py`: `DEFAULT_CONTENT_SECURITY_POLICY` с
  `script-src 'self'`, `style-src 'self' 'unsafe-inline'`, `default-src 'self'`,
  `base-uri`, `object-src 'none'`, `frame-ancestors`, `form-action`,
  `connect-src`. Проверка: `content_security_policy_tests.py` — в
  `script-src` нет `'unsafe-inline'`, политика несёт каждый объявленный
  источник.
- [x] 1.2 `KeepupSettings.content_security_policy` и `csp_report_only`;
  посредник защитных заголовков отправляет политику или отчёт, а заголовок,
  выставленный ответом, оставляет как есть. Проверка: свои настройки,
  Report-Only, `None`, выключенные заголовки и собственный заголовок ответа.

## 2. Разметка без встроенного кода

- [x] 2.1 Реестр `KeepupActions` в оболочке: `register`, `data-action`,
  один слушатель на документе для click, change и submit; действие на форме
  отвечает на submit, на поле — на change, а не на клик по кнопке внутри.
  Проверка: `panel_actions_tests.py` под node.
- [x] 2.2 Обе страницы тем и `main_new.js` без атрибутов-обработчиков;
  bootstrap узкой раскладки — файл `static/js/layout_bootstrap.js`.
  Проверка: `panel_inline_handlers_tests.py` (нет обработчиков и нет встроенного
  `<script>`).
- [x] 2.3 Восемь разделов каркаса переведены на реестр, значения — в `data-*`
  (идентификатор, страница, имя), `this` сохранён (плагины вызываются через
  `window.<plugin>`). Проверка: тот же тест «каждое действие в разметке
  зарегистрировано»; `panel_escaping_tests.py` — имя пользователя не становится
  обработчиком.
- [x] 2.4 Страницы панели отдаются с `Cache-Control: no-cache`, как их ассеты.
  Проверка: `content_security_policy_tests.py` — заголовок на `/selfcare`.

## 3. Документы

- [x] 3.1 Тесты в наборе keepup: `content_security_policy_tests.py`,
  `panel_inline_handlers_tests.py`, `panel_actions_tests.py`,
  `panel_escaping_tests.py` (обновлён), `panel_shell_tests.py`.
- [x] 3.2 CHANGELOG каркаса (Security и Fixed), AGENTS.md (настройки и правило
  раздела).

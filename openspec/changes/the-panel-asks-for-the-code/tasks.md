## 1. Вход

- [x] 1.1 `mfa_panel.js`: челлендж, поле кода, recovery-код, возврат к паролю.
  Проверка: `mfa_panel_tests.py`.
- [x] 1.2 `main_new.js`: `mfa_required` в `login()`, общий `finishSignIn`,
  проверка кода той же формой, установка экрана. Проверка:
  `mfa_panel_tests.py`.

## 2. Управление фактором

- [x] 2.1 Экран: статус, `start`, `confirm`, recovery-коды один раз, `disable`.
  Проверка: `mfa_panel_tests.py`.

## 3. Страницы и CSP

- [x] 3.1 `index_new.html` и `index_nebula.html` загружают `mfa_panel.js` до
  оболочки. Проверка: `mfa_panel_tests.py`.
- [x] 3.2 Ни inline-обработчиков, ни разметки со значениями.
  Проверка: `panel_inline_handlers_tests.py`, `panel_escaping_tests.py`.

## 4. Набор

- [x] 4.1 `mfa_panel_tests.py`, `panel_*_tests.py`, `mfa_tests.py` зелёные.

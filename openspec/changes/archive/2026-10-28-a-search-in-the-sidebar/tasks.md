## 1. Поле в меню

- [x] 1.1 Разметка в `index_new.html` и `index_nebula.html`: `#nav-search` над
  `#modules-nav-container`, поле с `aria-label`, `aria-controls`, кнопка
  очистки и строка статуса. Без inline-обработчиков (CSP).
  Проверка: `panel_search_tests.py`.
- [x] 1.2 `main_new.js`: область поиска с маркерами, `applyModuleSearch`,
  `clearModuleSearch`, `setupModuleSearch`, вызов из `initializeApp`; фильтр
  снимается при свёртывании меню. Проверка: `panel_search_tests.py`.
- [x] 1.3 `main_new.css`: поле, очистка, строка статуса, классы
  `nav-search-hidden`, `nav-search-match`, `nav-search-nested`; токены
  `--nav-search-*`, `--nav-item-search-base`, `--nav-item-search-step`.
  `main_nebula.css` переопределяет только значения токенов.
  Проверка: `panel_search_tests.py`.

## 2. Что считается совпадением и подмодулем

- [x] 2.1 Подстрока по видимому тексту пункта, без регистра и краевых
  пробелов; иконки и `description` не участвуют. Проверка:
  `panel_search_tests.py`.
- [x] 2.2 Родитель совпадения остаётся путём; подмодуль без родителя —
  с отступом по глубине (вложенный пункт или `data-nav-children`) и точкой.
  Проверка: `panel_search_tests.py`.
- [x] 2.3 Пункты, добавленные разделами после начала поиска, попадают под
  фильтр; пустой результат объясняется строкой и кнопкой очистки. Проверка:
  `panel_search_tests.py`.

## 3. Набор

- [x] 3.1 `panel_search_tests.py` (node, подставной DOM), `panel_shell_tests.py`,
  `content_security_policy_tests.py`, `header_badge_tests.py`,
  `mfa_panel_tests.py`, `stay_signed_in_tests.py` зелёные.

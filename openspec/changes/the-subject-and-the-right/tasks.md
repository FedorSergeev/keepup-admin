## 1. Форма и имена

- [x] 1.1 `keepup/kernel/security.py`: `AccessRequest`, `Identity`, имена служб
  `auth` и `permissions`. Проверка: `subject_contract_tests.py` — форма ядра и
  та, что публикует вход, — один и тот же класс.
- [x] 1.2 `kernel/call.py` строит вопрос о праве из формы ядра. Проверка:
  `subject_contract_tests.py` — вопрос несёт право, метод, путь и параметры
  пути.

## 2. Шов вместо импорта

- [x] 2.1 `plugins/routes.py` не импортирует ни субъекта, ни право.
  Проверка: `kernel_purity_tests.py`.
- [x] 2.2 Зависимость входа и проверяющий берутся у развёртывания; пустая
  служба — 401 на подписанном маршруте. Проверка: `subject_contract_tests.py`.
- [x] 2.3 `create_app` кладёт за службы собственный вход каркаса. Проверка:
  прежние наборы про вход и права зелёные (`identity_*_tests.py`,
  `route_authorisation_tests.py`).

## 3. Сторож

- [x] 3.1 `tests/kernel_purity_tests.py`: список модулей, которым ещё можно
  импортировать вход, с причиной; новый модуль — отказ, исчезнувший — тоже.
  Проверка: сам сторож.
- [x] 3.2 `kernel/**` не импортирует вход ни в одном модуле. Проверка: сторож.

## 4. Документы

- [x] 4.1 `tests/subject_contract_tests.py`, `tests/kernel_purity_tests.py`.
- [x] 4.2 CHANGELOG каркаса, OpenSpec, трекер региона keepup — задача 119 и
  приёмка.

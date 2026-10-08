## Why

Одиннадцать модулей каркаса — metrics_api, modules, locks, scheduler, cluster,
events_api, themes, web, api_docs, plugins/admin и plugins/routes — берут
текущего администратора из `keepup.auth.dependencies.get_current_admin`. Это и
есть настоящая авторизация каркаса, и пока субъект запроса — приватный импорт,
ни одна возможность не вынимается: вынос входа, пользователей или аудита рвёт
сразу все одиннадцать, а метрики, блокировки, планировщик, кластер и темы
оказываются в зависимости от входа, которого в их предмете нет.

Форма вопроса о праве при этом лежит в модуле входа
(`auth/identity/contract.py`), то есть ядро не владеет даже формой — а должно:
имя и форма службы принадлежат ядру, реализация — возможности.

## What Changes

- `keepup/kernel/security.py`: `AccessRequest` (форма вопроса), `Identity`
  (что развёртывание положило за службы), имена служб `auth` и `permissions`,
  доступ к ним.
- `AccessRequest` переезжает в ядро; `keepup.auth.identity.contract` его
  реэкспортирует, поэтому приложения и существующие проверки импортируют его
  оттуда же, где и раньше.
- `keepup/kernel/call.py` строит вопрос из формы ядра, а не из модуля входа.
- `plugins/routes.py` перестаёт импортировать вход: зависимость входа и
  проверяющий права берутся у развёртывания (`security.subject_dependency()` и
  `security.checker()`), а при пустой службе подписанный маршрут отвечает 401,
  а не падает на импорте.
- `create_app` (`factory.apply_settings`) кладёт за службы собственный вход
  каркаса — до тех пор, пока `keepup-auth` не станет плагином и не сделает это
  своим `register()`.
- Сторож `tests/kernel_purity_tests.py`: поимённый список модулей, которым ещё
  можно импортировать вход, с причиной у каждого; список может только
  сокращаться, `kernel/**` не импортирует вход вовсе.

## Capabilities

### New Capabilities

### Modified Capabilities

- `plugin-constructor`: субъект запроса и право — контракт ядра (службы `auth`
  и `permissions`), а не импорт модуля входа; форма вопроса о праве принадлежит
  ядру.

## Impact

- `keepup/kernel/security.py` (новый), `keepup/kernel/call.py`,
  `keepup/kernel/__init__.py`, `auth/identity/contract.py`, `plugins/routes.py`,
  `factory.py`, `tests/conftest.py`.
- Тесты: `tests/kernel_purity_tests.py`, `tests/subject_contract_tests.py`
  (новые).
- `doc/service-catalogue.md` уже называет обе службы; CHANGELOG каркаса,
  OpenSpec, трекер региона keepup — задача 119 и приёмка.

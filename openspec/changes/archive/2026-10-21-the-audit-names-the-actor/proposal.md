## Why

Строка входящего аудита не могла сказать, кто позвал: актор лежал внутри
`request_data`, а редакция заменяет там каждое значение — в колонке оказывалось
`<hidden>`. Административные маршруты самого каркаса (профиль, смена пароля,
выдача ролей, правка пользователя, команда реплике) не писались вовсе, и отказ
во входе не оставлял ни строки в журнале. Разбор инцидента начинался с «кто-то
что-то сделал» (keepup-172).

## What Changes

- `incoming_requests` получает колонки `user_id` и `username`. Транспорт
  передаёт актора отдельно от тела (`start_call(..., actor=...)`), поэтому
  редакция тела его не скрывает.
- Декоратор `audited(action, method, actor)` в ядре проводит framework-маршрут
  через тот же шов, что и маршрут плагина: профиль, пароль, роли, правка
  пользователя и команда остановки/перезапуска реплики.
- Отказ во входе пишется в журнал с именем и адресом (никогда с паролем), а
  `login_attempts` получает `last_address`, чтобы попытка была читаема после
  инцидента.
- Отказ маршрута сохраняет свой текст и статус в аудите, как и раньше.

## Capabilities

### Modified Capabilities

- `audit`: строка называет актора, а административное действие каркаса — это
  строка.

## Impact

- `kernel/observability.py` — `audited`, `actor` в `start_call`.
- `packages/keepup-audit/keepup_audit/audit.py`, `tables.py` — колонки и
  передача актора.
- `packages/keepup-http/keepup_http/routes.py` — актор вне тела.
- `packages/keepup-auth/keepup_auth/user_routes.py`, `routes.py`,
  `login_throttle.py`, `tables.py`; `packages/keepup-cluster/.../cluster.py`.
- `tests/audit_actor_tests.py`.

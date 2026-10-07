## 1. Имена, привязанные к хосту

- [x] 1.1 `HOST_PREFIX`, `cookie_names(secure)` и `session_token_in(cookies)`
  опубликованы в `keepup.auth.panel_session`; `SESSION_COOKIE` и `CSRF_COOKIE`
  остаются простыми именами для HTTP. Проверка: `public_interface_tests`
  проходит на новом `__all__`.
- [x] 1.2 По HTTPS ставятся `__Host-ss_session`/`__Host-ss_csrf` (Secure,
  Path=/, без Domain), старые имена гасятся; по HTTP имена прежние.
  Проверка: `host_prefix_cookies_tests.py` — обе схемы, атрибуты Set-Cookie.
- [x] 1.3 Чтение принимает оба имени, префиксное побеждает. Проверка: сессия
  прежнего выпуска проходит; подложенное простое имя не выигрывает у
  префиксного и не открывает доступ само по себе.
- [x] 1.4 `CsrfCookieRefresh` и выход работают с именами своей схемы; на HTTPS
  метка переезжает под префиксное имя. Проверка: чтение кладёт
  `__Host-ss_csrf`; выход гасит все четыре имени.
- [x] 1.5 Панельный скрипт читает метку из обоих имён. Проверка: node-проба
  оболочки — с двумя cookie уходит префиксное значение, с одной старой —
  старое.

## 2. Документы

- [x] 2.1 `keepup/tests/host_prefix_cookies_tests.py` в наборе; прежние
  `csrf_binding_tests`, `session_renewal_tests`, `websocket_sign_in_tests`,
  `oidc_flow_tests` проходят.
- [x] 2.2 CHANGELOG каркаса (Security и «Upgrading from 0.2.0»), OpenSpec,
  трекер региона keepup — задача 92 и приёмка.

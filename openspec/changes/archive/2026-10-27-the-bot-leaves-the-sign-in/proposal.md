## Why

Панель каркаса общая для всех приложений, а экран входа знал о телеграм-боте
одного развёртывания: отказ в самостоятельной регистрации называл бота, строка
«забыли пароль» предлагала отправить ему `/password`, а скрипт оболочки до
keepup-27 сам ходил за адресом бота. Бот — отдельная сущность и отдельный
плагин приложения, а не часть входа: у одного развёртывания его нет вовсе, и
ссылка на него там — тупик (keepup-178).

## What Changes

- Отказ в самостоятельной регистрации: «Ask an administrator for an invitation
  link…». Ни мессенджера, ни команды.
- Строка «Forgot your password?» скрыта по умолчанию и показывается только
  тогда, когда приложение назвало `password_reset_url` в своём публичном
  конфиге; текст и ссылка не называют бота.
- Комментарии оболочки и подсказки больше не описывают чужого бота.
- Новый `tests/sign_in_screen_tests.py` держит страницы, оболочку и маршрут
  регистрации.

## Capabilities

### New Capabilities

### Modified Capabilities

- `auth`: экран входа и отказы входа не знают о телеграм-боте.

## Impact

- `packages/keepup-auth/keepup_auth/routes.py`.
- `packages/keepup-http/keepup_http/static/index_new.html`,
  `static/index_nebula.html`, `static/js/main_new.js`.
- `tests/sign_in_screen_tests.py`.

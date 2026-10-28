## 1. Сообщения

- [x] 1.1 Отказ `POST /api/auth/register` называет администратора и приглашение,
  без мессенджера и команды. Проверка: `sign_in_screen_tests.py`.
- [x] 1.2 Комментарии оболочки и подсказка о сбросе пароля не называют бота.
  Проверка: `sign_in_screen_tests.py`.

## 2. Экран

- [x] 2.1 Обе страницы: строка «Forgot your password?» скрыта, пока приложение
  не назвало `password_reset_url`. Проверка: `sign_in_screen_tests.py`.
- [x] 2.2 Ссылка ведёт на адрес приложения и показывается вместе со строкой.
  Проверка: `sign_in_screen_tests.py`.

## 3. Набор

- [x] 3.1 `sign_in_screen_tests.py`, `application_seams_tests.py`,
  `panel_shell_tests.py` зелёные.

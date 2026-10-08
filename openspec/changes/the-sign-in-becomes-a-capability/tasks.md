## 1. Возможность

- [x] 1.1 `builtin/auth.py`: дескриптор, две службы, два вклада-таблицы.
  Проверка: `auth_plugin_tests.py`.
- [x] 1.2 Возможность владеет `auth_session` и `login_attempts`. Проверка:
  `auth_plugin_tests.py` — таблицы создаются службой.
- [x] 1.3 Рантайм принимает личность возможности. Проверка:
  `auth_plugin_tests.py` — шов отвечает её именем.
- [x] 1.4 Без хранилища вход не запускается и говорит почему. Проверка:
  `auth_plugin_tests.py`.

## 2. Документы

- [x] 2.1 `tests/auth_plugin_tests.py`.
- [x] 2.2 CHANGELOG каркаса, OpenSpec, трекер региона keepup — задача 116.

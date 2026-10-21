## 1. Отказ

- [x] 1.1 `table.traverses`: сегмент `.`/`..`, двойное кодирование, обратная
  косая черта. Проверка: `gateway_tests.py`.
- [x] 1.2 `proxy._handle`: отказ 400 до выбора маршрута и перед построением
  адреса службы. Проверка: `gateway_tests.py`.
- [x] 1.3 `sockets.SocketProxy.handle`: закрытие 1008 до выбора маршрута.
  Проверка: `gateway_socket_tests.py`.

## 2. Набор

- [x] 2.1 `gateway_tests.py` и `gateway_socket_tests.py` зелёные.

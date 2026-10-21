# integration-logs Specification

## Purpose
Журнал вызовов, которые приложение делает наружу, и кто вправе его читать.

## Requirements

### Requirement: Журнал интеграций принадлежит администратору

Все маршруты журнала — `GET /api/integration-logs`,
`GET /api/integration-logs/stats`, `GET /api/integration-logs/{log_id}` и
`POST /api/integration-logs/cleanup` — SHALL объявлять право
`integration_logs.admin`. Ядро SHALL проверять право до вызова обработчика.
Раздел панели SHALL быть помечен `adminOnly`.

#### Scenario: Клиент не читает журнал

- **WHEN** пользователь с ролью CLIENT вызывает любой из четырёх маршрутов
- **THEN** ядро отвечает 403, а обработчик не вызывается

#### Scenario: Анонимный не читает журнал

- **WHEN** маршрут вызывают без сессии
- **THEN** ответ — 401

#### Scenario: Администратор работает как прежде

- **WHEN** администратор вызывает список, статистику, одну запись или очистку
- **THEN** он получает те же ответы, что и до изменения

#### Scenario: Раздел скрыт от клиента

- **WHEN** клиент открывает панель
- **THEN** раздела «Integration log» в навигации нет

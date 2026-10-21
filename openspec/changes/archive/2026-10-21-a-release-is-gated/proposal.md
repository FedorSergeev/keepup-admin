## Why

Тег публиковал всё, что на нём стоит: релизный workflow не зависел от набора
тестов, ставил `build` и `twine` без версий, звал `pypa/gh-action-pypi-publish`
с ветки `release/v1` и не оставлял ни SBOM, ни политики раскрытия. У
Mythos-класса между «нашёл» и «воспользовался» часы, а между «тег» и
«опубликовано» не было ничего (keepup-175).

## What Changes

- Job `gate` прогоняет набор тестов до публикации; `capabilities` и `base`
  зависят от него. `concurrency: release` не даёт двум тегам гонять одни имена.
- `build==1.6.1`, `twine==7.0.0`; действия закреплены SHA, а не тегом или
  веткой.
- Job `sbom` собирает CycloneDX из окружения, в котором ставились пакеты, и
  прикладывает файл артефактом.
- `SECURITY.md`: приватный канал GitHub advisories, поддерживаемые версии,
  сроки 2/5/7/30/90 дней, что вне области.
- Compose приложения: порт только на loopback, `read_only`, `cap_drop: [ALL]`,
  `no-new-privileges`, лимиты pid и памяти, ротация журналов, том для журналов.

## Capabilities

### New Capabilities

- `release`: что должно быть правдой до того, как версия станет публичной.

## Impact

- `.github/workflows/release.yml`, `SECURITY.md`,
  `ceasar.keepupproject.ru/docker-compose.yml`.
- `tests/release_policy_tests.py`.

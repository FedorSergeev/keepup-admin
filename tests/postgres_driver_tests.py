"""A fresh installation reaches PostgreSQL through the driver it installed (keepup-84).

The connection address was a bare ``postgresql://``, which SQLAlchemy resolves to
its default driver -- psycopg2 up to 2.0, psycopg 3 from 2.1. The package declares
psycopg2 and allows any SQLAlchemy 2.x, so every fresh installation took 2.1 and
failed on the first query with "No module named 'psycopg'". Found by the load
stand (keepup-53), whose image is exactly such an installation.

    python3 -m pytest keepup/tests/postgres_driver_tests.py -v
"""

from sqlalchemy.engine import make_url

from keepup.db import DatabaseConfig


def postgres_config(monkeypatch):
    for name, value in {"DB_TYPE": "postgres", "DB_HOST": "db.invalid", "DB_PORT": "5432",
                        "DB_NAME": "keepup", "DB_USER": "keepup", "DB_PASSWORD": "x"}.items():
        monkeypatch.setenv(name, value)
    return DatabaseConfig()


def test_the_address_names_the_declared_driver(monkeypatch):
    url = make_url(postgres_config(monkeypatch).get_connection_string())
    assert url.drivername == "postgresql+psycopg2"


def test_whatever_sqlalchemy_is_installed_the_driver_is_psycopg2(monkeypatch):
    dialect = make_url(postgres_config(monkeypatch).get_connection_string()).get_dialect()
    assert dialect.driver == "psycopg2"

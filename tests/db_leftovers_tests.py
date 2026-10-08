"""What is left of the removed manager in the database module (keepup-55).

The configuration object was built twice -- the second instance replacing the
first at import -- and the pooled manager still carried a method that rewrote
positional parameters into named ones, a leftover of the transition.

    python3 -m pytest keepup/tests/db_leftovers_tests.py -v
"""

import ast
import importlib
import warnings
from pathlib import Path

from keepup import db

DB = Path(importlib.import_module(db.DatabaseManagerV2.__module__).__file__)


def test_the_configuration_is_built_once():
    tree = ast.parse(DB.read_text(encoding="utf-8"))
    built = [node for node in tree.body
             if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "db_config" for t in node.targets)]
    assert len(built) == 1


def test_the_positional_method_warns_that_it_goes(monkeypatch):
    class Session:
        def execute(self, statement, params):
            Session.seen = (str(statement), params)
            return type("Result", (), {"rowcount": 1})()

    class Opened:
        def __enter__(self):
            return Session()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(db.DatabaseManagerV2, "get_session", classmethod(lambda cls: Opened()))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert db.DatabaseManagerV2.execute_commit_with_positional(
            "UPDATE t SET a = ? WHERE b = ?", (1, 2)) == 1
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    # It still works while it is here.
    assert Session.seen == ("UPDATE t SET a = :param0 WHERE b = :param1",
                            {"param0": 1, "param1": 2})

"""The guide for coding agents says what the code is (keepup-39).

AGENTS.md is read by an agent that will then write code against the names it
finds there. A guide that names a module, a setting or a route key the framework
no longer has sends that agent to write something that fails later and less
clearly. So every such name is checked against the code here.
"""

import dataclasses
import importlib
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
GUIDE = PACKAGE / "AGENTS.md"
#: Long enough to hold the rules, short enough to be read whole.
MAX_LINES = 300


def guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_every_keepup_module_the_guide_names_exists():
    named = sorted(set(re.findall(r"`(keepup(?:\.[a-z_]+)+)`", guide())))
    assert named, "the guide names no module at all"
    missing = []
    for name in named:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            missing.append(name)
    assert not missing, f"AGENTS.md names modules keepup does not have: {missing}"


def test_every_setting_the_guide_names_is_a_field_of_keepup_settings():
    from keepup.settings import KeepupSettings

    text = guide()
    section = text[text.index("Settings worth knowing"):text.index("## Plugins")]
    named = set(re.findall(r"`([a-z_]+)`", section))
    fields = {f.name for f in dataclasses.fields(KeepupSettings)}
    assert named and not (named - fields), f"not settings: {sorted(named - fields)}"


def test_every_route_key_the_guide_names_is_one_the_runtime_reads():
    from keepup.plugins import route_mask

    text = guide()
    line = text[text.index("**Route keys**"):text.index("- **Handlers take plain")]
    keys = set(re.findall(r"`([a-z_]+)`", line))
    runtime = (PACKAGE / "plugins" / "routes.py").read_text(encoding="utf-8")
    read = set(re.findall(r"route\.get\('([a-z_]+)'", runtime)) | {"path", "methods", "handler"}
    read |= {route_mask.MASK_FIELD} | set(route_mask.DECLARATION_FIELDS)
    assert not (keys - read), f"route keys the runtime does not read: {sorted(keys - read)}"


def test_the_names_it_shows_in_code_exist():
    from keepup import locks, tables
    from keepup.plugins.base import BasePlugin

    for name in ("table", "auto_id", "ensure_tables", "ensure_columns", "foreign_key",
                 "per_dialect", "NOW"):
        assert hasattr(tables, name), f"keepup.tables has no {name}"
    for name in ("distributed_lock", "with_distributed_lock"):
        assert hasattr(locks, name), f"keepup.locks has no {name}"
    for name in ("initialize", "get_api_routes", "get_handlers", "get_websocket_routes",
                 "post_construct"):
        assert hasattr(BasePlugin, name), f"BasePlugin has no {name}"


def test_it_is_short_enough_to_be_read_whole():
    assert len(guide().splitlines()) <= MAX_LINES


def test_claude_md_points_at_the_guide_instead_of_copying_it():
    pointer = (PACKAGE / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in pointer
    assert len(pointer.splitlines()) < 10, "one set of rules, not two"


def test_the_guide_does_not_ship_in_the_package():
    """It is for the repository and a fork, not for an installed package."""
    pyproject = (PACKAGE / "pyproject.toml").read_text(encoding="utf-8")
    data = pyproject[pyproject.index("[tool.setuptools.package-data]"):]
    data = data[:data.index("\n[", 1)]
    assert "AGENTS.md" not in data and "CLAUDE.md" not in data and "*.md" not in data

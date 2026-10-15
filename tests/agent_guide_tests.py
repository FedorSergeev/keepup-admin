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


def test_every_keepup_name_the_guide_names_exists():
    """A backticked `keepup.x.y()` is a claim about a module and an attribute.

    The name is read from the backticks, the module is the longest prefix that
    imports, and the rest is walked as attributes -- which is how a call like
    `keepup.kernel.create_runtime(settings)` is checked rather than skipped
    (keepup-159).
    """
    named = sorted({match.group(1) + match.group(2)
                    for match in re.finditer(r"`(keepup(?:\.[a-z_]+)+)([.\w()\[\]]*)`", guide())})
    assert named, "the guide names no keepup module at all"
    missing = []
    for name in named:
        module_name = name
        tail = ""
        while module_name:
            try:
                module = importlib.import_module(module_name)
                break
            except ModuleNotFoundError:
                module_name, _, dropped = module_name.rpartition(".")
                tail = f".{dropped}{tail}" if dropped else tail
        if not module_name:
            missing.append(f"{name} -- no module")
            continue
        # A name shown as a call carries its arguments in the match: the
        # attributes are what comes before the first bracket.
        for attribute in [part for part in tail.split("(")[0].lstrip(".").split(".") if part]:
            if not hasattr(module, attribute):
                missing.append(f"{name} -- {module_name} has no {attribute}")
                break
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
    # The route runtime, and the body limit it hands each route's declaration to.
    runtime = "\n".join((PACKAGE / name).read_text(encoding="utf-8")
                        for name in ("packages/keepup-http/keepup_http/routes.py",
                                     "packages/keepup-http/keepup_http/body_limit.py"))
    read = set(re.findall(r"route\.get\('([a-z_]+)'", runtime)) | {"path", "methods", "handler"}
    read |= {route_mask.MASK_FIELD} | set(route_mask.DECLARATION_FIELDS)
    assert not (keys - read), f"route keys the runtime does not read: {sorted(keys - read)}"


def test_every_import_in_its_examples_resolves():
    """A snippet an agent copies is a claim about the package.

    The check above reads the module names the prose puts in backticks, and that
    is how `from keepup import tables` survived: it sits in a code block, and
    `keepup` itself does import. This one reads the examples and asks for the
    module *and* every name it takes from it (keepup-159).
    """
    bad = []
    for statement in example_imports():
        match = re.match(r"from ([\w.]+) import (.+)", statement)
        if match:
            module_name, names = match.group(1), match.group(2)
            if not module_name.startswith("keepup"):
                continue
            try:
                module = importlib.import_module(module_name)
            except ModuleNotFoundError:
                bad.append(f"{statement} -- no module {module_name}")
                continue
            for name in re.findall(r"[A-Za-z_][\w.]*", names):
                if not hasattr(module, name.split(".")[0]):
                    bad.append(f"{statement} -- {module_name} has no {name}")
            continue
        match = re.match(r"import ([\w.]+)", statement)
        if match and match.group(1).startswith("keepup"):
            try:
                importlib.import_module(match.group(1))
            except ModuleNotFoundError:
                bad.append(f"{statement} -- no module {match.group(1)}")
    assert bad == [], f"AGENTS.md shows imports that do not work: {bad}"


def test_every_path_it_names_is_a_file():
    """`keepup/settings.py` is not a path: the repository root *is* the package.

    A module of a capability is named from its distribution (`keepup_auth/...`),
    which is where a reader inside that distribution sees it.
    """
    bad = []
    for named in named_paths():
        if named.startswith("keepup/"):
            bad.append(f"{named} -- there is no keepup/ directory; "
                       "name the module (`keepup.settings`) or the file (`settings.py`)")
            continue
        candidates = [PACKAGE / named]
        candidates += [path / named for path in sorted((PACKAGE / "packages").glob("*"))]
        if not any(candidate.is_file() for candidate in candidates):
            bad.append(f"{named} -- no such file")
    assert bad == [], f"AGENTS.md names paths that do not exist: {bad}"


def example_imports():
    """Every import statement of the guide's fenced code blocks."""
    statements = []
    for block in re.findall(r"```(?:python|bash)?\n(.*?)```", guide(), re.S):
        for line in block.splitlines():
            line = line.strip()
            if line.startswith(("from ", "import ")) and "=" not in line.split("import")[0]:
                statements.append(line)
    return statements


#: Paths the guide writes as an *application's* files, not this package's.
AN_APPLICATIONS = {"config/modules.json", "config/auth.yaml"}


def named_paths():
    """Every file path the guide puts in backticks, this package's own."""
    paths = set()
    for named in re.findall(r"`([\w./-]+\.(?:py|json|md|sh|toml|html))`", guide()):
        if named.startswith((".", "/")):
            continue
        if named in AN_APPLICATIONS:
            continue
        paths.add(named)
    return sorted(paths)


def test_the_names_it_shows_in_code_exist():
    from keepup_db import tables
    from keepup import locks
    from keepup.plugins.base import BasePlugin

    for name in ("table", "auto_id", "ensure_tables", "ensure_columns", "foreign_key",
                 "per_dialect", "NOW"):
        assert hasattr(tables, name), f"keepup_db.tables has no {name}"
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

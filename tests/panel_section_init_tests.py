"""Every declared section names a function its script really has.

A panel section is drawn by the shell: the catalogue says which script to load
and which function to call afterwards, and the shell calls it by name --
`window[module.initFunction]`. A name that no script declares is not an error
anywhere: the section simply never initialises, its entry never appears in the
sidebar, and nothing in the log says why. Two of them were wrong for a long
time -- the accounts section said `initUsers` for a script that declares
`renderUsers`, and the cluster said `initCluster` for `initClusterModule` --
and the two entries were missing from the menu of every deployment.

    python -m pytest tests/panel_section_init_tests.py -v
"""

import json
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
SHELL_STATIC = PACKAGE / "packages" / "keepup-http" / "keepup_http" / "static"
SECTIONS_JSON = PACKAGE / "sections.json"

#: Declared sections the framework ships no script for. The section catalogue
#: of the panel moved to `modules_management` (`modules.js`), and this entry is
#: what was left of the old name: nothing grants it and no script draws it.
#: Named, so that a *new* section without a script fails this test instead of
#: joining a silence.
DECLARED_WITHOUT_A_SCRIPT = {
    "panel_sections": "the panel's own section catalogue is `modules_management`",
}

#: A function a classic script makes global: a top-level declaration, or the
#: explicit assignment to `window`.
DECLARATION = "^(?:async\\s+)?function\\s+{name}\\s*\\(|^window\\.{name}\\s*="


def script_of(declaration: dict) -> Path:
    """The script a section names, resolved inside the shell's own files."""
    path = str(declaration.get("js") or "")
    marker = "/keepup-static/"
    if marker not in path:
        return SHELL_STATIC / "__not_a_shell_path__"
    return SHELL_STATIC / path.split(marker, 1)[1]


def declares(path: Path, name: str) -> bool:
    if not path.is_file() or not name:
        return False
    source = path.read_text(encoding="utf-8", errors="ignore")
    return re.search(DECLARATION.format(name=re.escape(name)), source, re.MULTILINE) is not None


def plugin_declarations():
    """Every section the framework's own capabilities declare from Python."""
    from builtin.ui import UiPlugin
    from builtin.users import UsersPlugin
    from keepup_cluster.plugin import ClusterPlugin

    for plugin_class in (UsersPlugin, ClusterPlugin, UiPlugin):
        plugin = plugin_class(None)
        for declaration in plugin.get_panel_sections() or []:
            yield plugin.id if hasattr(plugin, "id") else plugin_class.__name__, declaration


def framework_declarations():
    declared = json.loads(SECTIONS_JSON.read_text(encoding="utf-8"))
    for declaration in declared.get("modules", []):
        yield "sections.json", declaration


ALL_DECLARATIONS = list(framework_declarations()) + list(plugin_declarations())


@pytest.mark.parametrize("declared_by,declaration", ALL_DECLARATIONS,
                         ids=[f"{by}:{d.get('id')}" for by, d in ALL_DECLARATIONS])
def test_the_section_names_a_function_its_script_declares(declared_by, declaration):
    section_id = declaration.get("id")
    if section_id in DECLARED_WITHOUT_A_SCRIPT:
        pytest.skip(DECLARED_WITHOUT_A_SCRIPT[section_id])

    path = script_of(declaration)
    assert path.is_file(), (
        f"{declared_by} declares the section {section_id!r} with a script the "
        f"package does not ship: {declaration.get('js')}")
    assert declares(path, declaration.get("initFunction")), (
        f"{declared_by} declares the section {section_id!r} as initialised by "
        f"{declaration.get('initFunction')!r}, and {path.name} declares no such "
        f"function: the section would never initialise and its entry would never "
        f"appear in the menu.")


def test_the_two_sections_that_were_wrong_are_named_as_their_scripts_name_them():
    """The regression itself: the accounts and the cluster, by name."""
    by_id = {declaration["id"]: declaration for _, declaration in ALL_DECLARATIONS}
    assert by_id["users"]["initFunction"] == "renderUsers"
    assert by_id["cluster"]["initFunction"] == "initClusterModule"
    assert declares(SHELL_STATIC / "modules" / "js" / "users.js", "renderUsers")
    assert declares(SHELL_STATIC / "modules" / "js" / "cluster.js", "initClusterModule")

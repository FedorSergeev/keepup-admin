"""What a package says is public, and what the applications actually take.

Task keepup-7. A package that is installed by strangers has to say which names
they may rely on: without that, every name is public, every rename is a
breaking change, and a version number stops meaning anything.

Before this task the root of the package declared four names while the
applications in this repository imported from thirty modules -- including two
private functions of the authentication layer, taken by their underscore names.

The check here is the declaration made enforceable: every name an application
imports from the framework must be declared public by the module it comes from.
A name outside the list fails the run and forces the decision -- publish it, or
stop taking it.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/public_interface_tests.py -v
"""

import ast
import importlib
from collections import defaultdict
from pathlib import Path

import pytest

from keepup.tests.repository import applications

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "keepup"


def imports_of_the_framework():
    """Every (module, name) an application takes out of keepup.

    Returns:
        A mapping of module name to the set of names imported from it.
    """
    taken = defaultdict(set)
    for root in applications():
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module \
                        and node.module.split(".")[0] == "keepup":
                    for alias in node.names:
                        taken[node.module].add(alias.name)
    return taken


def is_a_module(module: str, name: str) -> bool:
    """Whether `from module import name` is taking a submodule, not a name."""
    return (PACKAGE.parent / Path(module.replace(".", "/")) / f"{name}.py").is_file() \
        or (PACKAGE.parent / Path(module.replace(".", "/")) / name / "__init__.py").is_file()


def declared(module: str):
    """The public names of a module, or None when it declares none."""
    return getattr(importlib.import_module(module), "__all__", None)


# --- the declaration itself ----------------------------------------------------

def test_there_are_applications_to_check():
    """Otherwise everything below passes by having nothing to look at.

    In the framework's own repository there are none, and then this file has
    nothing to do -- which is a skip with a reason, not a green run.
    """
    if not applications():
        pytest.skip("no application in this repository imports the framework")


def test_the_root_of_the_package_declares_its_own():
    import keepup

    assert set(keepup.__all__) >= {"KeepupSettings", "StaticMount", "create_app", "__version__"}


@pytest.mark.parametrize("module", sorted(imports_of_the_framework()))
def test_a_module_an_application_imports_from_declares_what_is_public(module):
    """A module with no declaration publishes everything by accident."""
    names = {name for name in imports_of_the_framework()[module]
             if not is_a_module(module, name)}
    if not names:
        return  # only submodules taken from it; nothing to declare here
    assert declared(module) is not None, (
        f"{module} is imported by an application and declares no public names")


def test_every_name_an_application_takes_is_public():
    """The rule, as one list of everything that breaks it.

    Two private functions of keepup.auth.dependencies used to be here --
    `_authenticated_by_cookie` and `_bearer_of`, imported into the application's
    facade and called by nobody.
    """
    offenders = []
    for module, names in sorted(imports_of_the_framework().items()):
        public = declared(module)
        for name in sorted(names):
            if is_a_module(module, name):
                continue
            if public is None or name not in public:
                offenders.append(f"{module}.{name}")

    assert offenders == [], (
        "applications take these out of the framework, and the framework does "
        "not declare them public:\n  " + "\n  ".join(offenders)
        + "\n\nEither add the name to that module's __all__ -- meaning it is "
          "part of the package's interface and cannot be renamed lightly -- or "
          "stop taking it."
    )


def test_no_private_name_is_taken_by_an_application():
    """Said separately because it is the sharper half of the rule.

    A name with a leading underscore is the author saying "this is mine". An
    application reaching for one is depending on something nobody promised.
    """
    offenders = [f"{module}.{name}"
                 for module, names in imports_of_the_framework().items()
                 for name in names if name.startswith("_")]
    assert offenders == [], "\n".join(offenders)


# --- the declaration stays honest -----------------------------------------------

def declaring_modules():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        if "__all__" in path.read_text(encoding="utf-8"):
            found.append(".".join(path.relative_to(REPO).with_suffix("").parts))
    return found


@pytest.mark.parametrize("module", declaring_modules())
def test_every_declared_name_exists(module):
    """A name in the list that the module does not define is a promise to nobody."""
    imported = importlib.import_module(module)
    missing = [name for name in imported.__all__ if not hasattr(imported, name)]
    assert missing == [], f"{module} declares names it does not define: {missing}"


@pytest.mark.parametrize("module", declaring_modules())
def test_nothing_private_is_declared_public(module):
    imported = importlib.import_module(module)
    private = [name for name in imported.__all__
               if name.startswith("_") and not name.startswith("__")]
    assert private == [], f"{module} declares private names public: {private}"

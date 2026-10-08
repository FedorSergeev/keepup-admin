"""The constructor needs none of the libraries the base is to lose.

Task keepup-124. The release promises that `keepup-admin` plus a driver is a
deployment that talks to a database and carries no library it does not use: no
SQLAlchemy, no PostgreSQL driver, no password hasher, no JWT. The code that needs
those still lives in the base package in 0.4.0 and moves in the rest of this
task, so what can be pinned *now* -- and is the part that must never regress
while the move happens -- is the boundary itself: the kernel, the constructor
every plugin is loaded by, imports none of them.

The libraries and the distribution that will carry each are named here, so the
debt is measured rather than remembered.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
KERNEL = PACKAGE / "kernel"

#: The library, and the distribution that will carry it (keepup-124).
LEAVES_THE_BASE = {
    "sqlalchemy": "keepup-db",
    "psycopg2": "keepup-postgres",
    "bcrypt": "keepup-auth",
    "jwt": "keepup-auth",
}


def imported_roots(path):
    """The top-level modules a file imports, at any depth."""
    roots = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_kernel_imports_none_of_the_libraries_that_leave():
    """The constructor is what stays: it cannot need what the base is to lose."""
    offenders = {}
    for path in sorted(KERNEL.rglob("*.py")):
        found = imported_roots(path) & set(LEAVES_THE_BASE)
        if found:
            offenders[str(path.relative_to(PACKAGE))] = sorted(found)
    assert offenders == {}, (
        f"the kernel imports what a capability must carry: {offenders}. The "
        "abstraction reaches the database through the service, not by importing it."
    )


def test_every_library_that_leaves_has_a_distribution_that_takes_it():
    """A debt without a destination is a debt nobody pays."""
    distributions = {path.name for path in (PACKAGE / "packages").iterdir() if path.is_dir()}
    for library, distribution in LEAVES_THE_BASE.items():
        assert distribution in distributions, f"{library} is to go to {distribution}, which is not declared"


#: What has left the base, and the distribution that carries it now.
HAS_LEFT = {"psycopg2-binary": "keepup-postgres", "bcrypt": "keepup-auth",
            "pyjwt[crypto]": "keepup-auth"}


def test_a_library_that_left_is_no_dependency_and_has_no_keeper():
    """The release's whole point, checked one library at a time."""
    import tomllib

    declared = " ".join(
        tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
        ["project"]["dependencies"])
    for library, distribution in HAS_LEFT.items():
        import_name = library.split("[")[0].split("-")[0]
        assert library not in declared and import_name not in declared, (
            f"{library} is back in the base; it belongs to {distribution}")
        assert keepers(import_name) == [], (
            f"{library} left the base and these modules still import it: "
            f"{keepers(import_name)}")


def test_the_base_still_declares_them_and_that_is_the_debt():
    """Honest while the code is here: the check fails the day it is not needed.

    This is deliberately the opposite of the acceptance of keepup-124: the
    libraries are in the base because the code that imports them is, and the last
    cut of this task removes both together. When it does, this check has to be
    deleted in the same change -- which is what keeps the promise from being
    forgotten.
    """
    import tomllib

    text = (PACKAGE / "pyproject.toml").read_text(encoding="utf-8")
    metadata = tomllib.loads(text)
    declared = " ".join(metadata["project"]["dependencies"])
    for library in ("sqlalchemy",):
        assert library in declared, (
            f"{library} is no longer a dependency of the base: if the code that "
            "imported it has moved, delete this check in the same change and say "
            "so in doc/distributions.md"
        )


def test_the_kernel_does_not_import_a_capability_module():
    """The other direction of the same boundary: no `keepup.db`, `keepup.auth`."""
    capabilities = ("keepup.db", "keepup.tables", "keepup.schema", "keepup.audit",
                    "keepup.events", "keepup.metrics", "keepup.themes",
                    "keepup.integrations", "keepup.auth")
    offenders = {}
    for path in sorted(KERNEL.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = [name for name in capabilities if f"import {name}" in text or f"from {name}" in text]
        if found:
            offenders[str(path.relative_to(PACKAGE))] = found
    assert offenders == {}, f"the kernel imports a capability: {offenders}"


def keepers(library):
    """The base modules that import a library, computed from the code."""
    found = []
    for path, relative in _framework_files():
        text = path.read_text(encoding="utf-8")
        if f"import {library}" in text or f"from {library}" in text:
            found.append(relative)
    return found


def _framework_files():
    """Every Python file of the base package, checks and artifacts aside."""
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(PACKAGE).parts
        if any(part.startswith(".") or part in
               ("tests", "ci", "openspec", "doc", "packages", "build", "__pycache__")
               for part in parts):
            continue
        yield path, "/".join(parts)


def test_every_library_that_stays_has_its_keepers_written_down():
    """The list the next move reads: who keeps a library in the base today.

    A library leaves when the last module that imports it has moved, so the
    document has to name those modules -- and a module that moves has to leave
    this table in the same change, or the table stops being true silently.
    """
    text = (PACKAGE / "doc" / "distributions.md").read_text(encoding="utf-8")
    section = text[text.index("## Which modules keep a library in the base"):]
    section = section[:section.index("\n## ")]
    for library in LEAVES_THE_BASE:
        found = keepers(library)
        if not found:
            # A library no module of the base imports is still needed at run time
            # by something the base does: `psycopg2` is the driver SQLAlchemy
            # reaches for when it connects, and the reference to it is a
            # connection string rather than an import. It leaves with the module
            # that builds that string (keepup-124), and until then it is named
            # here so it is not simply forgotten.
            assert library in section or library.replace("-binary", "") in section, (
                f"{library} is a dependency of the base, no module imports it, and "
                "the document does not say why")
            continue
        missing = [name for name in found if f"`{name}`" not in section]
        assert missing == [], (
            f"these modules keep {library} in the base and the table does not name "
            f"them: {missing}"
        )

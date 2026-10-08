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
    for library in ("sqlalchemy", "psycopg2-binary", "bcrypt", "pyjwt"):
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

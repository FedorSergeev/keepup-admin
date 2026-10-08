"""The kernel imports no capability, and the sign-in leaks nowhere new.

Task keepup-119. The kernel's whole claim is that it knows nothing about the
capabilities it ships, and the load-bearing part of that claim is an import: one
convenient `from keepup.auth...` inside a function and the boundary is gone
invisibly, because nothing fails. This check is the thing that fails.

It is a list that may only shrink. A module that stops importing the sign-in has
to be struck off it in the same change -- otherwise the list would keep a
freedom nobody uses and the next reader would think the boundary is looser than
it is -- and a module that starts importing it is a change that has to say so in
the specification first.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]

#: Never part of the framework's shipped tree for this check.
SKIPPED = ("__pycache__", ".venv", "build", "dist", "tests", "ci", "openspec", "doc")

#: What the sign-in is: the modules a capability owns, and the shape it uses.
SIGN_IN = "keepup.auth"

#: Who may still import it, and why. Removing an entry is the point of 0.4.0.
STILL_IMPORTING = {
    "api_docs.py": "the API schema is served behind the administrator's sign-in",
    "cluster.py": "its routes are the administrator's",
    "events_api.py": "its routes are the administrator's",
    "factory.py": "the composition root puts the identity behind the seam (keepup-119)",
    "integrations.py": "a call is attributed to the user who made it",
    "locks.py": "its routes are the administrator's",
    "metrics_api.py": "its panel routes are the administrator's",
    "modules.py": "its routes are the administrator's",
    "notification_bus.py": "it signs what replicas send to each other",
    "plugins/admin.py": "the decision about a plugin is the administrator's",
    "plugins/routes.py": "it registers the WebSocket sign-in; the HTTP one goes through the seam",
    "scheduler.py": "its routes are the administrator's",
    "schema.py": "the first accounts are seeded with the schema",
    "themes.py": "its routes are the administrator's",
    "web.py": "its routes are the administrator's",
}


def package_files():
    """Every Python file of the framework, tests and artifacts aside."""
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        if any(part in SKIPPED for part in relative.parts):
            continue
        yield path, str(relative)


def imports_of(path):
    """The ``keepup.auth`` names a file imports, at any depth.

    Walks the tree rather than reading the module: an import inside a function
    crosses the same boundary as one at the top of the file, and it is the one
    nobody notices.
    """
    found = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == SIGN_IN or node.module.startswith(SIGN_IN + "."):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == SIGN_IN or alias.name.startswith(SIGN_IN + "."):
                    found.add(alias.name)
    return found


def importers():
    """Every module of the framework that imports the sign-in."""
    return {relative for path, relative in package_files() if imports_of(path)}


def test_only_the_named_modules_import_the_sign_in():
    """A new edge is a decision, and a decision is a change to the specification."""
    unknown = sorted(
        name for name in importers()
        if name not in STILL_IMPORTING and not name.startswith("auth/")
    )
    assert unknown == [], (
        "these modules import the sign-in and are not in the list: "
        f"{unknown}. Either the kernel owns the shape they need "
        "(keepup/kernel/security.py) or the specification changes first."
    )


def test_the_list_of_modules_still_importing_it_only_shrinks():
    """An entry nobody uses is a claim about the boundary that is no longer true."""
    stale = sorted(name for name in STILL_IMPORTING if name not in importers())
    assert stale == [], (
        f"these modules no longer import the sign-in: {stale}. Strike them off "
        "the list in the same change: it is what the release is for."
    )


def test_the_kernel_imports_no_capability_at_all():
    """`keepup/kernel` is the constructor: names and shapes, never an implementation."""
    offenders = {}
    for path, relative in package_files():
        if not relative.startswith("kernel/"):
            continue
        found = imports_of(path)
        if found:
            offenders[relative] = sorted(found)
    assert offenders == {}, (
        f"the kernel imports the sign-in: {offenders}. The kernel owns the shape "
        "(keepup/kernel/security.py); the implementation is a service it asks for."
    )


def test_the_router_no_longer_imports_the_subject_or_the_right():
    """The one edge this task removed, kept removed (keepup-119).

    The WebSocket sign-in is still imported here and moves with the transport
    (keepup-123): what this task cut is the HTTP subject and the right, which
    now come from ``keepup.kernel.security``.
    """
    found = imports_of(PACKAGE / "plugins" / "routes.py")
    assert found <= {"keepup.auth.websocket"}, (
        f"plugins/routes.py still imports the subject or the right: {sorted(found)}"
    )


def test_the_shape_of_a_right_is_the_kernel_s_own():
    """The sign-in re-exports it, and the kernel does not import the sign-in to get it."""
    from keepup.auth.identity.contract import AccessRequest as Published
    from keepup.kernel.security import AccessRequest as Owned

    assert Published is Owned
    action = Owned(permission="reports.read", method="GET", path="/api/reports")
    assert (action.permission, action.method, action.path) == (
        "reports.read", "GET", "/api/reports")

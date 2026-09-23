"""What the framework may carry out of this repository, as a check.

``keepup`` is being released on its own: a package on PyPI, a submodule of its
own repository, a dependency of applications written by people who will never
see this repository. That turns several habits of a private tree into defects
-- a Russian comment, a password good enough for a stand, the address of the
machine the code was written on.

None of it is visible at runtime, and none of it breaks a test that exercises
behaviour, which is why it is checked here against the source tree instead. It
has to fail before the application starts, not after the package is published.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/distribution_tests.py -v

The boundary of dependence -- "the framework does not import the application"
-- is a different statement and lives in boundaries_tests.py.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEEPUP = REPO / "keepup"

# Written as escapes, not as the letters themselves: this file is inside the
# package it checks, and a literal range would make the check fail on itself.
CYRILLIC = re.compile("[\u0400-\u04FF]")

#: Field markers of Sphinx and epydoc. Matched at the start of a line only --
#: a docstring may well mention `:param_name` while describing the syntax of
#: named parameters, and that is not markup.
FOREIGN_DOCSTRING_MARKUP = re.compile(
    r"^\s*(:param\s|:returns?:|:rtype:|:raises|:type\s|:key\s|@param\s|@return)")

#: A name holding one of these is a credential, whatever it is called around
#: it.
SECRETISH = re.compile(r"(PASSWORD|SECRET|TOKEN|CREDENTIAL|\bKEY\b|_KEY|KEY_)", re.IGNORECASE)

#: Names that match SECRETISH but cannot hold a secret: the name of an
#: environment variable, the reason a session was ended, the path a value is
#: read from.
NOT_A_VALUE = re.compile(r"(_ENV$|^REASON_|_PATH$|_HEADER$|_FIELD$|_NAME$)")

#: Places where a credential in the source is the opposite of a leak: the
#: framework lists these in order to refuse them. Each is named with why.
REFUSAL_LISTS = {
    ("keepup/auth/signing_key.py", "PLACEHOLDER_KEYS"):
        "signing keys from the setup examples -- a deployment still carrying "
        "one has not been configured, and the framework refuses to start",
    ("keepup/auth/seed_accounts.py", "RETIRED_SYSTEM_PASSWORDS"):
        "passwords earlier builds gave the system account -- an existing row "
        "holding one stops being a way in at the next start",
    ("keepup/auth/seed_accounts.py", "RETIRED_ADMIN_PASSWORDS"):
        "the administrator password earlier builds shipped, named so the "
        "start-up can say it is still in place",
}

#: An address of a deployment. localhost counts: it is the address of the
#: machine the code was written on, and as a default it turns "no collector was
#: configured" into shipping that silently fails against a closed port.
ADDRESS = re.compile(r"""https?://[^\s"')]+|\b(?:\d{1,3}\.){3}\d{1,3}\b""")

#: Names of the applications standing on this framework. The framework carries
#: none of them: a name it needs arrives through KeepupSettings. Product names
#: (ozon, servershare, ...) are checked separately in boundaries_tests.py.
APPLICATION_NAMES = ("keepup.tracker", "keepup tracker", "tracker",
                    # A customer of an earlier project, whose database name sat
                    # in keepup/db.py until task keepup-15. The check that
                    # exists is the one that would have caught it.
                    "sanroyal")


#: The package's own tests. They travel with the repository and are read by
#: whoever opens it, so the language rule reaches them -- but nothing else
#: here does: a test carries invented addresses and pretend tokens by trade,
#: and a two-field stub class inside one has nothing a docstring would add.
TESTS = KEEPUP / "tests"


def keepup_sources():
    """Every Python source file of the package, tests excluded.

    Returns:
        A sorted list of paths.
    """
    return sorted(path for path in KEEPUP.rglob("*.py")
                  if TESTS not in path.parents and not _is_build_output(path))


#: Directories a local build leaves inside the package. They hold copies of
#: the sources, so scanning them reports every finding twice and names a path
#: nobody edits -- and one `python -m build` was enough to turn this whole
#: file red with output that pointed at build/lib (keepup-28).
BUILD_OUTPUT = {"build", "dist", "static.min", "__pycache__", ".pytest_cache"}


def _is_build_output(path):
    return any(part in BUILD_OUTPUT or part.endswith(".egg-info")
               for part in path.relative_to(KEEPUP).parts)


def keepup_tests():
    """Every test file of the package.

    Returns:
        A sorted list of paths.
    """
    return sorted(TESTS.rglob("*.py"))


def relative(path):
    """Path as written in the exception lists above."""
    return str(path.relative_to(REPO))


# --- what the package ships besides its code ------------------------------------

#: Third-party bundles the package ships on purpose. They are minified, they
#: are not ours to rewrite, and reading them for our own rules says nothing.
VENDORED = ("js/tailwind.js", "js/feather-icons.js", "js/aos.js",
            "modules/js/chart.js", "modules/js/chartjs-adapter-date-fns.bundle.min.js")

#: Names of the products built on this framework. The package carries none of
#: them -- not in code, and not in a script, a stylesheet or a page either,
#: which is how a marketplace upload dialog lived in the panel shell until
#: task keepup-27: the check that existed read only *.py.
PRODUCT_NAMES = ("ozon", "wildberries", "yamarket", "yookassa", "servershare",
                 "loradataset", "marketplace", "marketconnect", "russim", "sanroyal")

#: Route prefixes the framework registers itself. Anything the package's own
#: front end calls must be one of these.
FRAMEWORK_ROUTES = ("/api/admin/", "/api/auth/", "/api/events", "/api/modules",
                    "/api/integration-logs", "/api/theme/", "/api/version",
                    "/api/public/config")

#: Routes of an application that the package's front end still calls, with what
#: it takes to stop. An explicit list that only shrinks -- a new one fails the
#: run rather than joining it.
ROUTES_STILL_CALLED = {
    "/api/agreements": "Documents of the platform. Asked for only when the "
                       "framework's own /api/auth/me says this person has any, "
                       "so an application without them is never called -- but "
                       "the route is still the application's (keepup-27).",
}

#: Where a route appears in the package's front end.
ROUTE = re.compile(r"/api/[a-zA-Z0-9_/-]*")


#: Suffixes of what the package ships besides its code.
SHIPPED_SUFFIXES = (".js", ".css", ".html", ".json", ".toml")


def keepup_assets():
    """Every file of the package that is not Python and not vendored.

    Scripts, stylesheets, pages and the package's own declarations go into the
    wheel exactly as they are and are read by whoever installs it, so the
    rules of the distribution reach them too.

    The whole package is walked, not only ``static``: ``sections.json`` and
    ``pyproject.toml`` sit at the top and ship as surely as anything under it
    -- and both carried Russian until the walk was widened (keepup-27).

    Returns:
        A sorted list of paths.
    """
    found = []
    for path in sorted(KEEPUP.rglob("*")):
        if not path.is_file() or path.suffix not in SHIPPED_SUFFIXES:
            continue
        if any(str(path).endswith(name) for name in VENDORED):
            continue
        if _is_build_output(path) or ".git" in path.relative_to(KEEPUP).parts:
            continue
        found.append(path)
    return found


#: Calls that only wrap a literal collection. Any other call is not looked
#: into: ``os.getenv('DB_PASSWORD')`` names a variable, and
#: ``value.encode('utf-8')`` names an encoding -- neither is a secret, and
#: reading their arguments as one is how this check would become noise nobody
#: reads.
COLLECTION_BUILDERS = ("frozenset", "set", "tuple", "list")


def string_constants(node):
    """String literals a value expression carries directly.

    A tuple, list or set of passwords is as much a password in the source as a
    single one, and ``frozenset({...})`` is how one of them is written here.

    Args:
        node: the right-hand side of an assignment.

    Yields:
        Each string literal found at the top level of that expression.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for element in node.elts:
            yield from string_constants(element)
    elif isinstance(node, ast.Call):
        called = node.func
        name = called.id if isinstance(called, ast.Name) else getattr(called, "attr", "")
        if name in COLLECTION_BUILDERS:
            for argument in node.args:
                yield from string_constants(argument)


def assigned_names(node):
    """The names an assignment writes to.

    Args:
        node: an ``ast.Assign`` or ``ast.AnnAssign``.

    Yields:
        Each target name, attributes included (``self.token`` yields
        ``token``).
    """
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            yield target.id
        elif isinstance(target, ast.Attribute):
            yield target.attr


def test_the_framework_is_written_in_english():
    """A package read by people outside this repository.

    Comments here carry the reason for a decision, so a Russian one is not a
    cosmetic problem: it is the reason, unavailable to the reader.

    It reaches the scripts, stylesheets and pages as well, and not for
    symmetry: the panel's own text is what a person installing this package
    reads first, and until keepup-27 eight hundred lines of it were in Russian
    because this check read *.py and nothing else. A comment is the reason
    behind a decision, made unavailable to the reader; a label is the product
    itself, in a language its user may not have.

    Unconditional since keepup-2. The list of files this used to forgive is
    gone rather than empty: an empty list is an invitation to write a name into
    it.
    """
    offenders = []
    for path in keepup_sources() + keepup_tests() + keepup_assets():
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = CYRILLIC.search(text)
        if found:
            line = text[: found.start()].count("\n") + 1
            offenders.append(f"{relative(path)}:{line}")
    assert offenders == [], (
        "Russian text in the framework:\n" + "\n".join(offenders)
    )


def test_every_module_explains_itself():
    """A reader of the package arrives from an index of names, not from git.

    A module that starts straight at its imports answers them with its name and
    nothing else.
    """
    offenders = []
    for path in keepup_sources():
        source = path.read_text(encoding="utf-8")
        if not source.strip():
            continue
        if not ast.get_docstring(ast.parse(source)):
            offenders.append(relative(path))
    assert offenders == [], (
        "modules of the package with no docstring:\n" + "\n".join(offenders)
    )


def test_every_class_explains_itself():
    """Including the shapes a request and a reply are described by.

    Those are the package's API as much as its functions are: a field list with
    no sentence over it says what may be sent and not what it means.
    """
    offenders = []
    for path in keepup_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and not ast.get_docstring(node):
                offenders.append(f"{relative(path)}:{node.lineno} {node.name}")
    assert offenders == [], (
        "classes of the package with no docstring:\n" + "\n".join(offenders)
    )


def test_the_package_has_one_docstring_style():
    """Google sections, and no second markup beside them.

    A package written in two markups is read whole by no documentation
    generator. Nothing matches this today -- the check is here so that nothing
    starts to.

    The marker has to be at the start of a line: `NOTE: takes NAMED parameters
    (:param_name)` mentions the syntax of named parameters and is not Sphinx.
    """
    offenders = []
    for path in keepup_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            for number, line in enumerate(doc.splitlines(), 1):
                if FOREIGN_DOCSTRING_MARKUP.match(line):
                    where = getattr(node, "name", "<module>")
                    offenders.append(f"{relative(path)}: {where}: {line.strip()}")
    assert offenders == [], (
        "docstrings in a markup that is not Google:\n" + "\n".join(offenders)
    )


def test_the_package_ships_no_credentials():
    """No password, key or token written into the package.

    A credential in the source is the same on every deployment and known to
    everyone who has seen the code, which is what makes it not a credential.
    """
    offenders = []
    for path in keepup_sources():
        name = relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            for target in assigned_names(node):
                if not SECRETISH.search(target) or NOT_A_VALUE.search(target):
                    continue
                if (name, target) in REFUSAL_LISTS:
                    continue
                values = [v for v in string_constants(node.value) if v] if node.value else []
                if values:
                    offenders.append(f"{name}:{node.lineno}: {target} = {values[0]!r}")
    assert offenders == [], (
        "the framework carries a credential in its source:\n" + "\n".join(offenders)
        + "\n\nIf none of these is a secret, the name is what tripped the check: "
          "rename it to *_FIELD or *_NAME. If it is a list of values the "
          "framework REFUSES, add it to REFUSAL_LISTS above with the reason. "
          "The rule errs towards false positives on purpose -- a missed key in "
          "a published package costs more than a rename."
    )


def test_a_function_named_for_a_secret_returns_no_literal():
    """The other shape of the same thing.

    ``_get_token()`` used to return a word typed into the framework; every
    application on it then presented the same one.
    """
    offenders = []
    for path in keepup_sources():
        name = relative(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not SECRETISH.search(node.name):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and inner.value is not None:
                    values = [v for v in string_constants(inner.value) if v]
                    if values:
                        offenders.append(f"{name}:{inner.lineno}: {node.name}() returns {values[0]!r}")
    assert offenders == [], (
        "a function named for a secret hands one back from the source:\n" + "\n".join(offenders)
    )


def test_the_package_knows_no_deployment_address():
    """Not even as a default, and not even localhost.

    An address in the package is the address of somebody else's machine. As a
    default it is worse than missing: shipping to a closed port looks like
    shipping.
    """
    offenders = []
    for path in keepup_sources():
        name = relative(path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            found = ADDRESS.search(line)
            if found:
                offenders.append(f"{name}:{number}: {found.group(0)}")
    assert offenders == [], (
        "the framework carries an address of a deployment:\n" + "\n".join(offenders)
    )


def test_the_package_does_not_name_the_application():
    """Including in what it says about itself to an outside service.

    The framework registered with the log collector as "Main KeepUP Tracker
    application" -- a description of one deployment, shipped to everyone.
    """
    offenders = []
    for path in keepup_sources():
        name = relative(path)
        text = path.read_text(encoding="utf-8").lower()
        for word in APPLICATION_NAMES:
            if word in text:
                offenders.append(f"{name}: {word}")
    assert offenders == [], (
        "the framework names an application standing on it:\n" + "\n".join(offenders)
    )


# --- the rules reach what the package ships beside its code ---------------------

def test_no_product_is_named_in_what_the_package_ships():
    """A script of the package is as published as its code.

    Until task keepup-27 the panel shell carried a marketplace upload dialog --
    a hundred and fifty lines calling that product's endpoints, reached by
    nothing, shipped to everyone. The check that existed read *.py and saw
    none of it.
    """
    offenders = []
    for path in keepup_assets():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            lowered = line.lower()
            for name in PRODUCT_NAMES:
                if name in lowered:
                    offenders.append(f"{relative(path)}:{number}: {name} -- {line.strip()[:70]}")

    assert offenders == [], (
        "the package ships files naming a product built on it:\n  "
        + "\n  ".join(offenders))


def test_the_front_end_calls_only_routes_the_framework_registers():
    """A route of one application, called from the package, is a refusal everywhere else.

    The panel shell used to ask every application for the platform's documents.
    Two of three have no such route, so every sign-in there answered 404 --
    harmless by luck, because the answer happened to be handled.
    """
    offenders = []
    for path in keepup_assets():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for route in ROUTE.findall(line):
                if route.startswith(FRAMEWORK_ROUTES):
                    continue
                if any(route.startswith(known) for known in ROUTES_STILL_CALLED):
                    continue
                offenders.append(f"{relative(path)}:{number}: {route}")

    assert offenders == [], (
        "the package's front end calls routes the framework does not register:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither the framework registers the route, or the front end stops "
          "calling it. A route that only one application has is a refusal in "
          "the others and an empty address in the published package.")


def test_the_list_of_routes_still_called_only_shrinks():
    """An exception that nobody uses any more is an exception nobody removed."""
    seen = set()
    for path in keepup_assets():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for route in ROUTE.findall(text):
            for known in ROUTES_STILL_CALLED:
                if route.startswith(known):
                    seen.add(known)

    stale = sorted(set(ROUTES_STILL_CALLED) - seen)
    assert stale == [], (
        f"these routes are no longer called and their exceptions are due to go: {stale}")

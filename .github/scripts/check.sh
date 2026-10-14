#!/bin/bash
# Every check this repository can make locally, in one command.
#
#     .github/scripts/check.sh            # everything, as the workflows run it
#     .github/scripts/check.sh --fast     # skip the wheel build and the audit
#     .github/scripts/check.sh --help
#
# The steps are the workflows' steps, in their order:
#
#   1. ruff            what quality.yml blocks on -- syntax, undefined names,
#                      unused imports -- with the same isolated selection
#   2. the suite       tests/ with coverage, exactly as tests.yml runs it
#   3. release         the tree's version and its fifteen distributions agree,
#                      and the graph that holds the split together points one way
#   4. the audit       tests/security_audit_tests/run_security_audit.sh: knocks
#                      on every route of six applications and audits the
#                      dependency floors (needs pip-audit; skipped without it)
#   5. distributions   every wheel built, twine-checked, installed into an empty
#                      virtualenv and asked to stand up -- build.yml's claim,
#                      which a developer's tree with editable installs hides
#
# Everything runs; the summary at the end says what failed and the exit code is
# non-zero if anything did. A step whose tool is missing says so and is counted
# as skipped, not as passed.
#
# The python is $PYTHON, or python3. Activate the environment that has keepup and
# its capabilities installed (`pip install -e ".[test]" -e packages/keepup-*`)
# before running this: the checks import the tree, not the checkout's path.

set -u

PYTHON="${PYTHON:-python3}"
FAST=0
while [ $# -gt 0 ]; do
    case "$1" in
        --fast) FAST=1 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The scripts live inside the package: two directories up is keepup-admin
# itself, which is also the repository -- and the working directory every check
# below expects.
PACKAGE="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/keepup-check.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

cd "$PACKAGE" || exit 2
if ! "$PYTHON" -c "import keepup" 2>/dev/null; then
    cat >&2 <<MESSAGE
keepup is not importable with $PYTHON from $PACKAGE.

Install the tree and its capabilities first:

    $PYTHON -m pip install -e ".[test]" -e packages/keepup-*
MESSAGE
    exit 2
fi

PASSED=0
FAILED=()
SKIPPED=()

started() { date +%s; }
finished() {
    local began="$1" name="$2"
    printf '  %-22s %3ss\n' "$name" "$(( $(date +%s) - began ))"
}

run_step() {
    local name="$1"; shift
    local began; began="$(started)"
    echo
    echo "=== $name"
    if "$@"; then
        finished "$began" "$name"
        PASSED=$((PASSED + 1))
        return 0
    fi
    finished "$began" "$name"
    FAILED+=("$name")
    return 1
}

skip_step() {
    local name="$1" why="$2"
    echo
    echo "=== $name -- skipped: $why"
    SKIPPED+=("$name")
}

# --- 1. the lint -----------------------------------------------------------------

lint() { "$PYTHON" -m ruff check . --no-cache --isolated --select E9,F63,F7,F82,F; }

if "$PYTHON" -c "import ruff" 2>/dev/null || command -v ruff >/dev/null 2>&1; then
    if command -v ruff >/dev/null 2>&1 && ! "$PYTHON" -c "import ruff" 2>/dev/null; then
        lint() { ruff check . --no-cache --isolated --select E9,F63,F7,F82,F; }
    fi
    run_step "ruff" lint
else
    skip_step "ruff" "install it: $PYTHON -m pip install ruff"
fi

# --- 2. the suite ----------------------------------------------------------------

suite() {
    "$PYTHON" -m pytest tests/ -q \
        --cov --cov-config=pyproject.toml \
        --cov-report=term-missing:skip-covered
}
run_step "the suite" suite

# --- 3. the release and the graph ------------------------------------------------

release() {
    local version
    version="$("$PYTHON" - <<'PY'
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
print(re.search(r'^version = "([^"]+)"', text, re.M).group(1))
PY
)"
    "$PYTHON" .github/scripts/release_versions.py "$version" &&
        "$PYTHON" .github/scripts/dependency_graph.py
}
run_step "the release" release

# --- 4. the security audit -------------------------------------------------------

if [ "$FAST" = "0" ]; then
    if "$PYTHON" -c "import pip_audit" 2>/dev/null; then
        # The report outlives the run: it goes to the temporary directory, not
        # into the working copy, and its path is printed below.
        REPORT="${TMPDIR:-/tmp}/keepup_security_audit_$(date +%Y%m%d_%H%M%S).md"
        audit() { PYTHON="$PYTHON" bash tests/security_audit_tests/run_security_audit.sh "$REPORT"; }
        run_step "the security audit" audit
        echo "  report: $REPORT"
    else
        skip_step "the security audit" "install pip-audit: $PYTHON -m pip install pip-audit"
    fi
else
    skip_step "the security audit" "--fast"
fi

# --- 5. the distributions --------------------------------------------------------

distributions() {
    local dist="$WORK/dist" empty="$WORK/elsewhere"
    mkdir -p "$dist"
    for package in "$PACKAGE" "$PACKAGE"/packages/keepup-*; do
        # --no-isolation: the environment running the check has setuptools, and
        # building in an isolated one asks the network for it -- which a machine
        # without one answers with a very long failure (the CI job has the
        # network and builds the way the release does).
        "$PYTHON" -m build --no-isolation --outdir "$dist" "$package" > "$WORK/build.log" 2>&1 || {
            echo "building $package failed:"; tail -20 "$WORK/build.log"; return 1
        }
    done
    "$PYTHON" -m twine check --strict "$dist"/* || return 1

    "$PYTHON" -m venv "$empty" || return 1
    "$empty/bin/pip" install --quiet --upgrade pip || return 1
    "$empty/bin/pip" install --quiet "$dist"/*.whl || return 1
    ( cd "$WORK" && "$empty/bin/python" - <<'PY'
import pathlib
import keepup
import keepup_http
from keepup import KeepupSettings, create_app

app = create_app(KeepupSettings(title="Elsewhere", project_name="elsewhere",
                                static_mounts=(), plugin_manager=None))
here = pathlib.Path(keepup.__file__).parent
transport = pathlib.Path(keepup_http.__file__).parent
assert app.routes, "the application came up with no routes"
# The shell travels with the transport that serves it, and the base carries no
# front end at all (keepup-121, keepup-154).
assert transport.joinpath("static/js/main_new.js").is_file(), "the panel shell did not ship"
assert not here.joinpath("static").exists(), "the base ships a front end again"
assert here.joinpath("sections.json").is_file(), "the section catalogue did not ship"
print("version", keepup.__version__, "routes", len(app.routes))
PY
    )
}

if [ "$FAST" = "0" ]; then
    if ! "$PYTHON" -c "import build, twine" 2>/dev/null; then
        skip_step "the distributions" "install them: $PYTHON -m pip install build twine"
    elif ! "$PYTHON" -c "import setuptools, wheel" 2>/dev/null; then
        # Building without isolation needs setuptools and wheel in this
        # environment, and building with it asks the network for them: a machine
        # that has neither gets a long failure rather than a check.
        skip_step "the distributions" "install them to build offline: $PYTHON -m pip install setuptools wheel"
    else
        run_step "the distributions" distributions
    fi
else
    skip_step "the distributions" "--fast"
fi

# --- the summary -----------------------------------------------------------------

echo
echo "=== summary"
printf '  passed:  %s\n' "$PASSED"
if [ "${#SKIPPED[@]}" -gt 0 ]; then
    printf '  skipped: %s\n' "$(IFS=', '; echo "${SKIPPED[*]}")"
fi
if [ "${#FAILED[@]}" -eq 0 ]; then
    echo "  failed:  none"
    exit 0
fi
printf '  failed:  %s\n' "$(IFS=', '; echo "${FAILED[*]}")"
exit 1

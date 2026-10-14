#!/bin/bash
# The keepup security audit, run by hand against this tree (keepup-94).
#
# Builds applications on keepup from the code checked out here, knocks on every
# door they have, and writes what it found as a Markdown report:
#
#     keepup/tests/security_audit_tests/run_security_audit.sh [report.md] [pytest args...]
#
# The report goes to the path given, or to a timestamped file in the temporary
# directory -- never into the tree, where it would be one more untracked file.
# The exit code is pytest's: 0 when every check held, non-zero otherwise.
#
# Run from the repository that holds keepup, or from keepup's own checkout: the
# script finds its way to the directory where `import keepup` resolves.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The package is two directories up from here; the repository is where its
# pyproject.toml is, which is the package itself in keepup's own checkout and the
# directory above it in an application that vendored the package.
PACKAGE="$(dirname "$(dirname "$HERE")")"
ROOT="$(dirname "$PACKAGE")"
[ -f "$PACKAGE/pyproject.toml" ] && ROOT="$PACKAGE"

# The interpreter is the one the caller runs, so that the audit uses the
# environment with keepup installed (keepup-154).
PYTHON="${PYTHON:-python3}"

REPORT="${1:-${TMPDIR:-/tmp}/keepup_security_audit_$(date +%Y%m%d_%H%M%S).md}"
shift $(( $# > 0 ? 1 : 0 ))

cd "$ROOT" || exit 2
if ! "$PYTHON" -c "import keepup" 2>/dev/null; then
    echo "keepup is not importable from $ROOT -- activate the environment that has its dependencies" >&2
    exit 2
fi
if ! "$PYTHON" -c "import pip_audit" 2>/dev/null; then
    echo "pip-audit is not installed: the dependency check will be skipped" \
         "($PYTHON -m pip install pip-audit)" >&2
fi

export SECURITY_AUDIT_REPORT="$REPORT"
"$PYTHON" -m pytest "$HERE"/*_tests.py -q -rs -p no:cacheprovider "$@"
status=$?

echo ""
echo "Report: $REPORT"
exit $status

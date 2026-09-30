"""The security audit suite: shared environment, markers and the report (keepup-94).

The environment is the framework suite's own -- a throwaway database, a signing
key, a stubbed metrics collector, framework state put back after every
application built: keepup/tests/conftest.py applies here as to any directory
under it. Two copies of it would drift, and a check that passes because its
environment differs is worse than none.

Two markers say what a check is about. ``finding("keepup-52#7")`` ties it to a
finding of an audit, ``area("tokens")`` to a part of the surface. With
``SECURITY_AUDIT_REPORT`` set, the outcomes are written there as Markdown at the
end of the run -- which is what ``run_security_audit.sh`` does; CI does not.

    python3 -m pytest keepup/tests/security_audit_tests/route_sweep_tests.py -v
"""

import os
import time
from collections import defaultdict
from pathlib import Path

import pytest

REPORT_ENV = "SECURITY_AUDIT_REPORT"

#: What each area covers, in the order the report lists them.
AREAS = {
    "routes": "Every route of every profile: sign-in, the administrative right, CSRF, sockets",
    "tokens": "Forged, expired, orphaned and revoked tokens",
    "findings": "Regression of the findings of audit keepup-52",
    "identity": "The identity provider surface (keepup-91)",
    "dependencies": "Known vulnerabilities in the dependencies (pip-audit)",
}


def pytest_configure(config):
    config.addinivalue_line("markers", "finding(ref): the audit finding this check holds fixed")
    config.addinivalue_line("markers", "area(name): the part of the surface this check covers")
    config._security_audit = {"meta": {}, "results": [], "started": time.time()}


def _marker(item, name):
    marker = item.get_closest_marker(name)
    return marker.args[0] if marker and marker.args else None


def _profile_of(item):
    callspec = getattr(item, "callspec", None)
    return (callspec.params.get("profile_name") if callspec is not None else None) or ""


def pytest_collection_modifyitems(config, items):
    state = config._security_audit
    # Checks of one profile in a row, so each application starts once per file
    # rather than once per check. Stable: the order within a profile is kept.
    items.sort(key=lambda item: (str(item.fspath), _profile_of(item)))
    for item in items:
        callspec = getattr(item, "callspec", None)
        profile = None
        if callspec is not None:
            profile = callspec.params.get("profile_name") or callspec.params.get("profile")
            profile = getattr(profile, "name", profile)
        state["meta"][item.nodeid] = {
            "name": item.name,
            "file": Path(str(item.fspath)).name,
            "finding": _marker(item, "finding"),
            "area": _marker(item, "area") or "other",
            "profile": profile,
        }


def pytest_runtest_logreport(report):
    """One row per check and phase that says something.

    A failure while setting up or tearing down counts as a failure of the check:
    a harness that cannot start an application has not shown that it is safe.
    A strict xfail is a known finding still open, and says so.
    """
    if report.when == "call":
        if hasattr(report, "wasxfail"):
            _results.append((report.nodeid, "known finding", report.wasxfail))
            return
        outcome, reason = report.outcome, ""
    elif report.failed:
        outcome, reason = "failed", f"error at {report.when}"
    elif report.when == "setup" and report.skipped:
        outcome, reason = "skipped", ""
    else:
        return
    if report.skipped and isinstance(report.longrepr, tuple):
        reason = str(report.longrepr[2]).replace("Skipped: ", "")
    elif report.failed:
        text = str(getattr(report, "longreprtext", "")).strip().splitlines()
        reason = (reason + ": " if reason else "") + (text[-1][:200] if text else "")
    _results.append((report.nodeid, outcome, reason))


#: Filled by the hook above; pytest_sessionfinish writes it out.
_results = []


def _cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def write_report(path: Path, meta, results, duration: float) -> None:
    """The Markdown report: a summary per area, then every check."""
    by_area = defaultdict(lambda: defaultdict(int))
    rows = []
    for nodeid, outcome, reason in results:
        info = meta.get(nodeid, {"name": nodeid, "area": "other", "finding": None,
                                 "profile": None, "file": ""})
        by_area[info["area"]][outcome] += 1
        rows.append((info["area"], info["file"], info["name"], info["profile"] or "",
                     info["finding"] or "", outcome, reason))

    failed = sum(counts["failed"] for counts in by_area.values())
    known = [row for row in rows if row[5] == "known finding"]
    lines = [
        "# keepup security audit",
        "",
        f"- Run: {time.strftime('%Y-%m-%d %H:%M:%S')}, {duration:.1f} s",
        f"- Tree: {os.getcwd()}",
        f"- Verdict: **{'FAILED' if failed else 'passed'}** "
        f"({failed} failed of {len(rows)} checks, {len(known)} known findings open)",
        "",
        "## By area",
        "",
        "| Area | What it covers | Passed | Failed | Skipped | Known findings |",
        "|---|---|---|---|---|---|",
    ]
    for area in list(AREAS) + sorted(set(by_area) - set(AREAS)):
        if area not in by_area:
            continue
        counts = by_area[area]
        lines.append(f"| {area} | {_cell(AREAS.get(area, ''))} | {counts['passed']} | "
                     f"{counts['failed']} | {counts['skipped']} | {counts['known finding']} |")
    if known:
        lines += ["", "## Known findings still open", ""]
        lines += [f"- `{row[2]}` ({row[1]}): {_cell(row[6])}" for row in known]
    lines += ["", "## Every check", "",
              "| Area | File | Check | Profile | Finding | Outcome | Reason |",
              "|---|---|---|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: (list(AREAS).index(r[0]) if r[0] in AREAS else 99,
                                           r[1], r[2])):
        lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the applications -------------------------------------------------------------------

@pytest.fixture(scope="session")
def the_other_system():
    """The homegrown system the identity profile relies on, once per run."""
    from audit_profiles import other_system
    with other_system() as other:
        yield other


@pytest.fixture(scope="session")
def applications(the_other_system, tmp_path_factory):
    """The application of one profile at a time, kept until another is asked for.

    Starting one is a lifespan with plugins, a scheduler and tasks; doing it per
    check would make the suite slow enough that nobody runs it before a release,
    so checks are ordered by profile (pytest_collection_modifyitems below) and a
    started profile serves every check of it in a row.

    One at a time, not all at once: the framework keeps process-wide state bound
    to the event loop of the application that runs (the socket sessions' wake-up
    event, among others), and a process runs one application. Two alive together
    fail at shutdown for a reason no deployment can have.
    """
    from contextlib import ExitStack
    from audit_profiles import by_name, running

    current = {"name": None, "stack": None, "running": None}

    def start(name):
        if current["name"] != name:
            if current["stack"] is not None:
                current["stack"].close()
            stack = ExitStack()
            current.update(name=name, stack=stack, running=stack.enter_context(
                running(by_name(name), tmp_path_factory.mktemp(name), the_other_system)))
        return current["running"]

    yield start
    if current["stack"] is not None:
        current["stack"].close()


@pytest.fixture
def start(applications):
    """A started profile, with its identity provider put back in place.

    The framework's own fixture takes the provider away after every test (an
    application built with one must not hand it to the next); an application
    kept for the run gets it back before each check that uses it.
    """
    from keepup.auth.identity import runtime as identity_runtime

    def get(name):
        running = applications(name)
        identity_runtime.install(running.identity)
        return running

    return get


def pytest_sessionfinish(session, exitstatus):
    target = os.environ.get(REPORT_ENV)
    if not target:
        return
    state = session.config._security_audit
    write_report(Path(target), state["meta"], _results, time.time() - state["started"])

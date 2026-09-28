"""Several roles per user, and what they add up to (keepup-51).

The set is the truth about who this is; `users.role` is a mirror of it for one
release. Panel sections and plugins are glued from every role held, the
administrative right is ADMIN being in the set, and an account that predates the
set reads as the one role its mirror names.

Against the session's throwaway SQLite database, plus one region of the panel
shell executed by a real `node`:

    python3 -m pytest keepup/tests/user_roles_tests.py -v
"""

import itertools
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from keepup import cache, modules
from keepup.auth import dependencies, user_roles
from keepup.db import DatabaseManagerV2
from keepup.factory import create_app
from keepup.plugins import admin as plugins_admin
from keepup.settings import KeepupSettings
from keepup.roles import ROLE_ADMIN, ROLE_CLIENT
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def fresh_caches():
    cache.invalidate_all()
    yield
    cache.invalidate_all()


def make_user(username, role=ROLE_CLIENT):
    """An account with one role, the way the rest of the framework creates one."""
    uid = dependencies.save_user_to_db(username, "a-long-password-51")
    dependencies.update_user(uid, status="active", role=role)
    return uid


def grant(role_name, module_id):
    """Declare a role by granting it a section, which is how a role comes to be."""
    DatabaseManagerV2.execute_commit(
        "INSERT INTO frontend_modules (module_id, name, is_active) "
        "VALUES (:id, :name, TRUE) ON CONFLICT (module_id) DO NOTHING",
        {"id": module_id, "name": module_id})
    DatabaseManagerV2.execute_commit(
        "INSERT INTO role_modules (role_name, module_id, is_active) "
        "VALUES (:role, :id, TRUE) ON CONFLICT (role_name, module_id) DO NOTHING",
        {"role": role_name, "id": module_id})


# --- the set itself ----------------------------------------------------------------

def test_a_user_holds_the_roles_that_were_granted():
    uid = make_user("roles-two")
    grant("MANAGER", "roles-managers-section")
    assert user_roles.set_roles(uid, [ROLE_CLIENT, "MANAGER"]) == ["CLIENT", "MANAGER"]
    assert user_roles.roles_of(uid) == ["CLIENT", "MANAGER"]


def test_a_newly_created_account_holds_its_role_in_the_set():
    uid = dependencies.save_user_to_db("roles-fresh", "a-long-password-51")
    # Not only the mirror: the row is there, so the panel and the gluing read the
    # set rather than the fallback.
    rows = DatabaseManagerV2.execute(
        "SELECT role_name FROM user_roles WHERE user_id = :id", {"id": uid})
    assert [row["role_name"] for row in rows] == [ROLE_CLIENT]


def test_the_set_is_replaced_not_added_to():
    uid = make_user("roles-replaced")
    user_roles.set_roles(uid, [ROLE_ADMIN, ROLE_CLIENT])
    user_roles.set_roles(uid, [ROLE_CLIENT])
    assert user_roles.roles_of(uid) == [ROLE_CLIENT]


def test_a_repeated_role_is_stored_once_and_the_order_does_not_matter():
    uid = make_user("roles-repeats")
    first = user_roles.set_roles(uid, [ROLE_ADMIN, ROLE_CLIENT, ROLE_ADMIN])
    second = user_roles.set_roles(uid, [ROLE_CLIENT, ROLE_ADMIN])
    assert first == second == [ROLE_ADMIN, ROLE_CLIENT]


def test_an_empty_set_is_refused():
    uid = make_user("roles-empty", role=ROLE_ADMIN)
    user_roles.set_roles(uid, [ROLE_ADMIN])
    with pytest.raises(user_roles.EmptyRoleSet):
        user_roles.set_roles(uid, [])
    with pytest.raises(user_roles.EmptyRoleSet):
        user_roles.set_roles(uid, ["  "])
    # Refused means unchanged: the account still holds what it held.
    assert user_roles.roles_of(uid) == [ROLE_ADMIN]


def test_an_undeclared_role_is_refused_and_the_known_ones_are_named():
    uid = make_user("roles-unknown")
    with pytest.raises(user_roles.UnknownRole) as refusal:
        user_roles.set_roles(uid, [ROLE_CLIENT, "WAREHOUSE"])
    assert ROLE_ADMIN in refusal.value.known and ROLE_CLIENT in refusal.value.known
    assert user_roles.roles_of(uid) == [ROLE_CLIENT]


def test_the_frameworks_own_paths_store_a_role_nothing_declares():
    uid = make_user("roles-unchecked")
    # An account created on an external sign-in takes its role from the
    # deployment's policy, and refusing the sign-in because a section grant is
    # missing would be a worse answer than a role that shows no sections.
    assert user_roles.set_roles(uid, ["WAREHOUSE"], checked=False) == ["WAREHOUSE"]
    assert user_roles.roles_of(uid) == ["WAREHOUSE"]
    # Empty is still refused on that path.
    with pytest.raises(user_roles.EmptyRoleSet):
        user_roles.set_roles(uid, [], checked=False)


def test_a_role_written_in_another_case_is_stored_as_it_was_declared():
    uid = make_user("roles-case")
    grant("SUPPORT", "roles-support-section")
    # role_modules is joined by the exact name, so a role kept in another case
    # would look granted in the panel and grant nothing.
    assert user_roles.set_roles(uid, ["support", "admin"]) == [ROLE_ADMIN, "SUPPORT"]


def test_the_known_roles_include_the_frameworks_own_and_the_catalogues():
    grant("AUDITOR", "roles-auditor-section")
    known = user_roles.known_roles()
    assert ROLE_ADMIN in known and ROLE_CLIENT in known and "AUDITOR" in known
    assert known == sorted(known)


# --- the mirror --------------------------------------------------------------------

def mirror_of(uid):
    return DatabaseManagerV2.execute_one(
        "SELECT role FROM users WHERE id = :id", {"id": uid})["role"]


def test_the_mirror_answers_admin_whenever_the_role_is_held():
    uid = make_user("mirror-admin")
    user_roles.set_roles(uid, [ROLE_CLIENT, ROLE_ADMIN])
    # Every reader of the old field compares it with ROLE_ADMIN; the mirror keeps
    # that answer true whichever role was granted first.
    assert mirror_of(uid) == ROLE_ADMIN


def test_the_mirror_is_the_first_role_when_admin_is_not_held():
    uid = make_user("mirror-client")
    grant("MANAGER", "roles-managers-section")
    user_roles.set_roles(uid, ["MANAGER", ROLE_CLIENT])
    assert mirror_of(uid) == ROLE_CLIENT


def test_setting_the_single_role_replaces_the_whole_set():
    uid = make_user("mirror-demoted")
    user_roles.set_roles(uid, [ROLE_ADMIN, ROLE_CLIENT])
    # "Make this person a client" must not leave ADMIN in the set: that would be
    # a right the administrator had just taken away.
    dependencies.update_user(uid, role=ROLE_CLIENT)
    assert user_roles.roles_of(uid) == [ROLE_CLIENT]
    assert mirror_of(uid) == ROLE_CLIENT


def test_an_account_with_no_roles_reads_as_the_one_its_mirror_names():
    uid = make_user("mirror-only", role=ROLE_ADMIN)
    DatabaseManagerV2.execute_commit(
        "DELETE FROM user_roles WHERE user_id = :id", {"id": uid})
    assert user_roles.roles_of(uid) == [ROLE_ADMIN]
    assert user_roles.attach_roles({"id": uid, "role": ROLE_ADMIN})["roles"] == [ROLE_ADMIN]


def test_the_start_gives_every_roleless_account_the_role_its_mirror_names():
    uid = make_user("fill-from-mirror", role=ROLE_ADMIN)
    DatabaseManagerV2.execute_commit(
        "DELETE FROM user_roles WHERE user_id = :id", {"id": uid})

    conn = DatabaseManagerV2.raw_connection()
    cursor = conn.cursor()
    try:
        filled = user_roles.fill_from_mirror(cursor, is_postgres=False)
        conn.commit()
        assert filled >= 1
        # Idempotent: the second start writes nothing.
        assert user_roles.fill_from_mirror(cursor, is_postgres=False) == 0
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    rows = DatabaseManagerV2.execute(
        "SELECT role_name FROM user_roles WHERE user_id = :id", {"id": uid})
    assert [row["role_name"] for row in rows] == [ROLE_ADMIN]


# --- the right ---------------------------------------------------------------------

def signed_in(uid, username):
    """The user as a route receives it, through the real token path."""
    token = dependencies.issue_session_token(uid, username)["access_token"]
    return token


async def test_the_administrative_right_is_admin_being_in_the_set():
    uid = make_user("right-both")
    user_roles.set_roles(uid, [ROLE_CLIENT, ROLE_ADMIN])
    user = await dependencies.get_current_user(signed_in(uid, "right-both"))
    assert user["roles"] == [ROLE_ADMIN, ROLE_CLIENT]
    # ADMIN is not the first role granted, and it is not what a single field
    # would have been -- the right still holds.
    assert await dependencies.get_current_admin(user) is user


async def test_a_user_without_admin_in_the_set_is_refused():
    uid = make_user("right-client")
    user_roles.set_roles(uid, [ROLE_CLIENT])
    user = await dependencies.get_current_user(signed_in(uid, "right-client"))
    with pytest.raises(HTTPException) as refused:
        await dependencies.get_current_admin(user)
    assert refused.value.status_code == 403


async def test_a_user_assembled_without_a_set_is_judged_by_its_single_role():
    # A plugin or a test that builds a user dict of its own is not silently
    # refused: the deprecated field is still an answer.
    assert await dependencies.get_current_admin({"role": ROLE_ADMIN})
    with pytest.raises(HTTPException):
        await dependencies.get_current_admin({"role": ROLE_CLIENT})


def test_the_list_of_users_carries_every_role():
    uid = make_user("right-listed")
    user_roles.set_roles(uid, [ROLE_ADMIN, ROLE_CLIENT])
    listed = {user["id"]: user for user in dependencies.get_all_users()}
    assert listed[uid]["roles"] == [ROLE_ADMIN, ROLE_CLIENT]
    assert dependencies.get_user_by_id(uid)["roles"] == [ROLE_ADMIN, ROLE_CLIENT]


# --- the gluing --------------------------------------------------------------------

def test_sections_of_every_role_are_glued_and_a_shared_one_appears_once():
    grant(ROLE_ADMIN, "glue-admin-only")
    grant(ROLE_ADMIN, "glue-shared")
    grant(ROLE_CLIENT, "glue-shared")
    grant(ROLE_CLIENT, "glue-client-only")

    glued = modules.get_modules_for_roles([ROLE_ADMIN, ROLE_CLIENT])
    ids = [module["module_id"] for module in glued]
    assert ids.count("glue-shared") == 1
    assert {"glue-admin-only", "glue-client-only", "glue-shared"} <= set(ids)
    # The menu must not rearrange itself because a role was granted later.
    assert ids == [module["module_id"]
                   for module in modules.get_modules_for_roles([ROLE_CLIENT, ROLE_ADMIN])]


def test_one_role_still_gets_exactly_its_own_sections():
    grant(ROLE_ADMIN, "glue-admin-only")
    grant(ROLE_CLIENT, "glue-client-only")
    ids = [module["module_id"] for module in modules.get_modules_for_roles([ROLE_CLIENT])]
    assert "glue-client-only" in ids and "glue-admin-only" not in ids


def test_the_roles_of_a_user_fall_back_to_the_single_field():
    assert user_roles.held_by({"roles": ["A", "B"]}) == ["A", "B"]
    assert user_roles.held_by({"role": ROLE_CLIENT}) == [ROLE_CLIENT]
    assert user_roles.held_by({}) == [] and user_roles.held_by(None) == []


async def test_the_catalogue_fallback_glues_the_roles_of_the_file(tmp_path, monkeypatch):
    catalogue = tmp_path / "modules.json"
    catalogue.write_text(json.dumps({
        "modules": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        "roles": [
            {"name": ROLE_ADMIN, "modules": ["a", "b"]},
            {"name": ROLE_CLIENT, "modules": ["b", "c"]},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(modules, "MODULES_CONFIG_PATH", str(catalogue))

    both = await modules.get_modules_from_json_fallback(
        {"roles": [ROLE_ADMIN, ROLE_CLIENT]})
    assert [module["id"] for module in both["modules"]] == ["a", "b", "c"]

    # A role the file does not describe adds nothing, and describes nobody's
    # sections when it is the only one.
    partly = await modules.get_modules_from_json_fallback({"roles": [ROLE_CLIENT, "AUDITOR"]})
    assert [module["id"] for module in partly["modules"]] == ["b", "c"]
    unknown = await modules.get_modules_from_json_fallback({"roles": ["AUDITOR"]})
    assert unknown["modules"] == []


class OnePlugin:
    """A plugin the manager admits to having."""


class Manager:
    def __init__(self, running):
        self.running = running

    def get_plugin(self, plugin_id):
        return OnePlugin() if plugin_id in self.running else None


async def test_plugins_of_every_role_are_glued_and_one_that_is_down_is_not_offered(
        tmp_path, monkeypatch):
    catalogue = tmp_path / "modules.json"
    catalogue.write_text(json.dumps({
        "plugins": [{"id": "p1", "name": "P1"}, {"id": "p2", "name": "P2"},
                    {"id": "p3", "name": "P3"}],
        "roles": [
            {"name": ROLE_ADMIN, "plugins": ["p1", "p2"]},
            {"name": ROLE_CLIENT, "plugins": ["p2", "p3"]},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(plugins_admin, "MODULES_CONFIG_PATH", str(catalogue))

    manager = Manager({"p1", "p2", "p3"})
    offered = await plugins_admin.get_plugins(manager, {"roles": [ROLE_ADMIN, ROLE_CLIENT]})
    assert [plugin["id"] for plugin in offered["plugins"]] == ["p1", "p2", "p3"]

    # Granted to a role of theirs, but it never came up: nothing to advertise.
    down = await plugins_admin.get_plugins(Manager({"p1"}),
                                           {"roles": [ROLE_ADMIN, ROLE_CLIENT]})
    assert [plugin["id"] for plugin in down["plugins"]] == ["p1"]

    nobody = await plugins_admin.get_plugins(manager, {"roles": ["AUDITOR"]})
    assert nobody["plugins"] == []


# --- the routes an administrator uses ----------------------------------------------

class NoPlugins:
    """A plugin manager holding nothing, which is all these routes need of one."""

    plugins = {}

    def get_plugin(self, plugin_id):
        return None

    async def initialize_all(self, *args, **kwargs):
        return {}

    async def cleanup_all(self):
        return None


@pytest.fixture
def client():
    """The framework's own application, with nothing of an application on disk.

    A plugin manager has to be there or the authentication routes are not
    registered at all (keepup/factory.py).
    """
    app = create_app(KeepupSettings(title="Framework Only", static_mounts=(),
                                    plugin_manager=NoPlugins()))
    with TestClient(app, raise_server_exceptions=False) as running:
        yield running


#: Usernames are unique, and each test in this file wants its own accounts.
_sequence = itertools.count()


def as_admin():
    """An administrator's bearer header, on an account of its own."""
    username = f"routes-admin-{next(_sequence)}"
    uid = make_user(username, role=ROLE_ADMIN)
    user_roles.set_roles(uid, [ROLE_ADMIN])
    return {"Authorization": f"Bearer {signed_in(uid, username)}"}


def test_an_administrator_reads_and_replaces_the_set(client):
    target = make_user("routes-target")
    headers = as_admin()

    read = client.get(f"/api/admin/users/{target}/roles", headers=headers)
    assert read.status_code == 200
    assert read.json()["roles"] == [ROLE_CLIENT]
    assert ROLE_ADMIN in read.json()["known_roles"]

    written = client.put(f"/api/admin/users/{target}/roles", headers=headers,
                         json={"roles": [ROLE_CLIENT, ROLE_ADMIN]})
    assert written.status_code == 200
    assert written.json()["roles"] == [ROLE_ADMIN, ROLE_CLIENT]
    assert client.get(f"/api/admin/users/{target}/roles",
                      headers=headers).json()["roles"] == [ROLE_ADMIN, ROLE_CLIENT]


def test_an_empty_set_and_an_undeclared_role_are_refused_with_400(client):
    target = make_user("routes-refused")
    headers = as_admin()

    empty = client.put(f"/api/admin/users/{target}/roles", headers=headers,
                       json={"roles": []})
    assert empty.status_code == 400 and "block the account" in empty.json()["detail"]

    unknown = client.put(f"/api/admin/users/{target}/roles", headers=headers,
                         json={"roles": ["WAREHOUSE"]})
    assert unknown.status_code == 400 and ROLE_CLIENT in unknown.json()["detail"]
    assert user_roles.roles_of(target) == [ROLE_CLIENT]


def test_a_missing_user_is_404_and_a_client_is_refused(client):
    headers = as_admin()
    assert client.get("/api/admin/users/9999999/roles", headers=headers).status_code == 404
    assert client.put("/api/admin/users/9999999/roles", headers=headers,
                      json={"roles": [ROLE_CLIENT]}).status_code == 404

    plain = make_user("routes-client")
    user_roles.set_roles(plain, [ROLE_CLIENT])
    as_client = {"Authorization": f"Bearer {signed_in(plain, 'routes-client')}"}
    assert client.get(f"/api/admin/users/{plain}/roles",
                      headers=as_client).status_code == 403
    assert client.put(f"/api/admin/users/{plain}/roles", headers=as_client,
                      json={"roles": [ROLE_ADMIN]}).status_code == 403


def test_the_current_user_is_told_its_whole_set(client):
    uid = make_user("routes-me", role=ROLE_ADMIN)
    user_roles.set_roles(uid, [ROLE_ADMIN, ROLE_CLIENT])
    answer = client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {signed_in(uid, 'routes-me')}"})
    assert answer.status_code == 200
    assert answer.json()["roles"] == [ROLE_ADMIN, ROLE_CLIENT]
    # The mirror is still there for the applications that read it.
    assert answer.json()["role"] == ROLE_ADMIN


# --- the panel shell ---------------------------------------------------------------
#
# The region of main_new.js that answers what roles a user holds, cut out by its
# markers and executed by a real `node`: what the panel decides cannot be read off
# the source.

SHELL = Path(__file__).resolve().parents[1] / "static" / "js" / "main_new.js"
ROLES_START = "// --- The roles a user holds"
ROLES_END = "// --- end of the roles a user holds"
NODE = shutil.which("node")

PRELUDE = r"""
const ROLE_ADMIN = 'ADMIN';
const ROLE_CLIENT = 'CLIENT';
let currentUser = null;
global.window = {};
const vm = require('vm');
vm.runInThisContext(process.env.REGION, { filename: 'main_new.js' });
"""


def shell_region() -> str:
    source = SHELL.read_text(encoding="utf-8")
    return source[source.index(ROLES_START):source.index(ROLES_END)]


def run_in_node(script: str):
    result = subprocess.run([NODE, "-e", PRELUDE + script], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "REGION": shell_region()})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(NODE is None, reason="needs node: the shell is JavaScript")
def test_the_shell_decides_by_the_set_and_falls_back_to_the_single_role():
    answered = run_in_node("""
    const set = {roles: ['CLIENT', 'ADMIN']};
    const mirrored = {role: 'ADMIN'};
    const client = {roles: ['CLIENT']};
    console.log(JSON.stringify([
        userRolesOf(set), userHasRole(set, ROLE_ADMIN), userHasRole(client, ROLE_ADMIN),
        userHasRole(mirrored, ROLE_ADMIN), userRolesOf(null),
        window.AppRoles.has(set, 'CLIENT'),
    ]));
    """)
    assert answered == [["CLIENT", "ADMIN"], True, False, True, [], True]


@pytest.mark.skipif(NODE is None, reason="needs node: the shell is JavaScript")
def test_the_admin_check_of_the_shell_reads_the_current_users_set():
    answered = run_in_node("""
    currentUser = {roles: ['CLIENT', 'ADMIN']}; const both = currentUserIsAdmin();
    currentUser = {roles: ['CLIENT']}; const client = currentUserIsAdmin();
    currentUser = null;
    console.log(JSON.stringify([both, client, currentUserIsAdmin()]));
    """)
    assert answered == [True, False, False]

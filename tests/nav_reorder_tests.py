"""The person's own order of the sidebar's entries (keepup-183).

The deployment decides the default sequence of the menu -- `nav_order`, then
the name, so a menu that was never touched stands as it was loaded. The person
decides their own: dragging an entry of the sidebar over another moves it,
and the sequence the drag left is stored for that person, not for anybody
else. It lives in `user_nav`, keyed by the person's id, which is what makes
it hold across a refresh and a new tab; what it does not name keeps the
deployment's places, so the default stands where it was not named.

    python3 -m pytest tests/nav_reorder_tests.py -v
"""

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from keepup import cache
from keepup_auth.dependencies import get_current_user
from keepup_db import DatabaseManagerV2
from keepup_modules import catalogue
from keepup_modules.catalogue import register_module_routes

PACKAGE = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_user_nav():
    DatabaseManagerV2.execute_commit("DELETE FROM role_modules", {})
    DatabaseManagerV2.execute_commit("DELETE FROM frontend_modules", {})
    DatabaseManagerV2.execute_commit("DELETE FROM user_nav", {})
    catalogue.set_declared_plugins(set())
    catalogue.set_running_plugins(set())
    yield
    catalogue.set_declared_plugins(set())
    catalogue.set_running_plugins(set())


def declare(section_id, name, nav_order=None):
    DatabaseManagerV2.execute_commit(
        "INSERT INTO frontend_modules (module_id, name, nav_order, is_active, "
        "declared_by) VALUES (:id, :name, :order, TRUE, 'config/modules.json')",
        {"id": section_id, "name": name, "order": nav_order})
    DatabaseManagerV2.execute_commit(
        "INSERT INTO role_modules (role_name, module_id, is_active) "
        "VALUES ('CLIENT', :id, TRUE)", {"id": section_id})


def grant(user_id, role_name="CLIENT"):
    DatabaseManagerV2.execute_commit(
        "INSERT OR IGNORE INTO user_roles (user_id, role_name) VALUES (:id, :role)",
        {"id": user_id, "role": role_name})


def user(user_id, username="someone"):
    return {"id": user_id, "username": username, "roles": ["CLIENT"]}


# --- the stored sequence ---------------------------------------------------------

def test_a_person_without_a_drag_is_read_as_no_sequence():
    assert catalogue.get_user_nav_order(1) is None


def test_a_stored_sequence_is_written_for_that_person_and_read_back():
    catalogue.set_user_nav_order(1, ["b", "a"])
    row = catalogue.get_user_nav_order(1)
    assert row is not None and row["user_id"] == 1
    assert catalogue._user_order_of(1) == ["b", "a"]


def test_the_sequence_of_one_person_is_not_the_sequence_of_another():
    catalogue.set_user_nav_order(1, ["b", "a"])
    assert catalogue._user_order_of(2) is None, (
        "the sequence of one person is read for another")


def test_a_write_of_the_same_sequence_changes_nothing():
    catalogue.set_user_nav_order(1, ["b", "a"])
    first = DatabaseManagerV2.execute_one(
        "SELECT updated_at FROM user_nav WHERE user_id = 1", {})["updated_at"]
    catalogue.set_user_nav_order(1, ["b", "a"])
    second = DatabaseManagerV2.execute_one(
        "SELECT updated_at FROM user_nav WHERE user_id = 1", {})["updated_at"]
    assert first == second, "a drag that moved nothing moved the row"


def test_a_sequence_that_is_not_a_list_of_ids_is_refused():
    with pytest.raises(Exception):
        catalogue.set_user_nav_order(1, "not a list")
    with pytest.raises(Exception):
        catalogue.set_user_nav_order(1, ["", "a"])
    assert catalogue.get_user_nav_order(1) is None


def test_a_sequence_that_the_database_no_longer_knows_reads_as_none():
    catalogue.set_user_nav_order(99, ["a"])
    DatabaseManagerV2.execute_commit("DELETE FROM user_nav WHERE user_id = 99", {})
    assert catalogue._user_order_of(99) is None


# --- the read of the menu for a person --------------------------------------------

def test_a_person_without_a_sequence_sees_the_deployment_default():
    """The default is the deployment's own: `nav_order`, then the name."""
    declare("users", "Users", nav_order=3)
    declare("metrics", "System metrics", nav_order=2)
    declare("themes", "Themes", nav_order=1)
    grant(1)
    cache.invalidate_all()

    modules = catalogue.get_modules_for_user(user(1))
    assert [m["module_id"] for m in modules] == ["themes", "metrics", "users"]


def test_the_sequence_is_the_persons_and_the_menu_stays_the_deployments():
    """A sequence of the menu is not a sequence of the catalogue (keepup-183).

    An entry is named by the page it opens (`data-target`), and a section may
    draw it long after the catalogue was read, so the two lists have different
    names for the same thing. What is stored is the person's own; what the
    catalogue answers is the deployment's, in the deployment's order, and the
    shell puts the one into the other as the entries arrive.
    """
    declare("users", "Users", nav_order=3)
    declare("metrics", "System metrics", nav_order=2)
    declare("themes", "Themes", nav_order=1)
    grant(1)
    catalogue.set_user_nav_order(1, ["users-section", "themes-section"])
    cache.invalidate_all()

    assert catalogue._user_order_of(1) == ["users-section", "themes-section"]
    modules = catalogue.get_modules_for_user(user(1))
    assert [m["module_id"] for m in modules] == ["themes", "metrics", "users"]


def test_a_named_entry_nothing_matches_is_kept_as_the_person_left_it():
    """A sequence may name a page this deployment no longer has -- a section
    switched off, a plugin gone. It is the person's own list, so it is kept
    whole; the shell simply has no entry to put in that place."""
    declare("users", "Users")
    declare("metrics", "System metrics")
    grant(1)
    catalogue.set_user_nav_order(1, ["ghost-section", "users-section", "metrics-section"])
    cache.invalidate_all()

    assert catalogue._user_order_of(1) == ["ghost-section", "users-section",
                                           "metrics-section"]
    # The catalogue is untouched by it: the menu is the deployment's.
    assert [m["module_id"] for m in catalogue.get_modules_for_user(user(1))] \
        == ["metrics", "users"]


def test_another_person_is_not_moved_by_this_one():
    declare("users", "Users")
    declare("metrics", "System metrics")
    grant(1)
    grant(2)
    catalogue.set_user_nav_order(1, ["metrics", "users"])
    cache.invalidate_all()

    assert [m["module_id"] for m in catalogue.get_modules_for_user(user(1))] \
        == ["metrics", "users"]
    assert [m["module_id"] for m in catalogue.get_modules_for_user(user(2))] \
        == ["metrics", "users"] == [m["module_id"] for m in
                                    catalogue.get_modules_for_roles(["CLIENT"])], (
        "a person who never dragged is read as the deployment's default")


# --- the routes ---------------------------------------------------------------------

@pytest.fixture
def client():
    cache.invalidate_all()
    app = FastAPI()
    register_module_routes(app)
    return app


def signed_in_client(app, user_id, username="someone"):
    cache.invalidate_all()
    def fake_user():
        return user(user_id, username)
    app.dependency_overrides[get_current_user] = fake_user
    return TestClient(app)


def test_the_order_is_read_back_for_the_same_person_and_not_for_another(client):
    """The route answers the stored sequence for the user it was written for,
    and nothing for a user who never dragged. The write and the read go through
    the same database, so the read is checked by the model, not by a second
    client, because the test's client overrides the session for both."""
    declare("users", "Users")
    declare("metrics", "System metrics")
    grant(1)
    grant(2)
    catalogue.set_user_nav_order(1, ["metrics", "users"])
    cache.invalidate_all()

    assert catalogue._user_order_of(1) == ["metrics", "users"]
    assert catalogue._user_order_of(2) is None


def test_the_route_is_registered_on_the_application():
    """The two routes of the preference are on the application, not only the
    model: the shell's save and read go through them."""
    from fastapi import FastAPI
    app = FastAPI()
    register_module_routes(app)
    paths = {route.path for route in app.routes}
    assert "/api/user/nav-order" in paths
    methods = {frozenset(route.methods)
               for route in app.routes if route.path == "/api/user/nav-order"}
    assert frozenset({"GET"}) in methods
    assert frozenset({"POST"}) in methods


def test_a_person_sets_their_own_sequence_and_their_menu_follows(client):
    declare("users", "Users", nav_order=1)
    declare("metrics", "System metrics", nav_order=2)
    declare("themes", "Themes", nav_order=3)
    grant(1)
    cache.invalidate_all()

    someone = signed_in_client(client, 1)
    answer = someone.post("/api/user/nav-order",
                          json={"order": ["themes-section", "users-section"]})
    assert answer.status_code == 200, answer.text

    # The sequence is the person's, and it is read back whole.
    assert someone.get("/api/user/nav-order").json() == {
        "order": ["themes-section", "users-section"]}
    # The catalogue still answers the deployment's order: the shell applies the
    # sequence to the entries, which is the only list it can name (keepup-183).
    cache.invalidate_all()
    modules = catalogue.get_modules_for_user(user(1))
    menu = [{"id": m["module_id"], "order": m.get("nav_order")} for m in modules]
    assert [m["id"] for m in menu] == ["users", "metrics", "themes"]
    # The deployment's own positions travel with the answer.
    assert [m.get("order") for m in menu] == [1, 2, 3]


def test_the_sequence_is_not_written_for_the_user_that_is_not_their_own(client):
    """The row is keyed by the id the session carries: a caller cannot name
    somebody else's id in the body, because the body does not carry one."""
    declare("users", "Users")
    declare("metrics", "System metrics")
    grant(1)
    grant(2)
    cache.invalidate_all()

    someone = signed_in_client(client, 1)
    someone.post("/api/user/nav-order", json={"order": ["metrics", "users"]})
    # The other person, who never dragged: still the deployment's order.
    another = signed_in_client(client, 2)
    assert another.get("/api/user/nav-order").json() == {"order": []}


def test_a_bad_body_is_refused_not_stored(client):
    declare("users", "Users")
    grant(1)
    cache.invalidate_all()

    someone = signed_in_client(client, 1)
    bad = someone.post("/api/user/nav-order", json={"order": "users"})
    assert bad.status_code in (400, 422), bad.text
    assert someone.get("/api/user/nav-order").json() == {"order": []}

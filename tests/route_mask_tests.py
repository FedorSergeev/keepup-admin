"""The request mask a route declares, and what the framework does with it.

Task keepup-16. Before it, the source of truth about what a route accepts was
the handler's signature, so every argument with a default was public and
nothing outside the process could know what a route takes.

The deciding part is a module without FastAPI, so most of this file calls it
directly rather than starting a server.

Run by path, like the other *_tests.py files:

    python3 -m pytest keepup/tests/route_mask_tests.py -v
"""

import pytest
from fastapi import HTTPException

from keepup.plugins import route_mask
from keepup.plugins.registry import admit_params


def mask_of(declaration, path=""):
    return route_mask.parse(declaration, path)


# --- the default does not change ---------------------------------------------

async def handler_with_a_default(current_user, order_id, as_user=None):
    return {"order_id": order_id, "as_user": as_user}


def test_a_route_without_a_mask_behaves_exactly_as_before():
    """The condition of the change: four hundred routes are converted later."""
    assert route_mask.parse(None, "/api/orders") is None

    passed = admit_params(handler_with_a_default, None, {"order_id": "5", "as_user": "7"})

    # Signature-based, and the value still arrives as the string it was.
    assert passed == {"order_id": "5", "as_user": "7"}


def test_a_route_without_a_mask_still_refuses_what_it_does_not_take():
    with pytest.raises(HTTPException) as error:
        admit_params(handler_with_a_default, None, {"order_id": "5", "nosuch": "1"})

    assert error.value.status_code == 400


# --- an argument can now be kept internal ------------------------------------

def test_an_argument_with_a_default_is_no_longer_public_once_a_mask_exists():
    """The whole reason for the task.

    ``as_user`` has a default, so before the mask a client could set it by
    spelling it in the query string; the plugin author had no way to say no.
    """
    mask = mask_of({"order_id": {"type": "int", "required": True}})

    with pytest.raises(HTTPException) as error:
        admit_params(handler_with_a_default, mask, {"order_id": "5", "as_user": "7"})

    assert error.value.status_code == 400
    assert "as_user" in error.value.detail


# --- values arrive converted and checked -------------------------------------

def test_a_number_reaches_the_handler_as_a_number():
    mask = mask_of({"limit": {"type": "int"}})

    assert mask.admit({"limit": "50"}) == ({"limit": 50}, [])


def test_a_flag_is_read_by_its_spelling_not_by_bool():
    """``bool("false")`` is true, which is how a query string says yes to no."""
    mask = mask_of({"deep": {"type": "bool"}})

    assert mask.admit({"deep": "false"})[0] == {"deep": False}
    assert mask.admit({"deep": "0"})[0] == {"deep": False}
    assert mask.admit({"deep": ""})[0] == {"deep": False}
    assert mask.admit({"deep": "true"})[0] == {"deep": True}


def test_a_flag_is_not_a_number():
    mask = mask_of({"limit": {"type": "int"}})

    values, complaints = mask.admit({"limit": True})

    assert values == {} and complaints


def test_a_value_of_the_wrong_type_is_refused_by_name():
    mask = mask_of({"limit": {"type": "int"}})

    values, complaints = mask.admit({"limit": "however many"})

    assert values == {}
    assert complaints == ["limit: expected int"]


def test_every_complaint_is_reported_not_the_first():
    """A client told one problem per request fixes its call once per mistake."""
    mask = mask_of({"limit": {"type": "int"}, "status": {"type": "str", "choices": ["active"]}})

    _, complaints = mask.admit({"limit": "x", "status": "gone", "nosuch": "1"})

    assert len(complaints) == 3


def test_a_missing_required_parameter_is_named():
    mask = mask_of({"shop_id": {"type": "int", "required": True}})

    _, complaints = mask.admit({})

    assert complaints == ["shop_id: required parameter is missing"]


def test_an_absent_optional_parameter_is_simply_absent():
    mask = mask_of({"search": {"type": "str"}})

    assert mask.admit({}) == ({}, [])


@pytest.mark.parametrize("declaration, value", [
    ({"type": "str", "choices": ["active", "archived"]}, "gone"),
    ({"type": "int", "min": 1}, "0"),
    ({"type": "int", "max": 200}, "201"),
    ({"type": "str", "max_length": 3}, "far too long"),
    ({"type": "str", "pattern": r"[a-z]+"}, "ABC"),
])
def test_a_value_outside_what_is_allowed_is_refused(declaration, value):
    mask = mask_of({"p": declaration})

    values, complaints = mask.admit({"p": value})

    assert values == {} and len(complaints) == 1


def test_a_pattern_matches_the_whole_value():
    """A pattern that matches a substring is a restriction nobody gets."""
    mask = mask_of({"code": {"type": "str", "pattern": r"[a-z]+"}})

    assert mask.admit({"code": "abc"})[0] == {"code": "abc"}
    assert mask.admit({"code": "abc-and-more"})[1]


def test_the_framework_supplies_its_own_and_the_mask_does_not_complain():
    mask = mask_of({"order_id": {"type": "int"}})

    _, complaints = mask.admit({"order_id": "5", "current_user": {"id": 1}})

    assert complaints == []


# --- a path parameter ---------------------------------------------------------

def test_a_path_parameter_is_required_without_saying_so():
    mask = mask_of({"shop_id": {"type": "int"}}, "/api/shops/{shop_id}")

    assert mask.parameters["shop_id"].required
    assert mask.parameters["shop_id"].source == route_mask.IN_PATH


def test_a_mask_that_forgets_a_parameter_of_its_own_path_is_refused():
    with pytest.raises(route_mask.MaskError):
        mask_of({}, "/api/shops/{shop_id}")


def test_a_path_parameter_declared_as_a_query_one_is_refused():
    with pytest.raises(route_mask.MaskError):
        mask_of({"shop_id": {"type": "int", "in": "query"}}, "/api/shops/{shop_id}")


# --- a wrong declaration stops the start, not a request ----------------------

@pytest.mark.parametrize("declaration", [
    {"p": {"type": "number"}},
    {"p": {"typo": "str"}},
    {"p": {"type": "str", "min": 1}},
    {"p": {"type": "int", "max_length": 3}},
    {"p": {"type": "int", "choices": ["not a number"]}},
    {"p": {"type": "int", "min": 10, "max": 1}},
    {"p": {"type": "str", "pattern": "["}},
    {"p": {"type": "str", "choices": []}},
    {"p": "a string where a declaration belongs"},
])
def test_a_declaration_that_does_not_make_sense_is_refused(declaration):
    with pytest.raises(route_mask.MaskError):
        mask_of(declaration)


def test_a_mask_naming_what_the_handler_does_not_take_is_refused():
    mask = mask_of({"nosuch": {"type": "str"}})

    with pytest.raises(route_mask.MaskError) as error:
        route_mask.check_signature(handler_with_a_default, mask)

    assert "nosuch" in str(error.value)


async def handler_taking_anything(current_user, **kwargs):
    return kwargs


def test_a_handler_that_asks_for_everything_accepts_any_name():
    mask = mask_of({"whatever": {"type": "str"}})

    route_mask.check_signature(handler_taking_anything, mask)


class Uninspectable:
    """A callable whose signature cannot be read -- a C extension, say."""

    def __call__(self, *args, **kwargs):
        return None

    @property
    def __signature__(self):
        raise ValueError("no signature")


def test_a_handler_that_cannot_be_inspected_is_left_alone():
    mask = mask_of({"whatever": {"type": "str"}})

    # Refusing a plugin over introspection would be worse than not checking.
    route_mask.check_signature(Uninspectable(), mask)


# --- the machine-readable export ---------------------------------------------

def test_the_export_carries_what_a_proxy_needs():
    routes = [{
        "path": "/api/shops/{shop_id}/offers",
        "methods": ["GET"],
        "handler": handler_taking_anything,
        "params": {
            "shop_id": {"type": "int"},
            "status": {"type": "str", "choices": ["active", "archived"]},
        },
    }]

    described = route_mask.describe_routes(routes)

    assert described[0]["path"] == "/api/shops/{shop_id}/offers"
    assert described[0]["methods"] == ["GET"]
    assert described[0]["declared"] is True
    assert described[0]["params"]["shop_id"] == {"type": "int", "required": True, "in": "path"}
    assert described[0]["params"]["status"]["choices"] == ["active", "archived"]


def test_the_export_says_nothing_about_the_handler():
    routes = [{"path": "/api/x", "methods": ["GET"], "handler": handler_taking_anything,
               "params": {"a": {"type": "str"}}}]

    described = route_mask.describe_routes(routes)

    assert "handler" not in described[0]
    assert "handler_taking_anything" not in str(described)


def test_a_route_without_a_mask_is_listed_as_having_declared_none():
    """"We do not know" and "anything goes" are different answers."""
    routes = [{"path": "/api/x", "methods": ["GET"], "handler": handler_taking_anything}]

    described = route_mask.describe_routes(routes)

    assert described[0]["declared"] is False
    assert described[0]["params"] == {}

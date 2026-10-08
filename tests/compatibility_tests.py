"""Names that moved keep working, and say where they went.

Task keepup-114. 0.4.0 moves code into the distributions beside the base package
(keepup-124), and an application that imports an old name must keep working for
one release -- while being told, because a silent shim is how an application ends
up pinned to a release it cannot leave. This is the map, the warning and the
stand-in, checked on a name that has moved and on the ones that will.
"""

import sys
import types
import warnings

import pytest

from keepup import compat


def test_every_name_that_moves_has_a_destination():
    """The map is the promise: no old name without somewhere it went."""
    assert compat.MOVED_NAMES
    for old, new in compat.MOVED.items():
        assert old.startswith("keepup.")
        assert new.startswith("keepup_")
        assert compat.destination_of(old) == new


def test_a_name_below_a_moved_one_maps_under_it():
    """A module that moves takes what is under it with it."""
    assert compat.destination_of("keepup.auth.user_roles") == "keepup_users.roles"
    assert compat.destination_of("keepup.not_moved") is None


def test_a_stand_in_answers_like_the_module_it_stands_for():
    """An application keeps working: the names it imported are all there."""
    created = types.ModuleType("keepup_fake_target")
    created.answer = 42
    sys.modules["keepup_fake_target"] = created
    try:
        stand_in = compat.shim("keepup.fake", "keepup_fake_target")
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            assert stand_in.answer == 42
        assert any("moved to keepup_fake_target" in str(item.message) for item in seen)
    finally:
        sys.modules.pop("keepup_fake_target", None)


def test_the_warning_is_said_once_per_process():
    """A log that repeats itself on every import is a log nobody reads."""
    compat._said.clear()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        compat.warn_moved("keepup.said.once", "keepup_said_once")
        compat.warn_moved("keepup.said.once", "keepup_said_once")
    assert len(seen) == 1
    assert issubclass(seen[0].category, DeprecationWarning)


def test_a_missing_home_names_the_distribution_to_install():
    """The one thing an application can act on: which package to install."""
    stand_in = compat.shim("keepup.moved.away", "keepup_nothing_here")
    with pytest.raises(ModuleNotFoundError) as missing:
        stand_in.anything
    assert "install keepup-nothing-here" in str(missing.value)


def test_a_name_that_did_not_move_needs_no_stand_in():
    """The layer does not invent history for a module that is where it was."""
    with pytest.raises(KeyError):
        compat.shim("keepup.never_moved")


def test_nothing_is_stood_in_for_while_the_module_is_here():
    """In 0.4.0 the code is still in the base package: the map waits."""
    installed = compat.install()
    assert installed == {}
    assert compat.installed() == {}

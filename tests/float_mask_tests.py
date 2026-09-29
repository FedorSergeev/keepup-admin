"""A float parameter of a request mask is a finite number (keepup-78).

`float()` reads "nan" and "inf" as well, and NaN passed the mask's bounds:
every comparison with it is false, so neither "less than min" nor "greater
than max" ever fired.

    python3 -m pytest keepup/tests/float_mask_tests.py -v
"""

import pytest

from keepup.plugins.route_mask import Parameter


def bounded():
    return Parameter("share", {"type": "float", "min": 0, "max": 1}, ())


@pytest.mark.parametrize("value", ["nan", "NaN", " nan ", "inf", "-inf", "Infinity", "1e999"])
def test_what_is_not_a_finite_number_is_refused(value):
    admitted, complaint = bounded().admit(value)
    assert admitted is None
    assert complaint == "share: expected float"


@pytest.mark.parametrize("value, expected", [("0", 0.0), ("0.25", 0.25), ("1", 1.0)])
def test_a_number_inside_the_bounds_is_admitted(value, expected):
    assert bounded().admit(value) == (expected, None)


def test_the_bounds_still_hold():
    assert bounded().admit("1.5")[1] == "share: greater than 1"
    assert bounded().admit("-0.1")[1] == "share: less than 0"


def test_an_unbounded_float_refuses_them_too():
    assert Parameter("x", {"type": "float"}, ()).admit("nan")[0] is None

"""Regression tests for the Garmin 401 -> GarminAuthExpired translation.

The refresh path is only reachable if `_raise_if_auth_expired` recognises garth's
error shape. It didn't: garth wraps requests.HTTPError in a GarthHTTPError dataclass
whose `.error` holds the original, so reading `.response` off the outer exception
always yielded None and the entire refresh path was dead code. These tests pin the
real exception type rather than a hand-rolled stand-in, so a garth upgrade that
changes the shape fails here instead of in production.
"""

import pytest
from garth.exc import GarthHTTPError
from requests import HTTPError, Response

from garmin import GarminAuthExpired, _raise_if_auth_expired


def _garth_error(status_code: int) -> GarthHTTPError:
    resp = Response()
    resp.status_code = status_code
    return GarthHTTPError(msg="Error in request", error=HTTPError(response=resp))


def test_garth_401_becomes_auth_expired():
    with pytest.raises(GarminAuthExpired):
        _raise_if_auth_expired(_garth_error(401))


def test_garth_non_401_passes_through():
    _raise_if_auth_expired(_garth_error(500))  # returns; caller re-raises the original


def test_plain_http_error_401_also_recognised():
    """Not every call site goes through garth's wrapper."""
    resp = Response()
    resp.status_code = 401
    with pytest.raises(GarminAuthExpired):
        _raise_if_auth_expired(HTTPError(response=resp))


def test_error_merely_mentioning_401_is_not_auth_expired():
    """The string-matching approach this replaced fired on a workout named '401 repeats'."""
    _raise_if_auth_expired(_garth_error(500))
    _raise_if_auth_expired(RuntimeError("Garmin 500: workout '401 repeats' rejected"))

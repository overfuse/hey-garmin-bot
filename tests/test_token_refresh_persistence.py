"""Regression tests for the discarded-refresh bug (plan item B).

upload_garmin_payload used to return only the workoutId; garth's internal OAuth2
refresh died with the per-call client, so one hour after login every upload paid
a refresh round-trip forever. The tuple return lets the refreshed token escape.
"""

import pytest

import garmin


class FakeGarthClient:
    """Stands in for garth.http.Client: loads a token, uploads, dumps a token."""

    dumps_value = None  # class attr set per-test; instances are created inside garmin

    def __init__(self):
        self.loaded = None

    def loads(self, token):
        self.loaded = token

    def connectapi(self, path, method, json):
        return {"workoutId": "wk-123"}

    def dumps(self):
        return self.dumps_value if self.dumps_value is not None else self.loaded


@pytest.fixture
def fake_garth(monkeypatch):
    monkeypatch.setattr(garmin, "GarthClient", FakeGarthClient)
    monkeypatch.setattr(garmin, "_install_garth_proxy", lambda client: None)
    FakeGarthClient.dumps_value = None
    return FakeGarthClient


def test_unchanged_token_reports_no_refresh(fake_garth):
    workout_id, refreshed = garmin.upload_garmin_payload("tok-a", {"name": "x"})
    assert workout_id == "wk-123"
    assert refreshed is None


def test_refreshed_token_escapes_the_client(fake_garth):
    fake_garth.dumps_value = "tok-b"
    workout_id, refreshed = garmin.upload_garmin_payload("tok-a", {"name": "x"})
    assert workout_id == "wk-123"
    assert refreshed == "tok-b"


@pytest.mark.asyncio
async def test_upload_parsed_workout_propagates_the_tuple(fake_garth, monkeypatch):
    monkeypatch.setattr(garmin, "convert", lambda wj: wj)
    fake_garth.dumps_value = "tok-b"
    workout_id, refreshed = await garmin.upload_parsed_workout("tok-a", {"name": "x"})
    assert (workout_id, refreshed) == ("wk-123", "tok-b")

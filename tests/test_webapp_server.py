"""The Mini App HTTP surface: auth enforcement, prefs round-trip, strict PUT.

Mongo is faked by monkeypatching the two user-module functions the handlers
call; initData comes from test_tg_init_data.make_init_data, so requests are
authenticated the same way real Telegram launches are.
"""

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from test_tg_init_data import BOT_TOKEN, make_init_data

import webapp_server
from prefs import DEFAULTS
from webapp_server import create_app

AUTH = {"Authorization": "tma " + make_init_data(user_id=42)}


@pytest.fixture
def users(monkeypatch):
    """In-memory stand-in for the users collection, keyed by telegram id."""
    store: dict[int, dict] = {}

    async def fake_get_user(uid):
        return store.get(uid)

    async def fake_set_prefs(uid, prefs):
        store.setdefault(uid, {"telegram_id": uid})["prefs"] = prefs

    monkeypatch.setattr(webapp_server.user, "get_user", fake_get_user)
    monkeypatch.setattr(webapp_server.user, "set_prefs", fake_set_prefs)
    return store


@pytest_asyncio.fixture
async def client():
    async with TestClient(TestServer(create_app(bot_token=BOT_TOKEN))) as c:
        yield c


@pytest.mark.asyncio
async def test_page_served_with_security_headers(client):
    resp = await client.get("/")
    assert resp.status == 200
    assert "text/html" in resp.headers["Content-Type"]
    assert resp.headers["Cache-Control"] == "no-store"
    assert "script-src 'self' https://telegram.org" in resp.headers["Content-Security-Policy"]
    body = await resp.text()
    assert "wu_cd_lap_press" in body


@pytest.mark.asyncio
async def test_app_js_served(client):
    resp = await client.get("/app.js")
    assert resp.status == 200
    assert "javascript" in resp.headers["Content-Type"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "tma not-real-init-data"},
        {"Authorization": "tma " + make_init_data(bot_token="999:wrong")},
    ],
)
async def test_api_rejects_unauthenticated(client, users, headers):
    for method in ("get", "put"):
        resp = await getattr(client, method)("/api/prefs", headers=headers, json=DEFAULTS)
        assert resp.status == 401
    assert users == {}  # nothing was written on any rejected request


@pytest.mark.asyncio
async def test_get_prefs_returns_defaults_for_unknown_user(client, users):
    resp = await client.get("/api/prefs", headers=AUTH)
    assert resp.status == 200
    assert await resp.json() == DEFAULTS


@pytest.mark.asyncio
async def test_get_prefs_merges_stored_over_defaults(client, users):
    users[42] = {"telegram_id": 42, "prefs": {"add_warmup": True}}
    body = await (await client.get("/api/prefs", headers=AUTH)).json()
    assert body["add_warmup"] is True
    assert body["wu_cd_lap_press"] is True  # default still applied


@pytest.mark.asyncio
async def test_put_then_get_round_trip(client, users):
    new = {"add_warmup": True, "add_cooldown": True, "wu_cd_lap_press": False, "wu_cd_skip_pace": True}
    resp = await client.put("/api/prefs", headers=AUTH, json=new)
    assert resp.status == 200
    assert users[42]["prefs"] == new
    assert await (await client.get("/api/prefs", headers=AUTH)).json() == new


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"add_warmup": True},  # partial: missing keys would reset to defaults
        {**DEFAULTS, "extra": True},  # unknown key
        {**DEFAULTS, "add_warmup": "yes"},  # non-boolean
        ["add_warmup"],  # not an object
    ],
)
async def test_put_rejects_malformed_bodies(client, users, body):
    resp = await client.put("/api/prefs", headers=AUTH, json=body)
    assert resp.status == 400
    assert users == {}


@pytest.mark.asyncio
async def test_put_rejects_non_json(client, users):
    resp = await client.put("/api/prefs", headers=AUTH, data=b"\x00binary")
    assert resp.status == 400
    assert users == {}


@pytest.mark.asyncio
async def test_prefs_are_keyed_to_the_initdata_user(client, users):
    other = {"Authorization": "tma " + make_init_data(user_id=99)}
    await client.put("/api/prefs", headers=other, json=dict.fromkeys(DEFAULTS, True))
    assert 99 in users and 42 not in users


@pytest.mark.asyncio
async def test_healthz_is_public(client):
    resp = await client.get("/healthz")
    assert resp.status == 200

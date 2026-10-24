"""The Fluent catalogs (locales/*.ftl) and the Mini App's STRINGS table.

The catalogs are only ever exercised by a live Telegram reply, so these
tests are what stops a Russian message from going missing, losing a
variable, or picking the wrong plural form.
"""

import re
from pathlib import Path

import pytest
from fluent.syntax import FluentParser
from fluent.syntax.ast import Message, VariableReference
from fluent.syntax.visitor import Visitor

import bot
from i18n import DEFAULT_LANG, LANGS, LOCALES_DIR, t
from workout_service import FailureCode, Progress

ROOT = Path(__file__).resolve().parent.parent


class _Vars(Visitor):
    def __init__(self):
        super().__init__()
        self.names: set[str] = set()

    def visit_VariableReference(self, node: VariableReference):
        self.names.add(node.id.name)


def _catalog(lang: str) -> dict[str, set[str]]:
    """message id -> variables it references."""
    resource = FluentParser().parse((LOCALES_DIR / f"{lang}.ftl").read_text(encoding="utf-8"))
    out = {}
    for entry in resource.body:
        if isinstance(entry, Message):
            v = _Vars()
            v.visit(entry)
            out[entry.id.name] = v.names
    return out


def test_default_is_russian():
    assert DEFAULT_LANG == "ru"


@pytest.mark.parametrize("lang", [lang for lang in LANGS if lang != "en"])
def test_catalogs_have_same_messages_and_variables(lang):
    assert _catalog(lang) == _catalog("en")


def test_every_bot_message_id_exists():
    ids = set(_catalog("en"))
    used = set(bot._FAILURE_MSG.values()) | set(bot._PROGRESS_MSG.values())
    assert used <= ids
    assert set(bot._FAILURE_MSG) == set(FailureCode)
    assert set(bot._PROGRESS_MSG) == set(Progress)


@pytest.mark.parametrize(
    ("wait", "word"),
    [(1, "минуту"), (2, "минуты"), (5, "минут"), (11, "минут"), (21, "минуту"), (22, "минуты")],
)
def test_russian_plurals(wait, word):
    text = t("ru", "fail-rate-limited", scope="hourly", cap=10, wait=wait)
    assert text.endswith(f"через {wait} {word}.")


def test_english_rate_limit_matches_old_wording():
    assert t("en", "fail-rate-limited", scope="daily", cap=50, wait=3) == (
        "⚠️ Daily limit reached (50 per day). Try again in 3 hours."
    )


def test_no_bidi_isolation_marks():
    # Fluent's default FSI/PDI marks would land inside the URL and break it.
    text = t("en", "upload-success", url="https://connect.garmin.com/w/1")
    assert "\N{FIRST STRONG ISOLATE}" not in text and "\N{POP DIRECTIONAL ISOLATE}" not in text
    assert text.endswith("\nhttps://connect.garmin.com/w/1")


def test_unknown_language_falls_back_to_default():
    assert t("de", "processing") == t(DEFAULT_LANG, "processing")


def test_unknown_message_raises():
    with pytest.raises(KeyError):
        t("en", "no-such-message")


def test_appended_messages_keep_their_leading_blank_line():
    for lang in LANGS:
        assert t(lang, "busy-suffix").startswith("\n\n⚠️")
        assert t(lang, "processing-time", ms=5).startswith("\n\n⚡")


def test_processing_time_value_is_not_localized():
    # bot.py passes ms pre-formatted so Fluent can't regroup the digits.
    assert t("ru", "processing-time", ms="12,345").endswith("12,345 мс")
    assert t("en", "processing-time", ms="12,345").endswith("12,345 ms")


# --- Mini App -----------------------------------------------------------------

def test_webapp_strings_cover_every_page_key_in_every_language():
    js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
    blocks = dict(re.findall(r"^  (\w+): \{\n(.*?)^  \},", js, re.S | re.M))
    assert set(blocks) == set(LANGS)
    keys = {lang: set(re.findall(r"^    (\w+):", body, re.M)) for lang, body in blocks.items()}
    assert keys["ru"] == keys["en"]
    page_keys = set(re.findall(r'data-i18n="(\w+)"', html))
    used_in_js = set(re.findall(r'(?:t|setStatus)\("(\w+)"', js)) | {"loading", "applies"}
    assert page_keys | used_in_js <= keys["en"]

"""User-facing bot copy in Russian and English (Project Fluent, locales/*.ftl).

Fluent over gettext: no compile step (.ftl is read as-is, so the Dockerfile
and tests need nothing extra), and plural/variant selection lives in the
translation file rather than in code — Russian needs three plural forms
("1 минуту / 2 минуты / 5 минут") where English needs two, and with Fluent
the call site doesn't know or care. Messages are keyed by stable ids, so
rewording the English copy doesn't orphan the Russian.

The Mini App's own strings live in webapp/app.js — it is a static page that
can't call into this module.
"""

from pathlib import Path

from fluent.runtime import FluentBundle, FluentResource
from fluent.syntax.ast import Junk

LANGS = ("ru", "en")
DEFAULT_LANG = "ru"

LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def _load(lang: str) -> FluentBundle:
    """Parse locales/<lang>.ftl, failing the deploy on any syntax error.

    Fluent's parser is error-tolerant: a broken entry becomes Junk and every
    other message still loads, so a typo would surface only as a missing
    message at reply time. Refuse it here instead.
    """
    resource = FluentResource((LOCALES_DIR / f"{lang}.ftl").read_text(encoding="utf-8"))
    junk = [entry.content.strip() for entry in resource.body if isinstance(entry, Junk)]
    if junk:
        raise ValueError(f"{lang}.ftl has unparseable entries: {junk}")
    # use_isolating=False: by default Fluent wraps every placeable in Unicode
    # bidi isolation marks. Telegram shows them verbatim, and inside a URL or a
    # /command they break the link. Both languages are LTR, so nothing is lost.
    bundle = FluentBundle([lang], use_isolating=False)
    bundle.add_resource(resource)
    return bundle


_BUNDLES = {lang: _load(lang) for lang in LANGS}


def t(lang: str, msg_id: str, **args) -> str:
    """Format message `msg_id` in `lang` (unknown languages get the default).

    tests/test_i18n.py keeps both catalogs in lockstep, so the English
    fallback is a last resort for a hot-fixed catalog, not a code path.
    """
    for candidate in (lang, DEFAULT_LANG, "en"):
        bundle = _BUNDLES.get(candidate)
        if bundle is not None and bundle.has_message(msg_id):
            text, errors = bundle.format_pattern(bundle.get_message(msg_id).value, args)
            if errors:
                # A missing variable renders as "{$name}" — ugly but readable,
                # so log and send rather than fail the reply.
                print(f"[i18n] {candidate}/{msg_id}: {errors}", flush=True)
            return text
    raise KeyError(msg_id)

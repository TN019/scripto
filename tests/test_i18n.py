from scripto.i18n import CATALOGS, DEFAULT_LANGUAGE, I18n
from scripto.i18n.en import CATALOG as EN
from scripto.i18n.zh import CATALOG as ZH


def test_catalogs_have_identical_keys():
    """R7 guard: every key must exist in both languages."""
    assert set(EN) == set(ZH)


def test_lookup_in_each_language():
    lang = {"value": "en"}
    i18n = I18n(lambda: lang["value"])
    english = i18n.t("app.tagline")
    lang["value"] = "zh"
    chinese = i18n.t("app.tagline")
    assert english != chinese
    assert english == EN["app.tagline"]
    assert chinese == ZH["app.tagline"]


def test_language_switch_is_instant():
    lang = {"value": "en"}
    i18n = I18n(lambda: lang["value"])
    assert i18n.language == "en"
    lang["value"] = "zh"
    assert i18n.language == "zh"  # same instance, no rebuild needed


def test_unknown_language_falls_back_to_default():
    i18n = I18n(lambda: "fr")
    assert i18n.language == DEFAULT_LANGUAGE
    assert i18n.t("app.name") == EN["app.name"]


def test_missing_key_returns_key_and_warns_once(caplog):
    i18n = I18n(lambda: "en")
    with caplog.at_level("WARNING"):
        assert i18n.t("no.such.key") == "no.such.key"
        i18n.t("no.such.key")
    warnings = [r for r in caplog.records if "no.such.key" in r.message]
    assert len(warnings) == 1


def test_formatting_kwargs():
    i18n = I18n(lambda: "zh")
    assert "0.9" in i18n.t("cli.info.header", version="0.9")


def test_default_language_catalog_is_fallback():
    assert DEFAULT_LANGUAGE in CATALOGS

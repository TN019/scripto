import pytest

from scripto.core.errors import ScriptoError
from scripto.core import languages, output


def test_builtin_languages():
    assert languages.get_language("zh").suffix == ".zh"
    assert languages.get_language("en").suffix == ".en"
    assert languages.get_language("ja").suffix == ".ja"
    assert languages.get_language("ko").suffix == ".ko"
    assert languages.get_language("zh").prompt_name == "Simplified Chinese"
    assert languages.get_language("ja").prompt_name == "Japanese"
    assert languages.get_language("ko").prompt_name == "Korean"


def test_unknown_language_raises():
    with pytest.raises(ScriptoError) as excinfo:
        languages.get_language("klingon")
    assert excinfo.value.key == "errors.unknown_language"


def test_registering_a_language_needs_no_pipeline_changes():
    spec = languages.LanguageSpec(code="fr", prompt_name="French", suffix=".fr")
    languages.register_language(spec)
    try:
        assert languages.get_language("fr") == spec
        assert languages.suffix_map()["fr"] == ".fr"
    finally:
        languages._REGISTRY.pop("fr", None)


def test_output_suffixes_come_from_registry():
    assert output.DEFAULT_SUFFIXES == languages.suffix_map()

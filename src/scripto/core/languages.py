"""Language registry (R3): adding a language is one entry here, zero pipeline changes.

Used by translation (prompt wording, output suffix) and by output naming for
transcripts. ``suffix`` matches R4 naming: en → lecture.en.srt, zh → lecture.cn.srt.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ScriptoError


@dataclass(frozen=True)
class LanguageSpec:
    code: str          # config value, e.g. "zh"
    prompt_name: str   # name used inside translation prompts
    suffix: str        # filename suffix, e.g. ".cn"


_REGISTRY: dict[str, LanguageSpec] = {}


def register_language(spec: LanguageSpec) -> None:
    _REGISTRY[spec.code] = spec


def get_language(code: str) -> LanguageSpec:
    spec = _REGISTRY.get(code)
    if spec is None:
        raise ScriptoError(
            f"Unknown target language: {code}", key="errors.unknown_language", code=code
        )
    return spec


def known_languages() -> list[LanguageSpec]:
    return list(_REGISTRY.values())


def suffix_map() -> dict[str, str]:
    """Language-code → filename-suffix map for output naming."""
    return {spec.code: spec.suffix for spec in _REGISTRY.values()}


register_language(LanguageSpec(code="en", prompt_name="English", suffix=".en"))
register_language(LanguageSpec(code="zh", prompt_name="Simplified Chinese", suffix=".cn"))

"""i18n (R7): every user-visible string goes through a catalog.

Rules:
- No hardcoded user-facing strings anywhere in the app — always ``i18n.t(key)``.
- ``en`` is the fallback catalog; a key missing from the active language falls
  back to English and logs one warning per key.
- Language is resolved dynamically on every lookup, so switching the setting
  takes effect instantly without rebuilding anything.
"""

from __future__ import annotations

import logging
from typing import Callable

from .en import CATALOG as EN
from .zh import CATALOG as ZH

CATALOGS: dict[str, dict[str, str]] = {"en": EN, "zh": ZH}
DEFAULT_LANGUAGE = "en"

logger = logging.getLogger(__name__)


class I18n:
    def __init__(self, get_language: Callable[[], str]):
        """``get_language`` is called on every lookup (e.g. reads config)."""
        self._get_language = get_language
        self._warned: set[str] = set()

    @property
    def language(self) -> str:
        lang = (self._get_language() or "").strip()
        return lang if lang in CATALOGS else DEFAULT_LANGUAGE

    def t(self, key: str, **kwargs) -> str:
        language = self.language
        template = CATALOGS[language].get(key)
        if template is None and language != DEFAULT_LANGUAGE:
            template = EN.get(key)
            if template is not None:
                self._warn_once(f"key {key!r} missing in {language!r}, using en")
        if template is None:
            self._warn_once(f"key {key!r} missing in every catalog")
            return key
        return template.format(**kwargs) if kwargs else template

    def _warn_once(self, message: str) -> None:
        if message not in self._warned:
            self._warned.add(message)
            logger.warning("i18n: %s", message)

"""`scripto` entry point — the desktop GUI (placeholder until milestone M5)."""

from __future__ import annotations

from .core.config import ConfigService
from .core.logs import setup_logging
from .i18n import I18n


def main() -> int:
    setup_logging()
    config_service = ConfigService()
    i18n = I18n(lambda: config_service.load().get("language", ""))
    print(i18n.t("app.gui_placeholder"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

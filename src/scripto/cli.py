"""`scripto-cli` entry point.

M1 scope: version and environment info. Transcription commands land with the
pipeline milestones.
"""

from __future__ import annotations

import argparse

from . import __version__
from .core import paths
from .core.config import ConfigService
from .core.logs import setup_logging
from .i18n import I18n


def build_parser(i18n: I18n) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripto-cli", description=i18n.t("cli.description")
    )
    parser.add_argument(
        "--version", action="version", version=f"scripto {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("info", help=i18n.t("cli.info.help"))
    return parser


def run_info(config_service: ConfigService, i18n: I18n) -> int:
    config = config_service.load()
    language = config.get("language") or i18n.t("cli.language.not_set")
    print(i18n.t("cli.info.header", version=__version__))
    print(i18n.t("cli.info.config", path=config_service.path))
    print(i18n.t("cli.info.data_dir", path=paths.data_dir()))
    print(i18n.t("cli.info.log_dir", path=paths.log_dir()))
    print(i18n.t("cli.info.language", value=language))
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    config_service = ConfigService()
    i18n = I18n(lambda: config_service.load().get("language", ""))
    parser = build_parser(i18n)
    args = parser.parse_args(argv)

    if args.command == "info":
        return run_info(config_service, i18n)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

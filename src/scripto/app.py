"""`scripto` entry point — the desktop GUI.

`SCRIPTO_GUI_WEB=1` serves the same UI over HTTP without opening a window
(used for headless UI testing); `SCRIPTO_GUI_PORT` picks the port.
"""

from __future__ import annotations

import os

from .core.logs import setup_logging


def main() -> int:
    setup_logging()
    import flet as ft

    from .gui.app_gui import gui_main

    if os.environ.get("SCRIPTO_GUI_WEB"):
        # Headless web serving for UI testing (dev-only deps: flet-web, uvicorn).
        # flet 0.86's ft.app(view=None) opens the desktop socket, not HTTP —
        # the fastapi integration is the supported web path.
        import uvicorn
        import flet_web.fastapi as flet_fastapi

        port = int(os.environ.get("SCRIPTO_GUI_PORT", "8551"))
        uvicorn.run(
            flet_fastapi.app(gui_main),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    else:
        ft.app(target=gui_main)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

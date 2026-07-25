"""PySide6 view layer: thin renderer over GuiViewModel.

Same hard rules as the flet layer it replaces:
- The UI thread never touches disk or network: scanning is debounced onto a
  worker, batch/model work runs on threads, results arrive via vm.drain().
- Rendering is incremental: one widget per file row, only changed rows are
  updated, drained at ~4 Hz.
- Every visible string goes through i18n; switching language remounts the
  window content so nothing is left behind.

Being a plain in-process Qt app (no separate viewer client) is the point of
the port: the Dock/taskbar identity is this process, closing the window can
simply hide it, and no bundle-rebranding machinery is needed.
"""

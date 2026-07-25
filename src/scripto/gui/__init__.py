"""GUI state layer.

Split by rule: ``viewmodel.py`` owns all state and logic and never imports
a UI toolkit (fully unit-tested); the Qt view layer in ``scripto.gui_qt``
is a thin renderer that drains the viewmodel on a throttled timer and only
touches changed rows.
"""

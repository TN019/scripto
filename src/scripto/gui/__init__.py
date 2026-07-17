"""Desktop GUI (Flet).

Split by rule: ``viewmodel.py`` owns all state and logic and never imports
flet (fully unit-tested); the flet layer is a thin renderer that drains the
viewmodel on a throttled timer and only touches changed rows.
"""

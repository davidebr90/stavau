"""stavau GUI (v0.3): PySide6 desktop shell over the existing core.

Import `stavau.ui.gui.app` lazily (see cli.py's `gui` subcommand) so that
PySide6 stays an optional dependency (`pip install stavau[gui]`). The
`viewmodel` module has no Qt import and can always be imported standalone.
"""

from __future__ import annotations

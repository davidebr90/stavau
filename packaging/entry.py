"""PyInstaller entry point for the stavau CLI.

PyInstaller needs a plain script (not a `console_scripts` shim) to use as the
analysis root. This module just imports and calls the real entry point at
``stavau.cli:main`` so the frozen executable behaves identically to the
`stavau` command installed via pip.
"""

from __future__ import annotations

from stavau.cli import main

if __name__ == "__main__":
    main()

# PyInstaller spec for stavau — one-folder bundle.
#
# Build (from the repo root, with ".[tray,integration]" and pyinstaller installed):
#   pyinstaller packaging/stavau.spec --distpath dist --workpath build --noconfirm
#
# Output: dist/stavau/ (a one-folder bundle). The entry point is
# packaging/entry.py, which just calls stavau.cli:main so the frozen binary
# behaves like the `stavau` console-script.
#
# pystray picks its backend at runtime via platform checks and imports the
# backend module lazily (e.g. pystray._win32, pystray._xorg, pystray._darwin,
# pystray._appindicator, pystray._gtk), which PyInstaller's static analysis
# cannot see. collect_submodules('pystray') pulls in every backend module on
# every OS; the ones that don't apply to the build platform simply fail their
# own top-level imports (e.g. _win32 importing ctypes.wintypes on Linux) and
# are never invoked at runtime, so bundling them all is harmless and avoids
# per-OS spec forks. Pillow (pystray's image dependency) is a normal import
# and needs no special handling.
#
# paho-mqtt (the optional [integration] extra) is imported lazily and guarded
# inside core.integration, so PyInstaller's static analysis cannot see it;
# collect_submodules('paho') bundles it (it is tiny) so the frozen binary can
# do MQTT smart-home integration. The PySide6 GUI ([gui] extra) is deliberately
# NOT bundled — Qt would add ~150 MB per platform for an optional feature — so
# `stavau gui` in a frozen bundle degrades to its ImportError message; run the
# GUI from a pip/pipx install (`pipx install "stavau[gui]"`). See docs/install.md.
from __future__ import annotations

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("pystray") + collect_submodules("PIL") + collect_submodules("paho")
)

a = Analysis(
    ["entry.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="stavau",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="stavau",
)

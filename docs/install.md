# Installing stavau

This page covers two installation paths — **pipx from source** (recommended
while there are no signed installers) and **prebuilt bundles** attached to a
[GitHub Release](https://github.com/davidebr90/stavau/releases) — plus how to
make stavau start automatically per OS, and how to uninstall.

> **Unsigned binaries.** The prebuilt bundles produced by
> `.github/workflows/release.yml` are **not code-signed or notarized**.
> Windows SmartScreen and macOS Gatekeeper will warn about an "unknown
> publisher" / unidentified developer. This is expected for now — there is no
> code-signing certificate yet. If that warning is unacceptable for your use
> case, install from source instead (below), where nothing is downloaded as a
> pre-built binary.

## 1. pipx from source (all platforms, recommended)

[pipx](https://pipx.pypa.io/) installs the `stavau` CLI into its own isolated
virtual environment and puts a `stavau` command on your PATH, without
touching your system Python.

```bash
# Install pipx once, if you don't have it:
python -m pip install --user pipx
python -m pipx ensurepath

# Install stavau straight from the repository:
pipx install "git+https://github.com/davidebr90/stavau.git"

# ...or from a local clone (useful for a specific branch/tag):
git clone https://github.com/davidebr90/stavau.git
cd stavau
pipx install .

# To include the optional system-tray UI (pystray + Pillow):
pipx install ".[tray]"

# For the graphical app (`stavau gui`, PySide6/Qt) and/or MQTT smart-home
# integration, add those extras — the GUI is only available this way, not in
# the prebuilt bundles below:
pipx install ".[tray,gui,integration]"
```

Verify it works:

```bash
stavau --version
stavau --help
```

### Upgrading

```bash
pipx upgrade stavau
```

### Uninstalling

```bash
pipx uninstall stavau
```

## 2. Prebuilt bundle (download and run)

Each [GitHub Release](https://github.com/davidebr90/stavau/releases) includes
a one-folder bundle per OS, built by PyInstaller from
[`packaging/stavau.spec`](../packaging/stavau.spec):

- `windows-stavau.zip`
- `linux-stavau.zip`
- `macos-stavau.zip`

Every release also has a `SHA256SUMS.txt` with checksums for all of the
above. Verify the download before running it:

```bash
# Linux/macOS
shasum -a 256 -c SHA256SUMS.txt --ignore-missing

# Windows (PowerShell)
Get-FileHash windows-stavau.zip -Algorithm SHA256
# ...then compare the hash against the value in SHA256SUMS.txt
```

Steps:

1. Download the zip for your OS and the matching `SHA256SUMS.txt`, and verify
   the checksum as above.
2. Extract the zip anywhere you like, e.g. `~/stavau/` or
   `C:\Users\<you>\stavau\`. You get a folder containing `stavau` (or
   `stavau.exe` on Windows) plus its bundled dependencies — keep the whole
   folder together, don't move the executable out on its own.
3. Run it:
   - Windows: `.\stavau\stavau.exe --version`
   - Linux/macOS: `./stavau/stavau --version`
4. **Windows SmartScreen** will likely show "Windows protected your PC". Click
   **More info → Run anyway** (only do this because you verified the
   checksum above).
5. **macOS Gatekeeper** will refuse to open an app "from an unidentified
   developer" the first time. Either right-click the binary → **Open** (and
   confirm in the dialog that appears), or clear the quarantine flag once you
   have verified the checksum:
   ```bash
   xattr -d com.apple.quarantine ./stavau/stavau
   ```

### Uninstalling

Delete the folder you extracted. The bundle is fully self-contained; it does
not touch any system directories. If you also enabled autostart (below),
remove that too — see the relevant section's uninstall step.

Your stavau **config and event log** live outside the bundle (via
[platformdirs](https://pypi.org/project/platformdirs/)) and are not removed
by deleting the bundle:

- Windows: `%LOCALAPPDATA%\stavau\`
- Linux: `~/.config/stavau/` and `~/.local/share/stavau/`
- macOS: `~/Library/Application Support/stavau/`

Delete those directories too if you want a clean uninstall.

## Autostart

stavau does not install itself as a startup item automatically — you choose
whether and how it starts. Pick your OS below. In every case, run
`stavau setup` **once, interactively** first to pair a device and write a
config; the autostart entry then just runs `stavau run` headlessly (or
`stavau tray` if you installed the `tray` extra and want the status icon).

### Windows — Startup folder shortcut

1. Press `Win + R`, type `shell:startup`, press Enter. This opens your
   per-user Startup folder.
2. Create a shortcut in that folder pointing at `stavau.exe` (from your pipx
   install, typically found via `where stavau`, or from the extracted
   bundle) with the argument `run` (or `tray`).
3. stavau will now start automatically at login.

Equivalent one-liner using `schtasks` instead of a shortcut (runs at logon,
current user only):

```powershell
schtasks /Create /SC ONLOGON /TN "stavau" /TR "\"C:\path\to\stavau.exe\" run" /RL LIMITED
```

To remove it:

```powershell
schtasks /Delete /TN "stavau" /F
```

(Or, if you used a Startup-folder shortcut, just delete the shortcut file
from `shell:startup`.)

### Linux — systemd user unit

A sample unit is provided at
[`packaging/stavau.service`](../packaging/stavau.service). It restarts stavau
on failure and starts it at login via the default per-user target.

```bash
mkdir -p ~/.config/systemd/user
cp packaging/stavau.service ~/.config/systemd/user/stavau.service
# Edit ExecStart in that file if `stavau` isn't at ~/.local/bin/stavau
systemctl --user enable --now stavau
```

Check status and logs:

```bash
systemctl --user status stavau
journalctl --user -u stavau -f
```

Uninstall:

```bash
systemctl --user disable --now stavau
rm ~/.config/systemd/user/stavau.service
systemctl --user daemon-reload
```

### macOS — LaunchAgent

A sample LaunchAgent plist is provided at
[`packaging/com.stavau.daemon.plist`](../packaging/com.stavau.daemon.plist).
It starts stavau at login (`RunAtLoad`) and relaunches it if it exits
(`KeepAlive`).

```bash
mkdir -p ~/Library/LaunchAgents
cp packaging/com.stavau.daemon.plist ~/Library/LaunchAgents/
# Edit ProgramArguments in that file to point at your actual `stavau` path
# (find it with `which stavau`) — LaunchAgents don't use your shell's PATH.
launchctl load -w ~/Library/LaunchAgents/com.stavau.daemon.plist
```

Check status and logs:

```bash
launchctl list | grep com.stavau.daemon
cat /tmp/stavau.out.log /tmp/stavau.err.log
```

Uninstall:

```bash
launchctl unload -w ~/Library/LaunchAgents/com.stavau.daemon.plist
rm ~/Library/LaunchAgents/com.stavau.daemon.plist
```

## Building the bundle yourself

You don't need to wait for a release to try the PyInstaller bundle — it
builds locally on any platform with Python 3.12:

```bash
pip install ".[tray,integration]" pyinstaller
cd packaging
pyinstaller stavau.spec --distpath ../dist --workpath ../build --noconfirm
../dist/stavau/stavau --version    # or dist\stavau\stavau.exe on Windows
```

See [`.github/workflows/release.yml`](../.github/workflows/release.yml) for
the exact commands used in CI, and [`packaging/stavau.spec`](../packaging/stavau.spec)
for the PyInstaller configuration (one-folder mode, `pystray`/`Pillow`/`paho`
hidden-imports, tests excluded). The bundle is CLI + tray + MQTT integration;
the PySide6 GUI is intentionally not bundled (Qt is large) — install it via
`pipx install "stavau[gui]"`.

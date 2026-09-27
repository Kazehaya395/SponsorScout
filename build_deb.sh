#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="SponsorScout"
PKG_NAME="sponsorscout"
DEB_ARCH="amd64"

BUILD_DIR=".build/deb"
VENV_DIR=".build/deb-venv"
DIST_DIR="dist"
APP_DIR="$BUILD_DIR/opt/$PKG_NAME"
DEBIAN_DIR="$BUILD_DIR/DEBIAN"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

# dpkg-deb only writes a local .deb file, so building must never run as root.
# Running with sudo would also create root-owned build artifacts and could
# trigger Debian's PEP 668 "externally-managed-environment" protection.
if [ "$(id -u)" -eq 0 ]; then
  cat >&2 <<'EOF'
ERROR: do not run build_deb.sh with sudo.
This script only creates dist/sponsorscout_<version>_amd64.deb and does not
need root. Run it as your normal user:

  ./build_deb.sh
EOF
  exit 1
fi

need python3
need dpkg-deb

# Debian/Ubuntu may ship Python without the venv/ensurepip modules. Fail with an
# actionable message instead of falling back to a system-wide pip install.
if ! python3 -c "import venv" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: Python's venv module is missing.
Install it first, then run ./build_deb.sh again:

  sudo apt install python3-venv
EOF
  exit 1
fi

# All build dependencies live in this disposable, git-ignored environment.
# This avoids PEP 668 and never modifies the system Python installation.
if [ -e "$VENV_DIR" ] && [ ! -w "$VENV_DIR" ]; then
  echo "ERROR: $VENV_DIR is not writable by the current user." >&2
  echo "A previous sudo build may have created it. Remove it once, then retry:" >&2
  echo "  sudo rm -rf $VENV_DIR && ./build_deb.sh" >&2
  exit 1
fi

# A venv can be present and even executable yet have no working pip (for
# example it was created while python3-venv was still missing, or a previous
# run was interrupted midway). Test that pip actually runs inside it instead of
# trusting the mere existence of bin/python, and rebuild the venv when it is
# not usable. This is what caused "bin/python: No module named pip".
venv_python="$VENV_DIR/bin/python"
venv_has_pip() {
  [ -x "$1" ] && "$1" -m pip --version >/dev/null 2>&1
}

if [ -e "$VENV_DIR" ] && ! venv_has_pip "$venv_python"; then
  echo "Existing $VENV_DIR has no working pip - recreating it..."
  rm -rf "$VENV_DIR"
fi

if [ ! -x "$venv_python" ]; then
  echo "Creating build virtual environment at $VENV_DIR..."
  if ! python3 -m venv "$VENV_DIR"; then
    echo "ERROR: could not create $VENV_DIR" >&2
    echo "If it exists with root-owned files, remove it and retry:" >&2
    echo "  sudo rm -rf $VENV_DIR && ./build_deb.sh" >&2
    exit 1
  fi
fi

# Some minimal installs produce a venv whose pip module is present but not
# bootstrapped; ask ensurepip to finish the job before giving up.
if ! venv_has_pip "$venv_python"; then
  echo "Bootstrapping pip inside $VENV_DIR..."
  "$venv_python" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

if ! venv_has_pip "$venv_python"; then
  cat >&2 <<EOF
ERROR: could not provide pip inside $VENV_DIR.
The venv module is available but ensurepip/pip is missing. Install the full
venv package and try again:

  sudo apt install python3-venv

If it still fails, remove the environment and rebuild it:

  rm -rf $VENV_DIR && ./build_deb.sh
EOF
  exit 1
fi

PYTHON="$venv_python"

VERSION="$("$PYTHON" - <<'PY'
from pathlib import Path
import re
text = Path('pyproject.toml').read_text(encoding='utf-8')
m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
if not m:
    raise SystemExit('Unable to read version from pyproject.toml')
print(m.group(1))
PY
)"

echo "Installing build dependencies into $VENV_DIR..."
PIP_DISABLE_PIP_VERSION_CHECK=1 "$PYTHON" -m pip install -r requirements.txt

# Fail fast when a dependency installed but is not actually usable. A broken
# greenlet (its compiled extension missing) makes `playwright.sync_api` raise
# ModuleNotFoundError, which the scanner swallows and later reports as an opaque
# "Playwright is required for DOM fallback" for every target.
if ! "$PYTHON" -c "from playwright.sync_api import sync_playwright" >/dev/null 2>&1; then
  echo "ERROR: playwright is not usable inside $VENV_DIR (broken/missing dependency)." >&2
  echo "Recreate the build environment and retry:" >&2
  echo "  rm -rf $VENV_DIR && ./build_deb.sh" >&2
  exit 1
fi

rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$APP_DIR" "$DEBIAN_DIR" "$DIST_DIR"

"$PYTHON" -m PyInstaller \
  --clean \
  --noconfirm \
  --windowed \
  --onedir \
  --name "$APP_NAME" \
  --collect-data sponsorscout \
  --collect-submodules sponsorscout \
  --collect-all playwright \
  --collect-submodules PySide6 \
  --hidden-import greenlet \
  --hidden-import pyee \
  --exclude-module pandas \
  --exclude-module PIL \
  --exclude-module bs4 \
  --exclude-module lxml \
  --exclude-module tkinter \
  --exclude-module pytest \
  sponsorscout/main.py

cp -a "dist/$APP_NAME/"* "$APP_DIR/"

# ── Size reduction: remove caches only ──────────────────────────────────────
# WARNING: Do NOT `strip` the bundled *.so files. PyInstaller ships many Python
# C extensions (numpy/lxml/Pillow/Playwright) whose symbol tables and
# unwind/exception metadata are required at runtime. Stripping them with
# --strip-unneeded produces intermittent SIGSEGV / corrupted-rendering crashes
# that look like a "virus" or "glitchy UI" on the user's machine. We only drop
# caches and obviously-unneeded metadata to stay safe.
echo "Reducing .deb size (safe mode — no binary stripping)…"

# Remove __pycache__ directories and .pyc files (saves 5-15 MB)
find "$APP_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$APP_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true

# Remove dist-info metadata directories (saves 5-10 MB)
find "$APP_DIR" -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true

# Remove unnecessary locale data (saves 2-5 MB)
find "$APP_DIR" -type d -name "locale" -exec rm -rf {} + 2>/dev/null || true

# Remove test directories bundled from packages (saves 5-20 MB)
find "$APP_DIR" -type d \( -name "tests" -o -name "test" -o -name "testing" \) \
  -not -path "*/sponsorscout/*" -exec rm -rf {} + 2>/dev/null || true

# ── Bundled Playwright Chromium ──────────────────────────────────────────────
# Install Chromium DIRECTLY into the package's `_playwright` directory so it
# ships inside the .deb and sponsorscout/paths.py (exe_dir/_playwright) and
# the /usr/bin/sponsorscout launcher can point PLAYWRIGHT_BROWSERS_PATH at it
# on the user's machine. Previously this downloaded to the build machine's
# ~/.cache/ms-playwright only, so the installed app had no browser at all and
# every `provider=auto` career target failed with "Executable doesn't exist".
echo "Installing Playwright Chromium into bundle ($APP_DIR/_playwright)…"
PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/_playwright" "$PYTHON" -m playwright install chromium
if [ ! -d "$APP_DIR/_playwright" ]; then
  echo "ERROR: Playwright browsers were NOT installed into $APP_DIR/_playwright —" >&2
  echo "the packaged app could not scan SPA career portals. Aborting build." >&2
  exit 1
fi
# Remove Playwright's bundled ffmpeg (video recording only — never used), ~3 MB.
find "$APP_DIR/_playwright" -maxdepth 1 -type d -name 'ffmpeg*' -exec rm -rf {} + 2>/dev/null || true
# The bundled Playwright Chromium (_playwright) is intentionally KEPT so
# JS-rendered career portals work out of the box. Deleting it makes every scan
# fail and the UI appear hung/frozen.
echo "Size reduction complete."

# ── Smoke test ──────────────────────────────────────────────────────────────
# Run the freshly built binary BEFORE packaging it. A bundle that cannot import
# Playwright (or cannot launch its bundled Chromium) used to sail through the
# build and then fail 200+ scan targets at runtime with
# "Playwright is required for DOM fallback". Fail here instead.
echo "Smoke-testing the packaged app (Playwright + bundled Chromium)…"
if ! PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/_playwright" \
     "$APP_DIR/$APP_NAME" --self-check --self-check-browser; then
  echo "ERROR: the packaged app failed --self-check; aborting the build." >&2
  echo "Re-run manually for full details:" >&2
  echo "  PLAYWRIGHT_BROWSERS_PATH=\"$APP_DIR/_playwright\" \\" >&2
  echo "    \"$APP_DIR/$APP_NAME\" --self-check --self-check-browser" >&2
  exit 1
fi

mkdir -p "$BUILD_DIR/usr/bin"
cat > "$BUILD_DIR/usr/bin/sponsorscout" <<'EOL'
#!/bin/sh
# SponsorScout launcher
# The binary lives at /opt/sponsorscout/SponsorScout after copying the
# *contents* of dist/SponsorScout/ into /opt/sponsorscout/.

APP_BIN="/opt/sponsorscout/SponsorScout"

# Verify the binary exists and is executable.
if [ ! -f "$APP_BIN" ]; then
  echo "SponsorScout: ERROR — $APP_BIN not found." >&2
  echo "  The .deb package may not have installed correctly." >&2
  echo "  Try reinstalling:  sudo dpkg --purge sponsorscout && sudo dpkg -i sponsorscout_*.deb" >&2
  exit 1
fi
if [ ! -x "$APP_BIN" ]; then
  echo "SponsorScout: ERROR — $APP_BIN is not executable." >&2
  echo "  Try:  sudo chmod +x $APP_BIN" >&2
  exit 1
fi

# Prefer the Chromium bundle shipped inside the package (/opt/sponsorscout/_playwright,
# installed by build_deb.sh); fall back to the user's Playwright cache.
if [ -z "$PLAYWRIGHT_BROWSERS_PATH" ]; then
  if [ -d "/opt/sponsorscout/_playwright" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="/opt/sponsorscout/_playwright"
  else
    export PLAYWRIGHT_BROWSERS_PATH="${HOME}/.cache/ms-playwright"
  fi
fi

exec "$APP_BIN" "$@"
EOL
chmod 755 "$BUILD_DIR/usr/bin/sponsorscout"

mkdir -p "$BUILD_DIR/usr/share/applications"
cat > "$BUILD_DIR/usr/share/applications/sponsorscout.desktop" <<EOL
[Desktop Entry]
Version=1.0
Type=Application
Name=SponsorScout
GenericName=Job Sponsorship Finder
Comment=Find visa-sponsoring jobs from official ATS boards
Exec=/usr/bin/sponsorscout
Icon=sponsorscout
Terminal=false
Categories=Office;Network;
Keywords=jobs;visa;sponsorship;careers;
StartupWMClass=SponsorScout
EOL

# Install scalable-ish hicolor icon theme entries (16px .. 512px) so the
# desktop entry's Icon=sponsorscout resolves on the panel / app grid.
if [ ! -f "sponsorscout/data/icons/sponsorscout_512.png" ]; then
  echo "ERROR: hicolor source icons not found (sponsorscout_512.png missing) -" >&2
  echo "the app would install with a blank icon. Aborting build." >&2
  exit 1
fi
for size in 16 24 32 48 64 96 128 256 512; do
  src="sponsorscout/data/icons/sponsorscout_${size}.png"
  if [ ! -f "$src" ]; then
    echo "WARNING: missing icon source $src - skipping this size." >&2
    continue
  fi
  dst="$BUILD_DIR/usr/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dst"
  cp "$src" "$dst/sponsorscout.png"
done

cat > "$DEBIAN_DIR/control" <<EOL
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $DEB_ARCH
Maintainer: SponsorScout <sponsorscout@localhost>
Depends: libc6, libgcc-s1, libstdc++6
Installed-Size: $(du -sk "$BUILD_DIR" | cut -f1)
Description: Job sponsorship search application
 A local desktop app for finding jobs with visa sponsorship signals.
EOL

cat > "$DEBIAN_DIR/postinst" <<'EOL'
#!/bin/sh
set -e
# Update desktop database and icon cache
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

# Last-resort fallback: install Playwright Chromium into the user's cache.
# Normally NOT needed — build_deb.sh ships the browsers inside
# /opt/sponsorscout/_playwright, which the launcher and sponsorscout/paths.py
# prefer automatically. This only helps if the bundled browsers are missing.
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [ -n "$PYTHON" ] && "$PYTHON" -c "import playwright" 2>/dev/null; then
  echo "SponsorScout: installing Playwright Chromium browser (one-time, ~150 MB)…"
  "$PYTHON" -m playwright install chromium >/dev/null 2>&1 || true
fi

exit 0
EOL
chmod 755 "$DEBIAN_DIR/postinst"

cat > "$DEBIAN_DIR/prerm" <<'EOL'
#!/bin/sh
set -eu

# Stop the GUI before dpkg removes package-owned files. This is also needed
# during upgrades, but it never removes user data.
for sig in TERM KILL; do
  pids=$(pgrep -x "SponsorScout" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "SponsorScout: stopping running instance(s) (SIG$sig)..."
    # shellcheck disable=SC2086
    kill -s "$sig" $pids 2>/dev/null || true
    sleep 1
  fi
done

exit 0
EOL
chmod 755 "$DEBIAN_DIR/prerm"

cat > "$DEBIAN_DIR/postrm" <<'EOL'
#!/bin/sh
set -eu

# dpkg calls postrm for upgrades as well as removals. Never purge user data
# during an upgrade; purge it only for an actual uninstall/remove.
ACTION="${1:-remove}"
case "$ACTION" in
  remove|purge) ;;
  *) exit 0 ;;
esac

# Stop the GUI before removing the package files and database.
for sig in TERM KILL; do
  pids=$(pgrep -x "SponsorScout" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "SponsorScout: stopping running instance(s) (SIG$sig)?"
    # shellcheck disable=SC2086
    kill -s "$sig" $pids 2>/dev/null || true
    sleep 1
  fi
done

# dpkg normally removes package-owned files itself. Keep this explicit cleanup
# for interrupted upgrades and older installations that left the app tree.
APP_DIR="/opt/sponsorscout"
if [ -d "$APP_DIR" ]; then
  rm -rf -- "$APP_DIR"
fi

purge_path() {
  target="$1"
  case "$target" in
    /*) ;;
    *) echo "SponsorScout: refusing non-absolute purge path: $target" >&2; return 0 ;;
  esac
  case "$target" in
    "/"|"/bin"|"/boot"|"/dev"|"/etc"|"/home"|"/lib"|"/lib64"|"/media"|"/mnt"|"/opt"|"/proc"|"/root"|"/run"|"/sbin"|"/srv"|"/sys"|"/tmp"|"/usr"|"/var")
      echo "SponsorScout: refusing unsafe purge path: $target" >&2
      return 0
      ;;
  esac
  if [ -d "$target" ]; then
    echo "SponsorScout: removing data directory: $target"
    rm -rf -- "$target"
  elif [ -e "$target" ]; then
    rm -f -- "$target"
  fi
}

# Remove the standard per-user data directory for every real account.
if command -v getent >/dev/null 2>&1; then
  getent passwd | while IFS=: read -r username _ uid _ _ home _; do
    case "$uid" in
      ''|*[!0-9]*) continue ;;
    esac
    if [ "$uid" -ge 1000 ] || [ "$username" = "root" ]; then
      case "$home" in
        ""|/|/nonexistent|/dev/null) continue ;;
      esac
      purge_path "$home/.sponsorscout"
    fi
  done
else
  purge_path "$HOME/.sponsorscout"
  purge_path "/root/.sponsorscout"
fi

# Also clean explicit overrides when dpkg inherited them from the invoking
# environment. This does not inspect arbitrary files or delete a drive root.
if [ -n "${SPONSORSCOUT_DATA_DIR:-}" ]; then
  purge_path "$SPONSORSCOUT_DATA_DIR"
fi
if [ -n "${SPONSORSCOUT_DB_PATH:-}" ]; then
  rm -f -- "$SPONSORSCOUT_DB_PATH" "$SPONSORSCOUT_DB_PATH-wal" "$SPONSORSCOUT_DB_PATH-shm"
fi

exit 0
EOL
chmod 755 "$DEBIAN_DIR/postrm"

# Verify the binary was copied and is a valid ELF executable.
if [ ! -x "$APP_DIR/$APP_NAME" ]; then
  echo "ERROR: $APP_NAME binary not found in $APP_DIR after PyInstaller build." >&2
  echo "Contents of $APP_DIR:" >&2
  ls -la "$APP_DIR/" >&2
  exit 1
fi

# ── Package ─────────────────────────────────────────────────────────────────
# Notes for this step:
#
#   * The staged tree is ~1.5 GB (PySide6 + the bundled Playwright Chromium), so
#     compression dominates the build time. We probe for the fastest
#     universally-installable compressor this machine actually has, rather than
#     trusting the dpkg default (often xz -6, sometimes gzip or nothing at all).
#   * --root-owner-group: an unprivileged build must not record the builder's
#     uid/gid, which otherwise installs /opt/sponsorscout owned by uid 1000.
#   * The .partial + rename keeps a half-written archive from looking finished.
#
# zstd is opt-in only (DEB_COMPRESSION=zstd ./build_deb.sh) because a zstd .deb
# can only be installed by dpkg >= 1.21.18.
DEB_OUT="$DIST_DIR/${PKG_NAME}_${VERSION}_${DEB_ARCH}.deb"
DEB_TMP="$DIST_DIR/.${PKG_NAME}_${VERSION}_${DEB_ARCH}.deb.partial"
rm -f "$DEB_TMP" "$DEB_OUT"

# ── Filesystem permissions ──────────────────────────────────────────────────
# WSL drvfs mounts (/mnt/...), NTFS and FAT report every file as 0777 and ignore
# chmod. dpkg-deb then refuses the control directory outright ("control
# directory has bad permissions 777"), and even if it did not, every installed
# file would be world-writable. Normalise in place when the filesystem honours
# chmod; otherwise stage the tree on a Linux filesystem and build from there.
BUILD_TREE="$BUILD_DIR"
STAGE_DIR=""
if chmod 755 "$DEBIAN_DIR" 2>/dev/null \
   && [ "$(stat -c '%a' "$DEBIAN_DIR" 2>/dev/null || echo 777)" = "755" ]; then
  echo "Normalising package permissions in place…"
  find "$BUILD_DIR" -type d -exec chmod 755 {} + 2>/dev/null || true
  find "$BUILD_DIR" -type f -exec chmod go-w {} + 2>/dev/null || true
else
  echo "This filesystem does not support Unix permissions (WSL /mnt, NTFS, FAT)." >&2
  echo "Staging the package tree on a Linux filesystem instead…" >&2
  STAGE_DIR="$(mktemp -d)"
  cp -a "$BUILD_DIR/." "$STAGE_DIR/"
  chmod -R u=rwX,go=rX "$STAGE_DIR"
  chmod 755 "$STAGE_DIR/DEBIAN"
  chmod 755 "$STAGE_DIR/DEBIAN/"* 2>/dev/null || true
  chmod 755 "$STAGE_DIR/usr/bin/sponsorscout" 2>/dev/null || true
  BUILD_TREE="$STAGE_DIR"
fi

# Probe what this dpkg actually supports instead of guessing from its version.
PROBE_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "$PROBE_ROOT"
  if [ -n "$STAGE_DIR" ]; then rm -rf "$STAGE_DIR"; fi
  return 0
}
trap cleanup EXIT
PROBE_DIR="$PROBE_ROOT/control"
mkdir -p "$PROBE_DIR/DEBIAN"
cat > "$PROBE_DIR/DEBIAN/control" <<EOL
Package: sponsorscout-probe
Version: 0
Architecture: $DEB_ARCH
Maintainer: SponsorScout <sponsorscout@localhost>
Description: capability probe
EOL

DEB_FLAGS=()
if dpkg-deb --build --root-owner-group "$PROBE_DIR" "$PROBE_ROOT/probe.deb" >/dev/null 2>&1; then
  DEB_FLAGS+=(--root-owner-group)
else
  echo "WARNING: this dpkg does not support --root-owner-group (needs dpkg >= 1.19)." >&2
  echo "         The package would install files owned by uid $(id -u)." >&2
fi

# A compressor is only usable if both the dpkg and the matching tool exist
# (xz needs xz-utils, zstd needs dpkg >= 1.21.18), so try them in order.
probe_compression() {
  dpkg-deb "${DEB_FLAGS[@]+"${DEB_FLAGS[@]}"}" -Z "$1" -S "$2" \
       --build "$PROBE_DIR" "$PROBE_ROOT/probe-$1.deb" >/dev/null 2>&1
}

COMPRESS_ARGS=()
COMPRESS_LABEL="dpkg default"
if probe_compression xz 1; then
  COMPRESS_ARGS=(-Z xz -S 1)
  COMPRESS_LABEL="xz -1"
elif probe_compression gzip 1; then
  COMPRESS_ARGS=(-Z gzip -S 1)
  COMPRESS_LABEL="gzip -1"
fi
echo "Compression: ${COMPRESS_LABEL}"

if [ "${DEB_COMPRESSION:-auto}" != "auto" ]; then
  DEB_COMPRESSION_LEVEL="${DEB_COMPRESSION_LEVEL:-3}"
  if probe_compression "$DEB_COMPRESSION" "$DEB_COMPRESSION_LEVEL"; then
    COMPRESS_ARGS=(-Z "$DEB_COMPRESSION" -S "$DEB_COMPRESSION_LEVEL")
    COMPRESS_LABEL="$DEB_COMPRESSION -$DEB_COMPRESSION_LEVEL"
  else
    echo "WARNING: -Z $DEB_COMPRESSION is not available here; keeping $COMPRESS_LABEL." >&2
  fi
fi

echo "Binary size: $(du -sh "$APP_DIR/$APP_NAME" | cut -f1)"
echo "Installed package tree size: $(du -sh "$APP_DIR" | cut -f1)"
echo "Compressing $(du -sm "$BUILD_TREE" | cut -f1) MB with ${COMPRESS_LABEL} (packaging is the slow step)…"

PACKAGE_START=$(date +%s)
if ! dpkg-deb "${DEB_FLAGS[@]+"${DEB_FLAGS[@]}"}" "${COMPRESS_ARGS[@]+"${COMPRESS_ARGS[@]}"}" \
     --build "$BUILD_TREE" "$DEB_TMP"; then
  rm -f "$DEB_TMP"
  echo "ERROR: dpkg-deb failed to build the package." >&2
  echo "If it complained about directory permissions, this repository lives on a" >&2
  echo "filesystem that cannot store them (WSL /mnt, NTFS, FAT). Copy the project" >&2
  echo "to a Linux filesystem such as ~/$(basename "$PWD") and build again." >&2
  exit 1
fi
mv -f "$DEB_TMP" "$DEB_OUT"
echo "Packaging took $(( $(date +%s) - PACKAGE_START ))s"
echo "Built $DEB_OUT  ($(du -sh "$DEB_OUT" | cut -f1))"
echo "Install with: sudo dpkg -i $DEB_OUT"

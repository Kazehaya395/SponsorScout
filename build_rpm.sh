#!/usr/bin/env bash
set -euo pipefail

# ── SponsorScout RPM build ──────────────────────────────────────────────────
# Builds dist/sponsorscout-<version>-<release>.<arch>.rpm from a fresh
# PyInstaller bundle with Playwright Chromium inside. Linux sibling of
# build_deb.sh (Debian/Ubuntu) and build_exe.ps1 (Windows); the three scripts
# deliberately share the same bundle, size-reduction, smoke-test and
# uninstall contract, and none of them wipes dist/, so the .exe, .deb and
# .rpm can sit side by side in dist/.
#
# Environment knobs:
#   RPM_COMPRESSION=auto|xz|gz|zstd
#       Payload compressor (default: auto = fast xz -1, installable by every
#       rpm). zstd is opt-in because a zstd rpm needs rpm >= 4.14 to install
#       (Fedora 31+, RHEL 9+).
#
# Never run this script with sudo: rpmbuild only writes local files, and a
# root-owned .build/ or venv would break every later run.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

APP_NAME="SponsorScout"
PKG_NAME="sponsorscout"
RPM_RELEASE="1"

BUILD_DIR=".build/rpm"
VENV_DIR=".build/rpm-venv"
DIST_DIR="dist"
PKG_ROOT="$ROOT_DIR/.build/rpm/pkgroot"   # staged install tree (/,opt,usr)
APP_DIR="$PKG_ROOT/opt/$PKG_NAME"         # the PyInstaller onedir bundle
TOPDIR="$ROOT_DIR/.build/rpm/rpmbuild"    # rpmbuild _topdir (absolute)

# The EXIT trap must never trip over unset variables.
PROBE_TOPDIR=""
STAGE_DIR=""

cleanup() {
  if [ -n "$PROBE_TOPDIR" ]; then rm -rf "$PROBE_TOPDIR"; fi
  if [ -n "$STAGE_DIR" ]; then rm -rf "$STAGE_DIR"; fi
  return 0
}
trap cleanup EXIT

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }
}

init_topdir() {
  mkdir -p "$1/BUILD" "$1/BUILDROOT" "$1/RPMS" "$1/SOURCES" "$1/SPECS" "$1/SRPMS"
}

# rpmbuild only writes local .rpm files, so building must never run as root.
# Running with sudo would also create root-owned build artifacts and could
# trigger PEP 668 "externally-managed-environment" protection on Debian.
if [ "$(id -u)" -eq 0 ]; then
  cat >&2 <<'EOF'
ERROR: do not run build_rpm.sh with sudo.
This script only creates dist/sponsorscout-<version>-<release>.<arch>.rpm and
does not need root. Run it as your normal user:

  ./build_rpm.sh
EOF
  exit 1
fi

need python3

if ! command -v rpmbuild >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: rpmbuild not found.
Install the RPM build tools for your distribution, then run ./build_rpm.sh again:

  Fedora / RHEL / Rocky:  sudo dnf install rpm-build
  openSUSE:                sudo zypper install rpm-build
  Debian / Ubuntu / WSL:   sudo apt install rpm
EOF
  exit 1
fi

RPM_ARCH="$(rpmbuild --eval '%{_arch}')"
if [ -z "$RPM_ARCH" ]; then
  echo "ERROR: rpmbuild --eval '%{_arch}' returned no architecture." >&2
  exit 1
fi

# Debian/Ubuntu may ship Python without the venv/ensurepip modules. Fail with an
# actionable message instead of falling back to a system-wide pip install.
if ! python3 -c "import venv" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
ERROR: Python's venv module is missing.
Install it first, then run ./build_rpm.sh again:

  Debian/Ubuntu:  sudo apt install python3-venv
  Fedora/RHEL:    sudo dnf install python3
EOF
  exit 1
fi

# ── Fresh package tree ──────────────────────────────────────────────────────
# The disposable venv at .build/rpm-venv is kept between runs; only the
# package staging area is wiped. dist/ is NOT wiped: the .exe and .deb built
# by build_exe.ps1 / build_deb.sh live there too.
if [ -e "$BUILD_DIR" ] && [ ! -w "$BUILD_DIR" ]; then
  echo "ERROR: $BUILD_DIR is not writable by the current user." >&2
  echo "A previous sudo build may have created it. Remove it once, then retry:" >&2
  echo "  sudo rm -rf $BUILD_DIR && ./build_rpm.sh" >&2
  exit 1
fi
rm -rf "$BUILD_DIR"
mkdir -p "$PKG_ROOT" "$DIST_DIR"

# ── Fail fast: verify rpmbuild BEFORE the long PyInstaller build ────────────
# A broken rpm-build install (missing brp scripts/macros) otherwise only
# surfaces after ~10 minutes of bundling. The probe package is tiny.
PROBE_TOPDIR="$ROOT_DIR/.build/rpm/probe"
init_topdir "$PROBE_TOPDIR"
PROBE_SPEC="$PROBE_TOPDIR/SPECS/probe.spec"
cat > "$PROBE_SPEC" <<'EOL'
Name:    sponsorscout-probe
Version: 0
Release: 0
Summary: rpmbuild capability probe (never installed)
License: MIT
%global debug_package %{nil}
%global __os_install_post %{nil}
%global __arch_install_post %{nil}
%global __strip /bin/true
%description
Throwaway package build_rpm.sh uses to verify rpmbuild before the long build.
%install
mkdir -p %{buildroot}/usr/share/sponsorscout-probe
echo ok > %{buildroot}/usr/share/sponsorscout-probe/probe
%files
%defattr(-,root,root)
/usr/share/sponsorscout-probe/probe
EOL

echo "Checking that rpmbuild can produce a trivial package..."
if ! rpmbuild -bb --define "_topdir $PROBE_TOPDIR" "$PROBE_SPEC"; then
  cat >&2 <<'EOF'
ERROR: rpmbuild failed on a trivial probe package (see output above).
The rpm-build installation is incomplete; try reinstalling it:

  Debian/Ubuntu:  sudo apt install --reinstall rpm
  Fedora/RHEL:    sudo dnf reinstall rpm-build
EOF
  exit 1
fi

# ── Payload compression probe ───────────────────────────────────────────────
# The staged tree is ~1.5 GB (PySide6 + the bundled Playwright Chromium), so
# compression dominates the packaging time. Probe which payload flags this
# rpmbuild actually supports instead of guessing from its version:
#   * auto/xz: w1T.xzdio (xz -1, multithreaded) -> w1.xzdio -> w1.gzdio,
#     mirroring build_deb.sh's fast "xz -1" default that every rpm can read.
#   * zstd is opt-in only (RPM_COMPRESSION=zstd ./build_rpm.sh) because a
#     zstd rpm can only be installed by rpm >= 4.14 (Fedora 31+, RHEL 9+).
echo "Probing rpmbuild payload compression support..."
probe_payload() {
  # $1 = payload flags string for _binary_payload.
  if [ -n "$1" ]; then
    rpmbuild -bb --define "_topdir $PROBE_TOPDIR" \
         --define "_binary_payload $1" "$PROBE_SPEC" >/dev/null 2>&1
  else
    rpmbuild -bb --define "_topdir $PROBE_TOPDIR" "$PROBE_SPEC" >/dev/null 2>&1
  fi
}

payload_label() {
  case "$1" in
    w1T.zstdio|w1.zstdio) echo "zstd -1" ;;
    w1T.xzdio)           echo "xz -1 (threads)" ;;
    w1.xzdio)            echo "xz -1" ;;
    w1.gzdio)            echo "gzip -1" ;;
    *)                   echo "rpmbuild default" ;;
  esac
}

PAYLOAD_DEFINE=""
COMPRESS_LABEL="rpmbuild default"
RPM_COMPRESSION_CHOICE="${RPM_COMPRESSION:-auto}"
case "$RPM_COMPRESSION_CHOICE" in
  zstd)         CANDIDATES=(w1T.zstdio w1.zstdio w1T.xzdio w1.xzdio w1.gzdio) ;;
  gz|gzip)      CANDIDATES=(w1.gzdio) ;;
  xz|auto|"")   CANDIDATES=(w1T.xzdio w1.xzdio w1.gzdio) ;;
  *)
    echo "WARNING: unknown RPM_COMPRESSION=\"$RPM_COMPRESSION_CHOICE\"" >&2
    echo "         (expected auto, xz, gz or zstd); using auto." >&2
    CANDIDATES=(w1T.xzdio w1.xzdio w1.gzdio)
    ;;
esac

for cand in "${CANDIDATES[@]}"; do
  if probe_payload "$cand"; then
    PAYLOAD_DEFINE="$cand"
    COMPRESS_LABEL="$(payload_label "$cand")"
    break
  fi
done

if [ "$RPM_COMPRESSION_CHOICE" = "zstd" ]; then
  case "$PAYLOAD_DEFINE" in
    *zstdio*) ;;
    *)
      echo "WARNING: this rpmbuild cannot compress with zstd (needs rpm >= 4.14);" >&2
      echo "         keeping ${COMPRESS_LABEL}." >&2
      ;;
  esac
fi
echo "Compression: ${COMPRESS_LABEL}"
rm -rf "$PROBE_TOPDIR"
PROBE_TOPDIR=""

# All build dependencies live in this disposable, git-ignored environment.
# This avoids PEP 668 and never modifies the system Python installation.
if [ -e "$VENV_DIR" ] && [ ! -w "$VENV_DIR" ]; then
  echo "ERROR: $VENV_DIR is not writable by the current user." >&2
  echo "A previous sudo build may have created it. Remove it once, then retry:" >&2
  echo "  sudo rm -rf $VENV_DIR && ./build_rpm.sh" >&2
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
    echo "  sudo rm -rf $VENV_DIR && ./build_rpm.sh" >&2
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

  rm -rf $VENV_DIR && ./build_rpm.sh
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
  echo "  rm -rf $VENV_DIR && ./build_rpm.sh" >&2
  exit 1
fi

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
# caches and obviously-unneeded metadata to stay safe. rpmbuild's own
# brp-strip / find-debuginfo are disabled in the generated spec for the same
# reason (see the %global lines there).
echo "Reducing .rpm size (safe mode — no binary stripping)..."

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
# ships inside the .rpm and sponsorscout/paths.py (exe_dir/_playwright) and
# the /usr/bin/sponsorscout launcher can point PLAYWRIGHT_BROWSERS_PATH at it
# on the user's machine. Otherwise the downloaded browser only lands in the
# build machine's ~/.cache/ms-playwright and every `provider=auto` career
# target fails with "Executable doesn't exist".
echo "Installing Playwright Chromium into bundle ($APP_DIR/_playwright)..."
PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/_playwright" "$PYTHON" -m playwright install chromium
if [ ! -d "$APP_DIR/_playwright" ]; then
  echo "ERROR: Playwright browsers were NOT installed into $APP_DIR/_playwright -" >&2
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
echo "Smoke-testing the packaged app (Playwright + bundled Chromium)..."
if ! PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/_playwright" \
     "$APP_DIR/$APP_NAME" --self-check --self-check-browser; then
  echo "ERROR: the packaged app failed --self-check; aborting the build." >&2
  echo "Re-run manually for full details:" >&2
  echo "  PLAYWRIGHT_BROWSERS_PATH=\"$APP_DIR/_playwright\" \\" >&2
  echo "    \"$APP_DIR/$APP_NAME\" --self-check --self-check-browser" >&2
  exit 1
fi

mkdir -p "$PKG_ROOT/usr/bin"
cat > "$PKG_ROOT/usr/bin/sponsorscout" <<'EOL'
#!/bin/sh
# SponsorScout launcher
# The binary lives at /opt/sponsorscout/SponsorScout after rpmbuild copies the
# *contents* of .build/rpm/pkgroot/opt/sponsorscout/ into /opt/sponsorscout/.

APP_BIN="/opt/sponsorscout/SponsorScout"

# Verify the binary exists and is executable.
if [ ! -f "$APP_BIN" ]; then
  echo "SponsorScout: ERROR — $APP_BIN not found." >&2
  echo "  The .rpm package may not have installed correctly." >&2
  echo "  Try reinstalling:  sudo rpm -e sponsorscout && sudo rpm -i sponsorscout-*.rpm" >&2
  exit 1
fi
if [ ! -x "$APP_BIN" ]; then
  echo "SponsorScout: ERROR — $APP_BIN is not executable." >&2
  echo "  Try:  sudo chmod +x $APP_BIN" >&2
  exit 1
fi

# Prefer the Chromium bundle shipped inside the package (/opt/sponsorscout/_playwright,
# installed by build_rpm.sh); fall back to the user's Playwright cache.
if [ -z "$PLAYWRIGHT_BROWSERS_PATH" ]; then
  if [ -d "/opt/sponsorscout/_playwright" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="/opt/sponsorscout/_playwright"
  else
    export PLAYWRIGHT_BROWSERS_PATH="${HOME}/.cache/ms-playwright"
  fi
fi

exec "$APP_BIN" "$@"
EOL
chmod 755 "$PKG_ROOT/usr/bin/sponsorscout"

mkdir -p "$PKG_ROOT/usr/share/applications"
cat > "$PKG_ROOT/usr/share/applications/sponsorscout.desktop" <<EOL
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

# Install hicolor icon theme entries so the desktop file's Icon=sponsorscout
# resolves on the panel / app grid.
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
  dst="$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
  mkdir -p "$dst"
  cp "$src" "$dst/sponsorscout.png"
done

# Verify the binary was copied and is a valid ELF executable.
if [ ! -x "$APP_DIR/$APP_NAME" ]; then
  echo "ERROR: $APP_NAME binary not found in $APP_DIR after PyInstaller build." >&2
  echo "Contents of $APP_DIR:" >&2
  ls -la "$APP_DIR/" >&2
  exit 1
fi

# ── Filesystem permissions ──────────────────────────────────────────────────
# WSL drvfs mounts (/mnt/...), NTFS and FAT report every file as 0777 and ignore
# chmod. rpmbuild would then record world-writable modes in the package (and
# rpm's own helper scripts can misbehave). Normalise in place when the
# filesystem honours chmod; otherwise stage the package tree AND the rpmbuild
# topdir on a Linux filesystem and build from there.
if chmod 755 "$PKG_ROOT" 2>/dev/null \
   && [ "$(stat -c '%a' "$PKG_ROOT" 2>/dev/null || echo 777)" = "755" ]; then
  echo "Normalising package permissions in place..."
  find "$PKG_ROOT" -type d -exec chmod 755 {} + 2>/dev/null || true
  find "$PKG_ROOT" -type f -exec chmod go-w {} + 2>/dev/null || true
  chmod 755 "$PKG_ROOT/usr/bin/sponsorscout" 2>/dev/null || true
else
  echo "This filesystem does not support Unix permissions (WSL /mnt, NTFS, FAT)." >&2
  echo "Staging the package and rpmbuild trees on a Linux filesystem instead..." >&2
  STAGE_DIR="$(mktemp -d)"
  mkdir -p "$STAGE_DIR/pkgroot" "$STAGE_DIR/rpmbuild"
  cp -a "$PKG_ROOT/." "$STAGE_DIR/pkgroot/"
  chmod -R u=rwX,go=rX "$STAGE_DIR/pkgroot"
  chmod 755 "$STAGE_DIR/pkgroot/usr/bin/sponsorscout"
  PKG_ROOT="$STAGE_DIR/pkgroot"
  APP_DIR="$PKG_ROOT/opt/$PKG_NAME"
  TOPDIR="$STAGE_DIR/rpmbuild"
fi

init_topdir "$TOPDIR"

# ── Spec file ───────────────────────────────────────────────────────────────
# Generated on every run (lives under the git-ignored .build/ tree, so it can
# never be confused with the tracked SponsorScout.spec PyInstaller config).
# The staged tree is copied verbatim into %{buildroot}; %defattr(-,root,root)
# makes the unprivileged build record root:root ownership instead of the
# builder's uid/gid (the rpm twin of dpkg's --root-owner-group).
SPEC="$TOPDIR/SPECS/sponsorscout.spec"
cat > "$SPEC" <<EOL
# Generated by ./build_rpm.sh — do not edit (regenerated on every build).
Name: $PKG_NAME
Version: $VERSION
Release: $RPM_RELEASE
Summary: Job sponsorship search application
License: MIT
URL: https://sponsorscout.app

# PyInstaller bundles are NOT distribution binaries: rpm's brp-strip and
# find-debuginfo would rewrite them, and stripping these C-extensions causes
# intermittent SIGSEGV / corrupted rendering (the exact failure build_deb.sh
# documents). Ship the payload exactly as PyInstaller built it.
%global debug_package %{nil}
%global __os_install_post %{nil}
%global __arch_install_post %{nil}
%global __strip /bin/true

%description
A local desktop app for finding jobs with visa sponsorship signals.

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}
cp -a "$PKG_ROOT"/. %{buildroot}/

%files
%defattr(-,root,root)
/opt/sponsorscout
/usr/bin/sponsorscout
/usr/share/applications/sponsorscout.desktop
/usr/share/icons/hicolor/*/apps/sponsorscout.png
EOL

cat >> "$SPEC" <<'EOL'
%post

set -e

# Update desktop database and icon cache (best-effort, like the .deb postinst).
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

# Last-resort fallback: install Playwright Chromium into the user's cache.
# Normally NOT needed — build_rpm.sh ships the browsers inside
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
  echo "SponsorScout: installing Playwright Chromium browser (one-time, ~150 MB)..."
  "$PYTHON" -m playwright install chromium >/dev/null 2>&1 || true
fi

exit 0
EOL

cat >> "$SPEC" <<'EOL'
%preun

set -eu

# Stop the GUI before rpm removes package-owned files. This also runs during
# upgrades, but it never removes user data.
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

cat >> "$SPEC" <<'EOL'
%postun

set -eu

# rpm passes the number of instances of this package still installed when the
# scriptlet finishes: 0 = final removal, anything else = an upgraded or
# parallel copy remains (rpm-scriptlets(7)). Never purge user data during an
# upgrade — purge only for a real uninstall, like the Debian postrm does for
# its "remove|purge" actions.
if [ "$1" -ne 0 ]; then
  exit 0
fi

# Stop the GUI before removing the package files and database.
for sig in TERM KILL; do
  pids=$(pgrep -x "SponsorScout" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "SponsorScout: stopping running instance(s) (SIG$sig)..."
    # shellcheck disable=SC2086
    kill -s "$sig" $pids 2>/dev/null || true
    sleep 1
  fi
done

# rpm normally removes package-owned files itself. Keep this explicit cleanup
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

# Also clean explicit overrides when rpm inherited them from the invoking
# environment. This does not inspect arbitrary files or delete a drive root.
if [ -n "${SPONSORSCOUT_DATA_DIR:-}" ]; then
  purge_path "$SPONSORSCOUT_DATA_DIR"
fi
if [ -n "${SPONSORSCOUT_DB_PATH:-}" ]; then
  rm -f -- "$SPONSORSCOUT_DB_PATH" "$SPONSORSCOUT_DB_PATH-wal" "$SPONSORSCOUT_DB_PATH-shm"
fi

exit 0
EOL

# ── Package ─────────────────────────────────────────────────────────────────
# Notes for this step:
#
#   * The staged tree is ~1.5 GB (PySide6 + the bundled Playwright Chromium),
#     so compression dominates the build time; the payload flags were probed
#     near the top of this script (COMPRESS_LABEL reports what was selected).
#   * %defattr(-,root,root) in the spec stops the unprivileged build from
#     recording the builder's uid/gid (rpm twin of dpkg's --root-owner-group).
#   * The .partial + rename keeps a half-written archive from looking finished.
RPM_BASE="${PKG_NAME}-${VERSION}-${RPM_RELEASE}.${RPM_ARCH}"
RPM_NAME="$RPM_BASE.rpm"
RPM_OUT="$DIST_DIR/$RPM_NAME"
RPM_TMP="$DIST_DIR/.$RPM_BASE.rpm.partial"
rm -f "$RPM_TMP" "$RPM_OUT"

echo "Binary size: $(du -sh "$APP_DIR/$APP_NAME" | cut -f1)"
echo "Installed package tree size: $(du -sh "$PKG_ROOT" | cut -f1)"
echo "Compressing $(du -sm "$PKG_ROOT" | cut -f1) MB with ${COMPRESS_LABEL} (packaging is the slow step)..."

RPM_ARGS=(--define "_topdir $TOPDIR")
if [ -n "$PAYLOAD_DEFINE" ]; then
  RPM_ARGS+=(--define "_binary_payload $PAYLOAD_DEFINE")
fi

PACKAGE_START=$(date +%s)
if ! rpmbuild -bb "${RPM_ARGS[@]}" "$SPEC"; then
  echo "ERROR: rpmbuild failed to build the package." >&2
  echo "If it complained about file permissions, this repository lives on a" >&2
  echo "filesystem that cannot store them (WSL /mnt, NTFS, FAT). Copy the project" >&2
  echo "to a Linux filesystem such as ~/$(basename "$PWD") and build again." >&2
  exit 1
fi

BUILT_RPM="$TOPDIR/RPMS/$RPM_ARCH/$RPM_NAME"
if [ ! -f "$BUILT_RPM" ]; then
  echo "ERROR: rpmbuild reported success but $BUILT_RPM is missing." >&2
  echo "Contents of $TOPDIR/RPMS:" >&2
  find "$TOPDIR/RPMS" -name '*.rpm' >&2 2>/dev/null || true
  exit 1
fi

# Cross-filesystem mv (the /tmp staging fallback) copies first, so only the
# .partial file can ever be incomplete; the final rename is same-fs and atomic.
mv -f "$BUILT_RPM" "$RPM_TMP"
mv -f "$RPM_TMP" "$RPM_OUT"
echo "Packaging took $(( $(date +%s) - PACKAGE_START ))s"
echo "Built $RPM_OUT  ($(du -sh "$RPM_OUT" | cut -f1))"
echo "Install with: sudo dnf install $RPM_OUT      (Fedora / RHEL / Rocky)"
echo "          or: sudo zypper install $RPM_OUT   (openSUSE)"
echo "          or: sudo rpm -i $RPM_OUT           (any RPM distro)"


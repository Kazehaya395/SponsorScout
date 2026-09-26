"""Regression checks for packaged uninstall cleanup.

The Windows installer and Debian build/package scripts are shell/compiler
artifacts rather than Python modules, so these tests validate their
safety-critical build and uninstall contract without requiring Inno Setup or a
Linux dpkg installation on the test machine.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INN = (ROOT / "installer.iss").read_text(encoding="utf-8")
DEB = (ROOT / "build_deb.sh").read_text(encoding="utf-8")


def test_windows_uninstaller_purges_app_data_but_not_shared_browser_cache():
    assert "[UninstallDelete]" in INN
    assert "procedure PurgeUserData" in INN
    assert "usPostUninstall" in INN
    assert "SPONSORSCOUT_DATA_DIR" in INN
    assert "SPONSORSCOUT_DB_PATH" in INN
    assert "ms-playwright" not in INN.lower()


def test_debian_uninstaller_purges_on_remove_but_skips_upgrade():
    postrm = DEB.split('cat > "$DEBIAN_DIR/postrm"', 1)[1].split("EOL\nchmod", 1)[0]
    assert 'case "$ACTION" in' in postrm
    assert "remove|purge)" in postrm
    assert "*) exit 0 ;;" in postrm
    assert "SPONSORSCOUT_DATA_DIR" in postrm
    assert "SPONSORSCOUT_DB_PATH" in postrm
    assert "ms-playwright" not in postrm.lower()
    assert 'cat > "$DEBIAN_DIR/prerm"' in DEB


def test_debian_build_uses_local_venv_and_does_not_require_sudo():
    assert "do not run build_deb.sh with sudo" in DEB
    assert 'if [ "$(id -u)" -eq 0 ]' in DEB
    assert 'VENV_DIR=".build/deb-venv"' in DEB
    assert 'python3 -m venv "$VENV_DIR"' in DEB
    assert '"$PYTHON" -m pip install -r requirements.txt' in DEB
    assert "python3 -m pip install" not in DEB
    assert "sudo apt install python3-venv" in DEB
    assert '[ ! -w "$VENV_DIR" ]' in DEB
    # A venv may exist with a working bin/python but no pip, so the health of
    # the environment must be verified with a real "pip --version" probe.
    assert "venv_has_pip" in DEB
    assert '-m pip --version' in DEB
    assert "has no working pip - recreating it" in DEB
    # ensurepip repairs a half-bootstrapped venv before giving up.
    assert "-m ensurepip --upgrade" in DEB
    assert "could not provide pip inside" in DEB


def test_debian_packaging_sets_root_owner_and_reports_timing():
    # The unprivileged build must not record the builder's uid/gid.
    assert "--root-owner-group" in DEB
    # xz -1 instead of the slow dpkg default (xz -6) on the ~1.5 GB payload.
    assert "probe_compression xz 1" in DEB
    # zstd is opt-in only, because older dpkg cannot install a zstd .deb.
    assert 'DEB_COMPRESSION:-auto' in DEB
    # Never leave a truncated archive that looks like a finished package.
    assert ".deb.partial" in DEB
    assert 'mv -f "$DEB_TMP" "$DEB_OUT"' in DEB
    assert "Packaging took" in DEB


def test_debian_packaging_handles_filesystems_without_permissions():
    # WSL /mnt, NTFS and FAT report 0777 and ignore chmod, which makes dpkg-deb
    # abort with "control directory has bad permissions 777".
    assert "does not support Unix permissions" in DEB
    assert "chmod 755 \"$DEBIAN_DIR\"" in DEB
    assert "stat -c '%a'" in DEB
    # Fall back to a Linux filesystem when the in-place chmod has no effect.
    assert 'STAGE_DIR="$(mktemp -d)"' in DEB
    assert 'cp -a "$BUILD_DIR/." "$STAGE_DIR/"' in DEB
    # The build must run against whichever tree is in use.
    assert 'BUILD_TREE="$BUILD_DIR"' in DEB
    assert 'BUILD_TREE="$STAGE_DIR"' in DEB
    assert '--build "$BUILD_TREE" "$DEB_TMP"' in DEB

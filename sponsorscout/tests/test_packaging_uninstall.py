"""Regression checks for packaged uninstall cleanup.

The Windows installer and the Debian/RPM build scripts are shell/compiler
artifacts rather than Python modules, so these tests validate their
safety-critical build and uninstall contract without requiring Inno Setup or a
dpkg/rpmbuild installation on the test machine.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INN = (ROOT / "installer.iss").read_text(encoding="utf-8")
DEB = (ROOT / "build_deb.sh").read_text(encoding="utf-8")
RPM = (ROOT / "build_rpm.sh").read_text(encoding="utf-8")


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


def test_rpm_uninstaller_purges_on_erase_but_never_on_upgrade():
    postun = RPM.split("%postun", 1)[1].split("EOL", 1)[0]
    # rpm passes the number of instances still installed as $1: 0 = final
    # removal, >0 = an upgraded/parallel copy remains (rpm-scriptlets(7)).
    assert '[ "$1" -ne 0 ]' in postun
    # The guard must run before any destructive work.
    assert postun.index('[ "$1" -ne 0 ]') < postun.index("purge_path")
    assert "purge_path" in postun
    assert "SPONSORSCOUT_DATA_DIR" in postun
    assert "SPONSORSCOUT_DB_PATH" in postun
    assert "ms-playwright" not in postun.lower()
    assert 'case "$ACTION" in' not in postun  # that case-statement is Debian-only


def test_rpm_scriptlets_stop_the_gui_and_refresh_the_desktop_entry():
    preun = RPM.split("%preun", 1)[1].split("EOL", 1)[0]
    # The GUI is stopped on erase AND upgrade (mirrors the Debian prerm), but
    # the upgrade path must never touch user data.
    assert 'pgrep -x "SponsorScout"' in preun
    assert "kill -s" in preun
    assert "purge_path" not in preun
    assert "SPONSORSCOUT_DATA_DIR" not in preun
    # %post mirrors the Debian postinst: desktop/icon caches + Chromium fallback.
    post = RPM.split("%post\n", 1)[1].split("EOL", 1)[0]
    assert "update-desktop-database" in post
    assert "gtk-update-icon-cache" in post
    assert "-m playwright install chromium" in post


def test_rpm_build_uses_local_venv_and_does_not_require_sudo():
    assert "do not run build_rpm.sh with sudo" in RPM
    assert 'if [ "$(id -u)" -eq 0 ]' in RPM
    assert 'VENV_DIR=".build/rpm-venv"' in RPM
    assert 'python3 -m venv "$VENV_DIR"' in RPM
    assert '"$PYTHON" -m pip install -r requirements.txt' in RPM
    assert "python3 -m pip install" not in RPM
    assert "sudo apt install python3-venv" in RPM
    assert '[ ! -w "$VENV_DIR" ]' in RPM
    # A venv may exist with a working bin/python but no pip, so the health of
    # the environment must be verified with a real "pip --version" probe.
    assert "venv_has_pip" in RPM
    assert "-m pip --version" in RPM
    assert "has no working pip - recreating it" in RPM
    assert "-m ensurepip --upgrade" in RPM
    assert "could not provide pip inside" in RPM
    # rpmbuild must be validated on a trivial package BEFORE the long build,
    # otherwise an incomplete rpm-build install only fails after ~10 minutes.
    assert "rpmbuild not found" in RPM
    assert "trivial probe package" in RPM


def test_rpm_never_strips_pyinstaller_binaries():
    # rpm's brp-strip / find-debuginfo would rewrite the bundle exactly like
    # the forbidden strip --strip-unneeded (SIGSEGV / corrupted rendering).
    assert "%global debug_package %{nil}" in RPM
    assert "%global __os_install_post %{nil}" in RPM
    assert "%global __strip /bin/true" in RPM
    assert "no binary stripping" in RPM


def test_rpm_packaging_is_root_owned_atomic_and_reports_timing():
    # The unprivileged build must not record the builder's uid/gid.
    assert "%defattr(-,root,root)" in RPM
    # Never leave a truncated archive that looks like a finished package.
    assert ".rpm.partial" in RPM
    assert 'mv -f "$RPM_TMP" "$RPM_OUT"' in RPM
    assert "Packaging took" in RPM
    # The payload compressor is probed, not guessed; zstd stays opt-in.
    assert "_binary_payload" in RPM
    assert "RPM_COMPRESSION" in RPM


def test_rpm_handles_filesystems_without_permissions():
    # WSL /mnt, NTFS and FAT report 0777 and ignore chmod, which would put
    # world-writable modes into the rpm.
    assert "does not support Unix permissions" in RPM
    assert 'STAGE_DIR="$(mktemp -d)"' in RPM
    assert 'cp -a "$PKG_ROOT/." "$STAGE_DIR/pkgroot/"' in RPM
    assert 'chmod 755 "$PKG_ROOT/usr/bin/sponsorscout"' in RPM
    assert "stat -c '%a'" in RPM
    # The build must run against whichever tree/topdir is in use.
    assert 'PKG_ROOT="$STAGE_DIR/pkgroot"' in RPM
    assert 'TOPDIR="$STAGE_DIR/rpmbuild"' in RPM


def test_rpm_bundles_chromium_and_smoke_tests_the_bundle():
    assert "-m playwright install chromium" in RPM
    assert "--self-check --self-check-browser" in RPM
    assert "_playwright" in RPM
    # The generated spec must ship every packaged entry point.
    assert "/usr/bin/sponsorscout" in RPM
    assert "/usr/share/applications/sponsorscout.desktop" in RPM
    assert "/opt/sponsorscout" in RPM

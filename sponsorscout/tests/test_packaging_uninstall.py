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


# ── Build-script correctness (2026-09 audit) ─────────────────────────────────

def test_windows_installer_writes_browser_path_to_the_per_user_hive():
    """HKCU, not HKLM.

    {app} is {autopf}\\SponsorScout — a PER-USER directory — so a machine-wide
    environment variable would point every account on the PC at whichever user
    installed last, and everyone else would fail to find Chromium.
    """
    registry = INN.split("[Registry]", 1)[1].split("[Tasks]", 1)[0]
    assert "PLAYWRIGHT_BROWSERS_PATH" in registry
    # Judge the directives only — the section carries a comment explaining
    # WHY it is HKCU, and that prose must not be mistaken for configuration.
    directives = [ln for ln in registry.splitlines()
                  if ln.strip() and not ln.strip().startswith(";")]
    body = "\n".join(directives)
    assert "Root: HKCU" in body
    assert "Root: HKLM" not in body, (
        "a machine-wide env var must not point at a per-user install path")
    # `Permissions: everyone-modify` on that variable would let any non-admin
    # rewrite something injected into every new process on the machine.
    assert "everyone-modify" not in body
    # uninsdeletevalue must only clear the installing user's own value.
    assert "uninsdeletevalue" in body


def test_installer_has_no_placeholder_publisher_url():
    """A 'yourusername' placeholder shipped to users as the support URL."""
    assert "yourusername" not in INN.lower()
    # ...and it must agree with pyproject, which has the real repository.
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    repo = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in pyproject.splitlines()
        if line.strip().startswith("Repository")
    )
    assert repo in INN


def test_debian_arch_is_derived_not_hardcoded():
    """A hard-coded 'amd64' produces an uninstallable .deb on arm64."""
    assert 'DEB_ARCH="$(dpkg --print-architecture' in DEB
    header = DEB.split("APP_DIR=", 1)[0]
    assert 'DEB_ARCH="amd64"\n' not in header.replace(
        'DEB_ARCH="amd64"\nfi\n', ""), (
        "DEB_ARCH must not be pinned before the dpkg-derived value")


def test_readme_documents_the_rpm_artifact():
    """RPM support is built and must be visible where users look for it."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # The download table (before the Italian half starts) must list it.
    download = readme.split("## 📥 Download", 1)[1].split("## 📸", 1)[0]
    assert ".rpm" in download, "the RPM is missing from the Download table"
    assert ".deb" in download and ".exe" in download
    # ...and so must the Italian mirror, not just the English one.
    it_download = readme.split("## 📥 Scarica", 1)[1].split("## 📸", 1)[0]
    assert ".rpm" in it_download, "the RPM is missing from the Italian table"
    # Uninstalling must mention it too — the .deb-only wording was stale.
    uninstall = readme.split("### Uninstalling", 1)[1].split("## 🧰", 1)[0]
    assert ".rpm" in uninstall
    it_uninstall = readme.split("### Disinstallazione", 1)[1].split("## 🧰", 1)[0]
    assert ".rpm" in it_uninstall


def test_readme_artifact_names_match_the_build_scripts():
    """`<version>`/`<arch>` placeholders must reflect the real output names."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # build_deb.sh: DEB_OUT="$DIST_DIR/${PKG_NAME}_${VERSION}_${DEB_ARCH}.deb"
    assert "${PKG_NAME}_${VERSION}_${DEB_ARCH}.deb" in DEB
    assert "sponsorscout_<version>_<arch>.deb" in readme
    assert "sponsorscout_<versione>_<arch>.deb" in readme
    # build_rpm.sh: RPM_BASE="${PKG_NAME}-${VERSION}-${RPM_RELEASE}.${RPM_ARCH}"
    assert '${PKG_NAME}-${VERSION}-${RPM_RELEASE}.${RPM_ARCH}"' in RPM
    assert 'RPM_RELEASE="1"' in RPM  # the documented "-1." release is real
    assert "sponsorscout-<version>-1.<arch>.rpm" in readme
    assert "sponsorscout-<versione>-1.<arch>.rpm" in readme
    # A stale hard-coded amd64 in the docs would now be wrong.
    assert "sponsorscout_<version>_amd64.deb" not in readme


def test_readme_documents_the_opt_in_browser_download():
    """The auto-download behaviour changed; both languages must say so."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.count("SPONSORSCOUT_AUTO_INSTALL_BROWSERS") >= 4, (
        "document the opt-in flag in EN + IT, troubleshooting + requirements")
    assert "SPONSORSCOUT_AUTO_INSTALL_BROWSERS" in readme


def test_debian_third_party_test_cleanup_is_not_a_no_op():
    """`-not -path '*/sponsorscout/*'` matched the WHOLE bundle.

    APP_DIR is .build/deb/opt/sponsorscout, so every path inside it matched
    that glob and the find removed nothing while still claiming a 5-20 MB
    saving. Only our own test package should be spared.
    """
    guard = [ln for ln in DEB.splitlines()
             if "-not -path" in ln and "test" in ln.lower()]
    assert guard, "the third-party tests cleanup rule disappeared"
    assert any("*/sponsorscout/tests" in g for g in guard), (
        "the exclusion must target sponsorscout/tests specifically, not the "
        "entire bundle")
    assert not any('"*/sponsorscout/*"' in g for g in guard), (
        "that glob matches every path under APP_DIR, so the rule is a no-op")


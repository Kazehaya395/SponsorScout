"""Regression checks for packaged uninstall cleanup.

The Windows installer and Debian package are shell/compiler scripts rather than
Python modules, so these tests validate the safety-critical uninstall contract
without requiring Inno Setup or a Linux dpkg installation on the test machine.
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

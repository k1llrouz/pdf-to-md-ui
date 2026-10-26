"""The update check, installing an update, and the title bar colour."""

import hashlib
import io
import json
import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtWidgets import QApplication, QPushButton

import gui
import updates
from version import __version__


def pump(until, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return until()


# -- comparing versions ---------------------------------------------------------- #


@pytest.mark.parametrize(
    "latest, current, newer",
    [
        ("v1.2.0", "1.1.0", True),
        ("1.1.1", "1.1.0", True),
        ("v1.10.0", "1.9.0", True),  # numbers, not text
        ("v1.1.0", "1.1.0", False),
        ("v1.1", "1.1.0", False),
        ("v1.0.9", "1.1.0", False),
        ("v2.0.0-beta", "1.1.0", True),
        ("nightly", "1.1.0", False),
    ],
)
def test_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


# -- asking GitHub ------------------------------------------------------------------ #


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


REAL_FETCH = updates.fetch_latest  # before the autouse fixture swaps it out


@pytest.fixture
def github(monkeypatch):
    """The real fetch_latest against a stand-in for GitHub's answer."""
    monkeypatch.setattr(updates, "fetch_latest", REAL_FETCH)
    answer = {}

    def urlopen(request, timeout):
        answer["request"] = request
        if isinstance(answer.get("body"), Exception):
            raise answer["body"]
        return Response(answer["body"])

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    return answer


def test_reads_the_latest_release(github):
    github["body"] = json.dumps(
        {"tag_name": "v1.2.0", "html_url": "https://github.com/k1llrouz/pdf-to-md-ui/releases/tag/v1.2.0"}
    ).encode()

    assert updates.fetch_latest() == {
        "version": "1.2.0",
        "url": "https://github.com/k1llrouz/pdf-to-md-ui/releases/tag/v1.2.0",
    }
    assert github["request"].get_header("User-agent").startswith("pdf-to-md-ui/")


@pytest.mark.parametrize("body", [OSError("offline"), b"<html>rate limited</html>", b"{}"])
def test_no_answer_is_not_an_error(github, body):
    github["body"] = body

    assert updates.fetch_latest() is None


def test_check_answers_on_the_window_thread(qapp, monkeypatch):
    monkeypatch.setattr(updates, "fetch_latest", lambda: {"version": "9.9.9", "url": "u"})
    got = []
    check = updates.UpdateCheck()
    check.finished.connect(got.append)

    check.start()

    assert pump(lambda: got)
    assert got == [{"version": "9.9.9", "url": "u"}]


# -- what the window does with it -------------------------------------------------- #

NEWER = {"version": "99.0.0", "url": "https://github.com/k1llrouz/pdf-to-md-ui/releases/tag/v99.0.0"}
SAME = {"version": __version__, "url": "https://example.invalid/same"}


@pytest.fixture
def on_screen(window):
    gui._windows.append(window)
    return window


def check(monkeypatch, answer, manual=False):
    monkeypatch.setattr(updates, "fetch_latest", lambda: answer)
    gui.check_for_updates(manual=manual)
    assert pump(lambda: not gui._update_checks), "the check never came back"


@pytest.fixture
def opened(monkeypatch):
    urls = []
    monkeypatch.setattr(gui.QDesktopServices, "openUrl", lambda url: urls.append(url))
    return urls


def test_startup_check_shows_a_newer_release(on_screen, monkeypatch, dialogs):
    check(monkeypatch, NEWER)

    assert on_screen.update_link.isVisibleTo(on_screen)
    assert "99.0.0" in on_screen.update_link.text()
    assert NEWER["url"] in on_screen.update_link.text()
    assert dialogs == [], "a startup check must not pop anything up"


@pytest.mark.parametrize("answer", [SAME, None])
def test_startup_check_stays_quiet_otherwise(on_screen, monkeypatch, dialogs, answer):
    check(monkeypatch, answer)

    assert not on_screen.update_link.isVisibleTo(on_screen)
    assert dialogs == []


def test_manual_check_offers_the_download(on_screen, monkeypatch, dialogs, opened):
    check(monkeypatch, NEWER, manual=True)

    assert "99.0.0" in dialogs[0][2] and __version__ in dialogs[0][2]
    assert opened == [QUrl(NEWER["url"])]


def test_manual_check_says_when_up_to_date(on_screen, monkeypatch, dialogs, opened):
    check(monkeypatch, SAME, manual=True)

    assert dialogs[0][2] == gui.tr("update.latest", version=__version__)
    assert opened == []


def test_manual_check_says_when_offline(on_screen, monkeypatch, dialogs):
    check(monkeypatch, None, manual=True)

    assert dialogs[0][2] == gui.tr("update.failed")


def test_the_link_survives_a_language_switch(qapp, settings_file, monkeypatch):
    old = gui.open_window()
    check(monkeypatch, NEWER)

    new = gui.switch_language(old, "ru")

    assert new.update_link.isVisibleTo(new)
    assert "Доступна версия 99.0.0" in new.update_link.text()


def test_startup_check_can_be_turned_off(window):
    assert gui.auto_update_check_enabled()  # on unless switched off

    window.act_auto_updates.setChecked(False)

    assert not gui.auto_update_check_enabled()
    assert not gui.MainWindow().act_auto_updates.isChecked()


# -- installing an update ------------------------------------------------------- #

SETUP = b"MZ" + b"installer" * 1000
SETUP_SHA = hashlib.sha256(SETUP).hexdigest()
DOWNLOAD = "https://github.com/k1llrouz/pdf-to-md-ui/releases/download/v99.0.0/PDF-to-Markdown-Setup.exe"
INSTALLABLE = dict(NEWER, download=DOWNLOAD, size=len(SETUP), sha256=SETUP_SHA)


def release_with(assets):
    return json.dumps(
        {"tag_name": "v99.0.0", "html_url": NEWER["url"], "assets": assets}
    ).encode()


def test_reads_the_installer_from_the_release(github):
    github["body"] = release_with(
        [
            {"name": "notes.txt", "browser_download_url": DOWNLOAD + ".txt", "size": 5},
            {
                "name": "PDF-to-Markdown-Setup.exe",
                "browser_download_url": DOWNLOAD,
                "size": 1234,
                "digest": "sha256:" + "AB" * 32,
            },
        ]
    )

    assert updates.fetch_latest() == {
        "version": "99.0.0",
        "url": NEWER["url"],
        "download": DOWNLOAD,
        "size": 1234,
        "sha256": "ab" * 32,
    }


@pytest.mark.parametrize(
    "assets",
    [
        [],
        None,
        [{"name": "PDF-to-Markdown-Setup.exe", "browser_download_url": "https://evil.example/x.exe", "size": 9}],
        [{"name": "PDF-to-Markdown-Setup.exe", "browser_download_url": DOWNLOAD, "size": 0}],
    ],
)
def test_without_a_usable_installer_only_the_page_is_left(github, assets):
    github["body"] = release_with(assets)

    assert updates.fetch_latest() == {"version": "99.0.0", "url": NEWER["url"]}


def test_only_an_installed_copy_updates_itself(monkeypatch, tmp_path):
    exe = tmp_path / "PDF to Markdown.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(updates.sys, "executable", str(exe))

    assert not updates.installed_copy(), "run from source"
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    assert not updates.installed_copy(), "a build folder (dist)"
    (tmp_path / "unins000.exe").write_bytes(b"")
    (tmp_path / "unins000.dat").write_bytes(b"")
    assert updates.installed_copy()
    assert updates.can_install(INSTALLABLE)
    assert not updates.can_install(NEWER), "no installer attached"


@pytest.fixture
def server(monkeypatch, tmp_path):
    """The installer download, served from memory into tmp_path."""
    monkeypatch.setattr(updates.tempfile, "tempdir", str(tmp_path))
    served = {"body": SETUP}

    def urlopen(request, timeout):
        served["request"] = request
        if isinstance(served["body"], Exception):
            raise served["body"]
        return Response(served["body"])

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updates, "CHUNK", 1000)
    return served


def leftovers(tmp_path):
    return [p for p in tmp_path.rglob("*") if p.is_file()]


def test_downloads_and_checks_the_installer(server, tmp_path):
    seen = []

    path = updates.download_installer(INSTALLABLE, progress=lambda d, t: seen.append((d, t)))

    assert path.name == "PDF-to-Markdown-Setup.exe"
    assert path.read_bytes() == SETUP
    assert leftovers(tmp_path) == [path], "no .part file left behind"
    assert seen[-1] == (len(SETUP), len(SETUP)) and len(seen) > 1
    assert server["request"].full_url == DOWNLOAD


@pytest.mark.parametrize(
    "release, body, error",
    [
        (INSTALLABLE, SETUP[:-1], ValueError),  # cut short
        (INSTALLABLE, SETUP + b"x", ValueError),  # more than promised
        (dict(INSTALLABLE, sha256="0" * 64), SETUP, ValueError),
        (INSTALLABLE, OSError("offline"), OSError),
    ],
)
def test_a_bad_download_is_thrown_away(server, tmp_path, release, body, error):
    server["body"] = body

    with pytest.raises(error):
        updates.download_installer(release)

    assert leftovers(tmp_path) == []


def test_a_download_can_be_cancelled(server, tmp_path):
    with pytest.raises(updates.DownloadCancelled):
        updates.download_installer(INSTALLABLE, cancelled=lambda: True)

    assert leftovers(tmp_path) == []


def test_without_a_checksum_the_size_has_to_do(server):
    release = {k: v for k, v in INSTALLABLE.items() if k != "sha256"}

    assert updates.download_installer(release).read_bytes() == SETUP


def test_old_downloads_are_cleared(monkeypatch, tmp_path):
    # Each update leaves its Setup.exe behind; the next start removes it at
    # once and the rest of the folder (the install log) after a week.
    monkeypatch.setattr(updates.tempfile, "gettempdir", lambda: str(tmp_path))
    recent = tmp_path / (updates.TEMP_PREFIX + "recent")
    old = tmp_path / (updates.TEMP_PREFIX + "old")
    other = tmp_path / "someone-elses"
    for folder in (recent, old, other):
        folder.mkdir()
        (folder / updates.INSTALLER_NAME).write_bytes(SETUP)
        (folder / "install.log").write_text("log")
    now = time.time()
    week_ago = now - updates.KEEP_UPDATE_LOGS - 60
    os.utime(old, (week_ago, week_ago))

    updates.remove_old_downloads(now)

    assert not (recent / updates.INSTALLER_NAME).exists()
    assert (recent / "install.log").exists()
    assert not old.exists()
    assert (other / updates.INSTALLER_NAME).exists(), "not ours"


def test_the_installer_runs_silently_and_detached(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(
        updates.subprocess, "Popen", lambda args, **kw: started.update(args=args, **kw)
    )
    setup = tmp_path / "PDF-to-Markdown-Setup.exe"

    updates.start_installer(setup)

    args = started["args"]
    assert args[0] == str(setup)
    assert "/SILENT" in args and "/SUPPRESSMSGBOXES" in args and "/RELAUNCH=1" in args
    assert any(a.startswith("/LOG=") for a in args)
    assert started["creationflags"] & updates.subprocess.DETACHED_PROCESS


@pytest.fixture
def installable(monkeypatch, tmp_path):
    """An installed copy whose download and installer are stand-ins."""
    monkeypatch.setattr(updates, "installed_copy", lambda: True)
    setup = tmp_path / "PDF-to-Markdown-Setup.exe"
    setup.write_bytes(SETUP)
    state = {"download": lambda release, progress, cancelled: setup, "started": [], "quit": 0}

    def download(release, progress=None, cancelled=None):
        return state["download"](release, progress, cancelled)

    def start(path):
        if isinstance(state.get("start_error"), Exception):
            raise state["start_error"]
        state["started"].append(path)

    def quit_():
        state["quit"] += 1

    monkeypatch.setattr(updates, "download_installer", download)
    monkeypatch.setattr(updates, "start_installer", start)
    monkeypatch.setattr(gui, "_quit_for_update", quit_)
    state["setup"] = setup
    return state


def click_the_link(window, release):
    window.show_update(release)
    window.update_link.linkActivated.emit(release["url"])
    assert pump(lambda: not gui._downloads), "the download never came back"


def test_the_link_opens_the_page_where_the_copy_cannot_update(on_screen, dialogs, opened):
    click_the_link(on_screen, INSTALLABLE)  # run from source: not installed

    assert opened == [QUrl(NEWER["url"])]
    assert dialogs == [], "straight to the page, as before"
    assert on_screen.update_link.toolTip() == gui.tr("update.link_tooltip")


def test_the_link_installs_the_update(on_screen, dialogs, opened, installable):
    click_the_link(on_screen, INSTALLABLE)

    assert dialogs[0][2] == gui.tr("update.ask_install", version="99.0.0", current=__version__)
    assert installable["started"] == [installable["setup"]]
    assert installable["quit"] == 1
    assert opened == []
    assert on_screen.update_link.toolTip() == gui.tr("update.install_tooltip")


def test_the_download_window_shows_the_percentage(
    on_screen, monkeypatch, dialogs, opened, installable
):
    # The theme's progress bar is too thin for text: the percentage goes into
    # the label, and the window is wide enough for it.
    seen = []

    def download(release, progress, cancelled):
        progress(45, 100)
        return installable["setup"]

    installable["download"] = download
    original = gui.QProgressDialog.setLabelText
    monkeypatch.setattr(
        gui.QProgressDialog,
        "setLabelText",
        lambda self, text: (seen.append((text, self.minimumWidth())), original(self, text)),
    )

    click_the_link(on_screen, INSTALLABLE)

    label = gui.tr("update.downloading", version="99.0.0")
    assert (f"{label}  45%", 420) in seen


def test_the_installer_clears_the_old_program_files():
    # [Files] only adds and overwrites: without this an old
    # opendataloader_pdf-*.dist-info stays and About shows the old version.
    script = Path(__file__).resolve().parent.parent / "installer.iss"
    section = script.read_text(encoding="utf-8-sig").split("[InstallDelete]", 1)[1]
    section = section.split("\n[", 1)[0]
    assert r'Type: filesandordirs; Name: "{app}\_internal"' in section


def test_the_manual_check_offers_the_install(on_screen, monkeypatch, dialogs, opened, installable):
    check(monkeypatch, INSTALLABLE, manual=True)
    assert pump(lambda: not gui._downloads)

    assert dialogs[0][2] == gui.tr("update.ask_install", version="99.0.0", current=__version__)
    assert installable["quit"] == 1


def test_no_means_no(on_screen, monkeypatch, dialogs, opened, installable):
    monkeypatch.setattr(
        gui.QMessageBox, "question", staticmethod(lambda *a: gui.QMessageBox.StandardButton.No)
    )

    click_the_link(on_screen, INSTALLABLE)

    assert installable["started"] == [] and opened == []


def raise_(error):
    def download(release, progress, cancelled):
        raise error

    return download


@pytest.mark.parametrize("error", [OSError("offline"), ValueError("checksum")])
def test_a_failed_download_falls_back_to_the_page(on_screen, dialogs, opened, installable, error):
    installable["download"] = raise_(error)

    click_the_link(on_screen, INSTALLABLE)

    assert dialogs[-1][2] == gui.tr("update.install_failed")
    assert opened == [QUrl(NEWER["url"])]
    assert installable["quit"] == 0


def test_an_installer_that_will_not_start_falls_back_to_the_page(
    on_screen, dialogs, opened, installable
):
    installable["start_error"] = OSError("blocked")

    click_the_link(on_screen, INSTALLABLE)

    assert dialogs[-1][2] == gui.tr("update.install_failed")
    assert opened == [QUrl(NEWER["url"])]
    assert installable["quit"] == 0


def test_a_cancelled_download_does_nothing_more(on_screen, dialogs, opened, installable):
    installable["download"] = raise_(updates.DownloadCancelled())

    click_the_link(on_screen, INSTALLABLE)

    assert len(dialogs) == 1, "only the question"
    assert opened == [] and installable["quit"] == 0


def test_cancel_reaches_the_download(on_screen, dialogs, installable):
    def download(release, progress, cancelled):
        progress(1, 2)
        while not cancelled():
            time.sleep(0.01)
        raise updates.DownloadCancelled()

    installable["download"] = download
    on_screen.show_update(INSTALLABLE)
    on_screen.update_link.linkActivated.emit(INSTALLABLE["url"])
    _, dialog = gui._downloads[0]

    dialog.findChild(QPushButton).click()

    assert pump(lambda: not gui._downloads)
    assert installable["quit"] == 0


def test_no_update_in_the_middle_of_a_conversion(on_screen, monkeypatch, dialogs, installable):
    monkeypatch.setattr(on_screen, "_busy", lambda: True)

    click_the_link(on_screen, INSTALLABLE)

    assert dialogs[-1][2] == gui.tr("update.busy")
    assert installable["started"] == []


# -- title bar colour ------------------------------------------------------------------ #


class Hints:
    """Records what the program tells Qt about its colours."""

    def __init__(self):
        self.calls = []

    def setColorScheme(self, scheme):
        self.calls.append(scheme)

    def unsetColorScheme(self):
        self.calls.append("system")

    def colorScheme(self):
        return Qt.ColorScheme.Dark


def test_qt_is_told_the_chosen_theme(window, monkeypatch):
    hints = Hints()
    monkeypatch.setattr(gui.QGuiApplication, "styleHints", staticmethod(lambda: hints))

    window._set_theme("dark")
    window._set_theme("light")
    window._set_theme("system")

    assert hints.calls == [Qt.ColorScheme.Dark, Qt.ColorScheme.Light, "system"]


def test_as_in_windows_follows_a_change_in_windows(window, monkeypatch):
    window._set_theme("system")
    monkeypatch.setattr(gui, "_system_prefers_dark", lambda: not window.dark)
    was = window.dark

    window._follow_system_theme()

    assert window.dark is (not was)


def test_a_chosen_theme_ignores_windows(window, monkeypatch):
    window._set_theme("light")
    monkeypatch.setattr(gui, "_system_prefers_dark", lambda: True)

    window._follow_system_theme()

    assert window.dark is False


def test_closed_windows_stop_listening(qapp, settings_file):
    w = gui.MainWindow()
    w.close()
    w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()

    # Would raise "already deleted" if the window were still connected.
    QApplication.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)

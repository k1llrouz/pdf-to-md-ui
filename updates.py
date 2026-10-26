"""Is there a newer release on GitHub - and installing it.

One request to the GitHub API: the latest published release, drafts and
pre-releases left out. It runs on a thread of its own - a slow or missing
connection must never hold the window up - and any failure just means "no
answer": the program works the same offline.

An installed copy can update itself: the release's Setup.exe is downloaded
to a temporary folder, checked against the size and checksum GitHub gives
for it, and started without its wizard while the program closes. A copy run
from source or from a build folder has nothing to install over, so it only
opens the download page.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from version import __version__

LATEST_URL = "https://api.github.com/repos/k1llrouz/pdf-to-md-ui/releases/latest"
TIMEOUT = 10  # seconds
# The installer CI attaches to every release, and where it may come from.
INSTALLER_NAME = "PDF-to-Markdown-Setup.exe"
DOWNLOAD_PREFIX = "https://github.com/k1llrouz/pdf-to-md-ui/releases/download/"
DOWNLOAD_TIMEOUT = 30  # seconds without a byte before giving up
CHUNK = 64 * 1024
# The folder each download goes to; remove_old_downloads() clears old ones.
TEMP_PREFIX = "pdf-to-md-update-"
KEEP_UPDATE_LOGS = 7 * 24 * 3600  # seconds
# Inno Setup's switches: a progress window and nothing to answer; the install
# folder, language and tasks are those of the copy already there. RELAUNCH is
# the program's own (installer.iss): start it again when done.
INSTALLER_ARGS = [
    "/SILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/NORESTARTAPPLICATIONS",
    "/RELAUNCH=1",
]


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.0' -> (1, 2, 0). Anything after the numbers is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str = __version__) -> bool:
    new, old = parse_version(latest), parse_version(current)
    if not new or not old:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def fetch_latest() -> dict | None:
    """{"version": "1.2.0", "url": <release page>} or None if unreachable.

    With the installer attached, also "download" (its address), "size" and,
    where GitHub gives one, "sha256".
    """
    request = urllib.request.Request(
        LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub turns away requests without one.
            "User-Agent": f"pdf-to-md-ui/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            release = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(release, dict):
        return None
    tag, url = release.get("tag_name"), release.get("html_url")
    if not isinstance(tag, str) or not isinstance(url, str):
        return None
    answer = {"version": tag.lstrip("v"), "url": url}
    answer.update(_installer_asset(release.get("assets")))
    return answer


def _installer_asset(assets) -> dict:
    """The installer's address, size and checksum - or nothing at all."""
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict) or asset.get("name") != INSTALLER_NAME:
            continue
        download, size = asset.get("browser_download_url"), asset.get("size")
        if not isinstance(download, str) or not download.startswith(DOWNLOAD_PREFIX):
            return {}
        if not isinstance(size, int) or size <= 0:
            return {}
        found = {"download": download, "size": size}
        digest = asset.get("digest")  # "sha256:..."; older releases have none
        if isinstance(digest, str) and digest.startswith("sha256:"):
            found["sha256"] = digest.split(":", 1)[1].lower()
        return found
    return {}


# -- updating an installed copy ---------------------------------------------- #


def installed_copy() -> bool:
    """Is this the program Setup.exe installed - the one an update replaces?

    Inno Setup leaves its uninstaller next to the program. A frozen build
    without one is a build folder (dist), and run from source there is
    nothing to install over at all.
    """
    if not getattr(sys, "frozen", False):
        return False
    folder = Path(sys.executable).resolve().parent
    return (folder / "unins000.exe").is_file() and (folder / "unins000.dat").is_file()


def can_install(release: dict) -> bool:
    """Can this release be installed from within the program?"""
    return bool(release.get("download")) and installed_copy()


class DownloadCancelled(Exception):
    pass


def download_installer(release: dict, progress=None, cancelled=None) -> Path:
    """Fetch the release's installer into a new temporary folder.

    `progress(done, total)` is called as bytes arrive; `cancelled()` returning
    True stops the download. The file is checked against the size and the
    checksum GitHub gave, and a partial or wrong one is deleted. Raises
    OSError, ValueError for a mismatch, DownloadCancelled when stopped.
    """
    total = release["size"]
    folder = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    target = folder / INSTALLER_NAME
    partial = folder / (INSTALLER_NAME + ".part")
    request = urllib.request.Request(
        release["download"], headers={"User-Agent": f"pdf-to-md-ui/{__version__}"}
    )
    digest = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
            with open(partial, "wb") as out:
                while True:
                    if cancelled is not None and cancelled():
                        raise DownloadCancelled()
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > total:
                        raise ValueError("the installer is bigger than GitHub says")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress is not None:
                        progress(done, total)
        if done != total:
            raise ValueError(f"got {done} bytes of {total}")
        expected = release.get("sha256")
        if expected and digest.hexdigest() != expected:
            raise ValueError("the installer's checksum does not match")
        partial.replace(target)
    except BaseException:
        _remove(folder)
        raise
    return target


def remove_old_downloads(now: float | None = None) -> None:
    """Clear what earlier updates left in the temporary folder.

    The installer cannot delete itself, so every update leaves its ~90 MB
    Setup.exe behind. Called at startup: the installer goes at once (if it is
    still running, the file is in use and stays until the next start), its
    log - the one thing to look at when an update went wrong - after a week.
    """
    now = time.time() if now is None else now
    for folder in Path(tempfile.gettempdir()).glob(TEMP_PREFIX + "*"):
        try:
            if not folder.is_dir():
                continue
            if now - folder.stat().st_mtime > KEEP_UPDATE_LOGS:
                _remove(folder)
                continue
            for item in folder.glob("*.exe"):
                item.unlink()
        except OSError:
            pass


def _remove(folder: Path) -> None:
    for item in folder.glob("*"):
        try:
            item.unlink()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


def start_installer(installer: Path) -> None:
    """Start the downloaded Setup.exe on its own; the program then quits.

    It is detached, so it outlives the program, and it needs no waiting for:
    Inno Setup closes whatever still holds the program's files (the copy on
    its way out) before replacing them. Its log goes next to it, for when
    something goes wrong. Raises OSError if it cannot be started.
    """
    log = installer.with_name("install.log")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(installer), *INSTALLER_ARGS, f"/LOG={log}"],
        cwd=str(installer.parent),
        creationflags=flags,
        close_fds=True,
    )


class UpdateCheck(QObject):
    """Asks in the background; `finished` carries fetch_latest()'s answer."""

    finished = Signal(object)

    def start(self) -> None:
        # The signal crosses back to the window's thread on its own: Qt queues
        # it for the receiver. The thread is a daemon, so a hung request never
        # keeps the program from exiting.
        threading.Thread(target=lambda: self.finished.emit(fetch_latest()), daemon=True).start()


class UpdateDownload(QObject):
    """download_installer() on a thread of its own.

    `progress` carries (bytes so far, total); `finished` the installer's path
    and an empty string, or None and what went wrong - empty for a cancel.
    """

    progress = Signal(int, int)
    finished = Signal(object, str)

    def __init__(self, release: dict):
        super().__init__()
        self.release = release
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            path = download_installer(
                self.release,
                progress=lambda done, total: self.progress.emit(done, total),
                cancelled=self._stop.is_set,
            )
        except DownloadCancelled:
            self.finished.emit(None, "")
        except (OSError, ValueError) as exc:
            self.finished.emit(None, str(exc) or exc.__class__.__name__)
        else:
            self.finished.emit(path, "")

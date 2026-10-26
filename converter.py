"""Conversion core: everything that runs a PDF through opendataloader-pdf.

Nothing in here touches a widget, so it can be tested without a window.

The conversion is not driven through opendataloader_pdf.convert(): that call is
synchronous, has no timeout and cannot be interrupted, so a JVM that stalls
(waiting on a hybrid backend that was never started, chewing through a
datasheet full of plots) takes the whole application down with it. Instead the
bundled JAR is launched per file with subprocess, which buys a real timeout, a
working Cancel button and per-file error isolation: one broken PDF no longer
kills the batch.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import importlib.metadata
import importlib.resources as resources
import inspect
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal

from i18n import tr
import pikepdf
from fontfix import prepare_copy
from postprocess import (
    add_figure_hint,
    drop_figure_tables,
    looks_scanned,
    split_limit_columns,
    tables_to_markdown,
    tidy_images,
    unescape_signs,
)

JAR_NAME = "opendataloader-pdf-cli.jar"

# Page markers help both a human reader and whatever splits the document into
# chunks later; %page-number% is the placeholder the CLI fills in.
PAGE_SEPARATOR = "\n\n---- page %page-number% ----\n\n"

# The hybrid backend is a separate server the user installs and starts by hand.
# Without a timeout the JVM waits for an answer that never comes - which is
# exactly what "the app hangs" looked like - and without a fallback there is no
# way back to the Java pipeline either.
HYBRID_DEFAULT_URL = "http://localhost:5002"
HYBRID_TIMEOUT_MS = "60000"

# Defaults of the CLI itself. Options are only passed when the user moves them
# off these values: sending a setting an older release never heard of would pop
# the "not supported" dialog on every run for no gain.
IMAGE_DPI_DEFAULT = 144
SPACE_RATIO_DEFAULT = 0.17

# Content safety filters to switch off. Hidden-, tiny- and off-page-text checks
# cost real time on pages with plots, where axis labels and ticks run into the
# thousands. Spelled out rather than "all": that would also switch off the
# "background" filter, which only removes large vector shapes (LineArtChunk
# over half the page in one direction - never text), and the datasheet results
# were checked with it on. Whether dropping it helps is a separate, measured
# decision.
CONTENT_SAFETY_OFF = "hidden-text,off-page,tiny,hidden-ocg"

# Suffixes the converter appends to the input name for the PDFs it writes.
DERIVED_PDF_SUFFIXES = ("_annotated", "_tagged")


# --------------------------------------------------------------------------- #
# Java / JAR plumbing
# --------------------------------------------------------------------------- #

# resources.as_file() may materialise the JAR into a temp folder; keep the
# context open for the lifetime of the process instead of re-extracting it once
# per file in the batch.
_JAR_STACK = contextlib.ExitStack()
_JAR_PATH: str | None = None


def jar_path() -> str | None:
    """Absolute path to the CLI jar shipped inside opendataloader_pdf."""
    global _JAR_PATH
    if _JAR_PATH is None:
        try:
            ref = resources.files("opendataloader_pdf").joinpath("jar", JAR_NAME)
            _JAR_PATH = str(_JAR_STACK.enter_context(resources.as_file(ref)))
        except (ImportError, ModuleNotFoundError, FileNotFoundError):
            return None
    return _JAR_PATH


def odl_version() -> str:
    """Installed opendataloader-pdf version, for the log.

    Its output changes between releases - 2.5.1 to 2.5.5 escaped every table
    cell - so "which version produced this" is the first question when a
    result looks wrong.
    """
    try:
        return importlib.metadata.version("opendataloader-pdf")
    except importlib.metadata.PackageNotFoundError:
        return "?"


def java_executable() -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    return shutil.which("java")


def use_bundled_jre() -> None:
    """Point JAVA_HOME at the JRE shipped next to the executable, if present.

    The build puts a trimmed jlink runtime in a "jre" folder beside the
    application. It wins over whatever JAVA_HOME happened to be set: the app was
    built and tested against that runtime, while the machine's own JAVA_HOME may
    be anything at all - including a JDK far newer than the JAR expects.
    """
    base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent
    jre = base / "jre"
    launcher = jre / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not launcher.exists():
        return
    os.environ["JAVA_HOME"] = str(jre)
    os.environ["PATH"] = str(jre / "bin") + os.pathsep + os.environ.get("PATH", "")


# --------------------------------------------------------------------------- #
# Hidden-text switch - see patches/README.md
# --------------------------------------------------------------------------- #

# opendataloader-pdf can drop text a reader cannot see - it renders the page
# and compares the text with what is behind it - but 2.5.15 leaves that filter
# off and its CLI can only switch it off. This FilterConfig takes its default
# from a system property instead.
HIDDEN_PATCH_ROOT = Path(__file__).resolve().parent / "patches" / "hidden-text"
HIDDEN_PATCHED_CLASS = "org/opendataloader/pdf/api/FilterConfig.class"
HIDDEN_PROPERTY = "-Dopendataloader.filterHiddenText=true"
# FilterConfig as shipped in opendataloader-pdf 2.5.15, the source the patch
# was compiled from. Another jar gets no patch: its FilterConfig may differ.
HIDDEN_PATCH_TARGETS = {
    "d2efb4ed6e481eb84b422d56f823090071ca997d867839cdfe6814d5af5f9428",
}
_HIDDEN_CACHE: dict[str, bool] = {}

# PDFBox builds a cache of the system's fonts the first time it has to draw
# text - image extraction and the hidden-text filter both do. On a slow disk
# that has taken minutes. A timeout killing the JVM mid-build means the cache
# is never written and every following file starts the build over, so the
# clock stops while it runs (up to FONT_CACHE_GRACE, in case it never ends).
FONT_CACHE_STARTED = "Building on-disk font cache"
FONT_CACHE_FINISHED = "Finished building on-disk font cache"
FONT_CACHE_GRACE = 15 * 60  # seconds


def _main_class(jar: str) -> str | None:
    try:
        with zipfile.ZipFile(jar) as z:
            manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    return next(
        (
            line.split(":", 1)[1].strip()
            for line in manifest.splitlines()
            if line.startswith("Main-Class:")
        ),
        None,
    )


def hidden_text_patch(jar: str) -> bool:
    """True when the hidden-text switch fits this jar."""
    if jar not in _HIDDEN_CACHE:
        fits = False
        try:
            if (HIDDEN_PATCH_ROOT / HIDDEN_PATCHED_CLASS).is_file():
                with zipfile.ZipFile(jar) as z:
                    bundled = hashlib.sha256(z.read(HIDDEN_PATCHED_CLASS)).hexdigest()
                fits = bundled in HIDDEN_PATCH_TARGETS and _main_class(jar) is not None
        except (OSError, KeyError, zipfile.BadZipFile):
            fits = False
        _HIDDEN_CACHE[jar] = fits
    return _HIDDEN_CACHE[jar]


def java_command(
    java: str, jar: str, args: list[str], filter_hidden: bool = False
) -> list[str]:
    """The full command line for one conversion. Tests swap this out.

    filter_hidden: also drop invisible and same-colour text, through the
    hidden-text patch - when it fits this jar; otherwise nothing changes here
    and the caller says so in the log.
    """
    command = [java, "-Djava.awt.headless=true", "-Dapple.awt.UIElement=true"]
    main = _main_class(jar) if filter_hidden and hidden_text_patch(jar) else None
    if main:
        # Classpath order decides which class loads: the patch, then the jar.
        classpath = os.pathsep.join([str(HIDDEN_PATCH_ROOT), jar])
        return [*command, HIDDEN_PROPERTY, "-cp", classpath, main, *args]
    return [*command, "-jar", jar, *args]


def _total_ram_mb() -> int:
    """Physical memory in MiB, or 0 when it cannot be determined."""
    if os.name == "nt":

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        try:
            status = _MemStatus()
            status.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys // (1024 * 1024))
        except Exception:  # noqa: BLE001 - a heap hint, never worth crashing over
            return 0
        return 0
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * size // (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        return 0


def max_heap_mb() -> int:
    """Heap ceiling for the JVM.

    The API exposes no memory switch, so the only lever is JAVA_TOOL_OPTIONS in
    the child environment. Half of physical memory, capped at 8 GB: enough for
    a plot-heavy datasheet, still safe on a 4 GB laptop.
    """
    total = _total_ram_mb()
    if not total:
        return 4096
    return max(1024, min(8192, total // 2))


def supported_option_names() -> set[str] | None:
    """Options the installed opendataloader-pdf knows about.

    We build the command line ourselves, but the generated convert() signature
    is still an honest inventory of what the bundled JAR accepts: the two come
    from the same release. None means "cannot tell".
    """
    try:
        import opendataloader_pdf
    except ImportError:
        return None
    try:
        params = inspect.signature(opendataloader_pdf.convert).parameters
    except (TypeError, ValueError):
        return None
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return None
    return set(params)


def build_args(
    pdf: str, output_dir: str, options: dict, supported: set[str] | None
) -> list[str]:
    """Turn an option dict into CLI arguments (--markdown-with-html and friends)."""
    args: list[str] = [pdf, "--output-dir", output_dir]
    for key, value in options.items():
        if supported is not None and key not in supported:
            continue
        if value is None or value == "" or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        if value is True:
            args.append(flag)
        else:
            args.extend([flag, str(value)])
    return args


def server_reachable(url: str, timeout: float = 1.5) -> bool:
    """Cheap TCP probe: is anything listening where the hybrid backend should be?"""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# --------------------------------------------------------------------------- #
# Deciding where each file goes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Job:
    pdf: str  # the source file
    output_dir: str  # the folder its results are written to
    label: str  # how the file is named in the log and in the summary


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _is_inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _is_derived(pdf: Path) -> bool:
    """A PDF this program wrote itself, sitting next to the one it came from.

    With the results folder set to the sources folder, doc_annotated.pdf lands
    beside doc.pdf, and the next run would convert it too - and then its own
    annotated copy, one generation more every time.
    """
    stem = pdf.stem
    for suffix in DERIVED_PDF_SUFFIXES:
        if stem.lower().endswith(suffix):
            original = pdf.with_name(stem[: -len(suffix)] + pdf.suffix)
            if original.exists():
                return True
    return False


def plan_jobs(sources: list[str], output_root: str) -> list[Job]:
    """One job per PDF in the selection, each with the folder its results go to.

    The converter names every output after its input (doc.md, doc_images/,
    doc_annotated.pdf), so two inputs called doc.pdf written to the same place
    overwrite each other - silently, with both reported as converted. Folders
    are the only thing under our control; renaming files instead would break
    the links from the .md to its images. Hence:

    - a folder keeps its inner structure: <folder>/TI/a.pdf -> <root>/TI/;
      with several folders selected each gets its own subfolder by name;
    - a file added on its own goes straight into the root;
    - if two files would still land on the same <dir>/<name>, the later one
      goes into <dir>/<name> (2)/ instead;
    - a results folder inside a selected folder is skipped, as are PDFs this
      program derived from a file next to them - otherwise every run would
      convert the previous run's output.
    """
    root = Path(output_root)
    folders = [Path(s) for s in sources if Path(s).is_dir()]
    prefix_folders = len(folders) > 1

    seen: set[str] = set()
    planned: list[tuple[Path, Path, str]] = []  # (pdf, target dir, label)

    for raw in sources:
        source = Path(raw)
        if source.is_dir():
            prune_root = _is_inside(root, source) and _key(root) != _key(source)
            found = sorted(
                p
                for p in source.rglob("*")
                if p.is_file()
                and p.suffix.lower() == ".pdf"
                and not (prune_root and _is_inside(p, root))
                and not _is_derived(p)
            )
            base = Path(source.name) if prefix_folders else Path()
            entries = [
                (p, root / base / p.relative_to(source).parent, base / p.relative_to(source))
                for p in found
            ]
        elif source.is_file():
            # Picked by hand: taken as asked, whatever its name says.
            entries = [(source, root, Path(source.name))]
        else:
            entries = []

        for pdf, target, label in entries:
            key = _key(pdf)
            if key in seen:  # the same file reached twice, e.g. itself and its folder
                continue
            seen.add(key)
            planned.append((pdf, target, label.as_posix()))

    # Two different files bound for the same <dir>/<stem>: move the later one
    # into a folder of its own rather than let it overwrite the first.
    taken: set[tuple[str, str]] = set()
    jobs: list[Job] = []
    for pdf, target, label in planned:
        stem = pdf.stem
        candidate, n = target, 1
        while (os.path.normcase(str(candidate)), stem.lower()) in taken:
            n += 1
            candidate = target / f"{stem} ({n})"
        taken.add((os.path.normcase(str(candidate)), stem.lower()))
        jobs.append(Job(str(pdf), str(candidate), label))

    # Two hand-picked files may share a bare name; the full path tells them apart.
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.label] = counts.get(job.label, 0) + 1
    return [
        Job(j.pdf, j.output_dir, j.pdf) if counts[j.label] > 1 else j for j in jobs
    ]


# --------------------------------------------------------------------------- #
# Conversion worker
# --------------------------------------------------------------------------- #


class ConvertWorker(QObject):
    """Runs the JAR once per file on a worker thread.

    One process per PDF is what makes the rest possible: a per-file timeout, a
    Cancel that actually stops the JVM, and a batch that survives a file the
    converter chokes on.
    """

    log = Signal(str)
    progress = Signal(int, int, str)  # index (1-based), total, file label
    finished = Signal(object)  # summary dict

    def __init__(
        self,
        jobs: list[Job],
        output_root: str,
        options: dict,
        timeout: int,
        supported: set[str] | None,
        filter_hidden: bool = False,
        clean_figures: bool = False,
        plain_tables: bool = False,
    ):
        super().__init__()
        self.jobs = jobs
        self.output_root = output_root
        self.options = options
        self.timeout = timeout  # seconds; 0 = no limit
        self.supported = supported
        self.filter_hidden = filter_hidden
        self.clean_figures = clean_figures
        # Markdown tables as | pipes |, made from the HTML ones at the very end.
        self.plain_tables = plain_tables

        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._cancelled = False
        self._pending: list[str] = []
        self._last_flush = 0.0

    # -- public API (called from the GUI thread) --------------------------- #

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None:
            self._kill(process)

    # -- run --------------------------------------------------------------- #

    def run(self) -> None:
        summary = {
            "ok": 0,
            "total": len(self.jobs),
            "failed": [],
            "timed_out": [],
            "degraded": [],
            # Converted, but no text came out: most likely a scan.
            "no_text": [],
            "cancelled": False,
            "error": "",
            # One entry per file that ran: what the file list shows next to it.
            "results": [],
        }

        def record(job: Job, status: str) -> None:
            summary["results"].append(
                {"pdf": job.pdf, "output_dir": job.output_dir, "status": status}
            )

        jar = jar_path()
        java = java_executable()
        if jar is None:
            summary["error"] = tr("run.no_package")
            self.finished.emit(summary)
            return
        if java is None:
            summary["error"] = tr("run.no_java")
            self.finished.emit(summary)
            return

        heap = max_heap_mb()
        self._emit(f"opendataloader-pdf {odl_version()}")
        if self.filter_hidden:
            self._emit(
                tr("run.hidden_on") if hidden_text_patch(jar) else tr("run.hidden_partial")
            )
        self._emit(tr("run.inputs", count=len(self.jobs)))
        self._emit(tr("run.output", path=self.output_root))
        self._emit(tr("run.java", path=java, heap=heap))
        if self.timeout:
            self._emit(tr("run.timeout", seconds=self.timeout))

        # Splitting merged MIN/TYP/MAX columns needs the positions only the
        # JSON output has; ask for it too and remove it later if unwanted.
        run_options = dict(self.options)
        formats = [f for f in str(run_options.get("format", "")).split(",") if f]
        self._extra_json = bool(
            run_options.get("markdown_with_html") and "markdown" in formats and "json" not in formats
        )
        if self._extra_json:
            run_options["format"] = ",".join(formats + ["json"])

        for index, job in enumerate(self.jobs, 1):
            with self._lock:
                if self._cancelled:
                    break
            self.progress.emit(index, len(self.jobs), job.label)
            self._emit(f"[{index}/{len(self.jobs)}] {job.label}")
            if os.path.normcase(job.output_dir) != os.path.normcase(self.output_root):
                self._emit(tr("run.result_dir", path=job.output_dir))

            try:
                os.makedirs(job.output_dir, exist_ok=True)
            except OSError as exc:
                self._emit(tr("run.mkdir_failed", error=exc))
                summary["failed"].append(job.label)
                record(job, "failed")
                continue

            # A copy with readable fonts when the original has fonts the
            # converter could only print as "?" (fontfix.py); else the file.
            work_dir = tempfile.mkdtemp(prefix="pdf2md-")
            source = self._readable_copy(job, work_dir)
            try:
                outcome = self._run_one(java, jar, job, run_options, heap, source)
                if outcome == "timeout" and run_options.get("image_output") != "off":
                    # Images are the expensive part; an empty result helps
                    # nobody, so retry once without them and say so afterwards.
                    self._emit(tr("run.retry_no_images"))
                    retry = dict(run_options, image_output="off")
                    outcome = self._run_one(java, jar, job, retry, heap, source)
                    if outcome == "ok":
                        outcome = "degraded"
            finally:
                shutil.rmtree(work_dir, ignore_errors=True)

            if outcome == "degraded":
                self._postprocess(job)
                self._check_text(job, summary)
                summary["degraded"].append(job.label)
                summary["ok"] += 1
                record(job, "degraded")
                continue

            if outcome == "cancelled":
                summary["cancelled"] = True
                break

            if outcome == "ok":
                self._postprocess(job)
                self._check_text(job, summary)
                summary["ok"] += 1
                record(job, "ok")
            elif outcome == "timeout":
                summary["timed_out"].append(job.label)
                self._emit(tr("run.timed_out"))
                record(job, "timed_out")
            else:
                summary["failed"].append(job.label)
                self._emit(tr("run.failed"))
                record(job, "failed")

        self._flush()
        self.finished.emit(summary)

    def _readable_copy(self, job: Job, work_dir: str) -> str:
        """The PDF to hand the converter: a repaired copy, or the original.

        Fonts get the character maps they lack and inline images move out of
        the page text (fontfix.py). The copy keeps the file name - the
        converter names its output after it - and is written unencrypted, so
        it needs no password.
        """
        target = os.path.join(work_dir, os.path.basename(job.pdf))
        try:
            fonts, pages = prepare_copy(job.pdf, target, self.options.get("password") or "")
        except Exception:  # noqa: BLE001 - any trouble: convert the original
            return job.pdf
        if fonts:
            self._emit(tr("run.fonts_fixed", count=fonts))
        if pages:
            self._emit(tr("run.inline_images", count=pages))
        return target if fonts or pages else job.pdf

    def _postprocess(self, job: Job) -> None:
        """Tidy the Markdown the converter wrote (postprocess.py).

        Tables and images only in the HTML-table form: plain Markdown tables
        keep neither the merged cells to split nor the empty ones a drawing
        is told by. Escaped signs in either. Never fatal - the untouched file
        is still a result.
        """
        stem = Path(job.output_dir) / Path(job.pdf).stem
        json_path = stem.with_suffix(".json")
        try:
            md_path = stem.with_suffix(".md")
            if not md_path.exists():
                return
            text = md_path.read_text(encoding="utf-8")
            new = text
            if self.options.get("markdown_with_html"):
                if json_path.exists():
                    doc = json.loads(json_path.read_text(encoding="utf-8"))
                    new, split = split_limit_columns(new, doc)
                    if split:
                        self._emit(tr("run.limits_split", count=split))
                if self.clean_figures:
                    new, dropped = drop_figure_tables(new)
                    if dropped:
                        self._emit(tr("run.figures_dropped", count=dropped))
                    new = add_figure_hint(new)
                if self.options.get("image_output") == "external":
                    new = self._tidy_images(stem, new)
            new, _ = unescape_signs(new)
            if self.plain_tables:
                new = tables_to_markdown(new)
            if new != text:
                md_path.write_text(new, encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            pass
        finally:
            if getattr(self, "_extra_json", False):
                with contextlib.suppress(OSError):
                    json_path.unlink()

    def _check_text(self, job: Job, summary: dict) -> None:
        """Say so when the result has no text: the PDF is likely a scan."""
        stem = Path(job.output_dir) / Path(job.pdf).stem
        result = next((p for p in (stem.with_suffix(".md"), stem.with_suffix(".txt")) if p.exists()), None)
        if result is None:
            return
        try:
            with pikepdf.open(job.pdf, password=self.options.get("password") or "") as pdf:
                pages = len(pdf.pages)
            text = result.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 - cannot tell: say nothing
            return
        if looks_scanned(text, pages):
            self._emit(tr("run.no_text"))
            summary["no_text"].append(job.label)

    def _tidy_images(self, stem: Path, md: str) -> str:
        """Merge duplicate image files, drop logos, delete unlinked files."""
        folder = stem.parent / (stem.name + "_images")
        if not folder.is_dir():
            return md
        names = [f.name for f in folder.iterdir()]
        keep: set[str] = set()
        others = [".html"] + ([] if getattr(self, "_extra_json", False) else [".json"])
        for suffix in others:
            other = stem.with_suffix(suffix)
            if other.exists():
                text = other.read_text(encoding="utf-8", errors="replace")
                keep |= {n for n in names if n in text}
        new, removed, deleted = tidy_images(md, folder, keep)
        if removed or deleted:
            self._emit(tr("run.images_tidied", links=removed, files=deleted))
        return new

    # -- one file ----------------------------------------------------------- #

    def _run_one(
        self, java: str, jar: str, job: Job, options: dict, heap: int, source: str | None = None
    ) -> str:
        """Return "ok", "error", "timeout" or "cancelled".

        source: the PDF to read, when it is not job.pdf itself (a font-fixed
        copy; it needs no password).
        """
        if source and source != job.pdf:
            options = {k: v for k, v in options.items() if k != "password"}
        args = build_args(source or job.pdf, job.output_dir, options, self.supported)
        command = java_command(java, jar, args, filter_hidden=self.filter_hidden)

        env = os.environ.copy()
        env.setdefault("JAVA_TOOL_OPTIONS", f"-Xmx{heap}m")

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=_no_window(),
            )
        except OSError as exc:
            self._emit(tr("run.java_start_failed", error=exc))
            return "error"

        with self._lock:
            self._process = process
            cancelled = self._cancelled
        if cancelled:
            self._kill(process)
            return "cancelled"

        lines: queue.Queue = queue.Queue()
        reader = threading.Thread(target=_pump, args=(process.stdout, lines), daemon=True)
        reader.start()

        deadline = time.monotonic() + self.timeout if self.timeout else None
        left_before_fonts = None  # timeout left when the font cache build began
        draining = True
        while draining:
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                pass
            else:
                if line is None:
                    draining = False
                else:
                    self._emit(line.rstrip())
                    if deadline and FONT_CACHE_STARTED in line and left_before_fonts is None:
                        left_before_fonts = max(0.0, deadline - time.monotonic())
                        deadline = time.monotonic() + FONT_CACHE_GRACE
                        self._emit(tr("run.font_cache"))
                    elif deadline and FONT_CACHE_FINISHED in line and left_before_fonts is not None:
                        deadline = time.monotonic() + left_before_fonts

            with self._lock:
                cancelled = self._cancelled
            if cancelled:
                self._kill(process)
                return "cancelled"
            if draining and deadline and time.monotonic() > deadline:
                self._kill(process)
                return "timeout"

        code = process.wait()
        with self._lock:
            self._process = None
        if code != 0:
            self._emit(tr("run.java_exit", code=code))
            return "error"
        return "ok"

    def _kill(self, process: subprocess.Popen) -> None:
        """Kill the JVM and everything it spawned.

        The JVM is a child of Python and happily outlives a killed thread, so
        stopping it has to go through the process tree; on Windows only
        taskkill /T reliably does that.
        """
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    creationflags=_no_window(),
                    timeout=15,
                )
            else:
                process.terminate()
        except Exception:  # noqa: BLE001 - fall through to kill()
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                process.kill()
        with self._lock:
            if self._process is process:
                self._process = None

    # -- log batching ------------------------------------------------------- #

    def _emit(self, text: str) -> None:
        """Queue a log line, flushing at most ten times a second.

        A signal per JVM output line floods the GUI thread on a large document
        and the window stops repainting - which reads as a hang even while the
        conversion is running fine.
        """
        if not text:
            return
        self._pending.append(text)
        now = time.monotonic()
        if now - self._last_flush >= 0.1 or len(self._pending) >= 200:
            self._flush()

    def _flush(self) -> None:
        if self._pending:
            self.log.emit("\n".join(self._pending))
            self._pending.clear()
        self._last_flush = time.monotonic()


def _pump(stream, sink: queue.Queue) -> None:
    """Feed a process' output into a queue, ending with a None sentinel."""
    try:
        for line in stream:
            sink.put(line)
    except (ValueError, OSError):  # stream closed under us by a kill
        pass
    finally:
        sink.put(None)

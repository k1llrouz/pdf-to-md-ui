"""Desktop front-end for opendataloader-pdf.

A single-window Qt application aimed at people who never open a terminal:
drag PDFs in, tick the options, press one button. The conversion itself lives
in converter.py; this module is the window around it.
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (
    QElapsedTimer,
    QPoint,
    QPointF,
    QRectF,
    qVersion,
    QEvent,
    QObject,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QActionGroup,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import shiboken6

import i18n
import theme
import updates
from converter import (
    CONTENT_SAFETY_OFF,
    HYBRID_DEFAULT_URL,
    HYBRID_TIMEOUT_MS,
    IMAGE_DPI_DEFAULT,
    PAGE_SEPARATOR,
    SPACE_RATIO_DEFAULT,
    ConvertWorker,
    _no_window,
    java_executable,
    odl_version,
    plan_jobs,
    server_reachable,
    supported_option_names,
    use_bundled_jre,
)
from i18n import tr
from single_instance import SingleInstance
from updates import UpdateCheck, is_newer
from version import __version__

APP_NAME = "PDF → Markdown"
REPO_URL = "https://github.com/k1llrouz/pdf-to-md-ui"
# (CLI format, label key). The annotated PDF is not an output format like the
# others - it carries no extracted text - but it belongs in the same row
# anyway: it is a file the run produces, and hiding it among the settings left
# people guessing what the checkbox did.
FORMATS = [
    ("markdown", "format.markdown"),
    ("json", "format.json"),
    ("html", "format.html"),
    ("text", "format.text"),
    ("tagged-pdf", "format.tagged_pdf"),
    ("pdf", "format.pdf"),
]
# Formats whose name alone does not say what the file is.
FORMAT_HELP = {
    "tagged-pdf": "format.tagged_pdf_tooltip",
    "pdf": "format.pdf_tooltip",
}
# Image resolution choices. 144 is the converter's own default; 96 is
# usually enough for datasheet plots; 300 and up is print quality.
IMAGE_DPI_CHOICES = [72, 96, 120, 144, 200, 300, 600]
# Timeout per file, in seconds; 0 is no limit.
TIMEOUT_CHOICES = [15, 30, 60, 120, 300, 600, 1800, 0]
TIMEOUT_DEFAULT = 30
# Saved destinations under "Where to save".
FAVORITES_MAX = 5
# AI mode (the converter's hybrid backend) is hidden until it works again:
# its two settings are not shown and a saved choice is ignored.
AI_MODE = False


def resource(name: str) -> Path:
    """A file shipped with the program - inside the bundle when frozen."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / name if base else Path(__file__).resolve().parent / name


# Results the converter writes for each format, in the order a double-click
# prefers them: the text first, the PDFs only when nothing else was made.
RESULT_SUFFIXES = [
    ("markdown", ".md"),
    ("html", ".html"),
    ("text", ".txt"),
    ("json", ".json"),
    ("pdf", "_annotated.pdf"),
    ("tagged-pdf", "_tagged.pdf"),
]

_STATUS_COLOURS = {
    "ok": "#1f9d5f",
    "degraded": "#d39b1e",
    "partial": "#d39b1e",
    "failed": "#d13438",
    "timed_out": "#d13438",
}
_status_icons: dict[str, QIcon] = {}


def status_icon(status: str) -> QIcon:
    """A round mark for the file list: tick, exclamation or cross."""
    if status not in _status_icons:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_STATUS_COLOURS[status]))
        p.drawEllipse(2, 2, 28, 28)
        p.setPen(QPen(QColor("white"), 4, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if status == "ok":
            p.drawPolyline([QPointF(9, 16.5), QPointF(14, 21.5), QPointF(23, 11)])
        elif status in ("degraded", "partial"):
            p.drawLine(QPointF(16, 8), QPointF(16, 18))
            p.drawPoint(QPointF(16, 24))
        else:
            p.drawLine(QPointF(10.5, 10.5), QPointF(21.5, 21.5))
            p.drawLine(QPointF(21.5, 10.5), QPointF(10.5, 21.5))
        p.end()
        _status_icons[status] = QIcon(pixmap)
    return _status_icons[status]


def app_settings() -> QSettings:
    """Per-user settings store: the registry on Windows, an ini file elsewhere."""
    return QSettings("pdf-to-md-ui", "settings")


# --------------------------------------------------------------------------- #
# Remembering the options between launches
# --------------------------------------------------------------------------- #


def widget_value(widget: QWidget):
    if isinstance(widget, QCheckBox) or (
        isinstance(widget, QPushButton) and widget.isCheckable()
    ):
        return widget.isChecked()
    if isinstance(widget, QComboBox):
        data = widget.currentData()
        return "" if data is None else data
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QLineEdit):
        return widget.text()
    raise TypeError(f"no stored value for {type(widget).__name__}")


def set_widget_value(widget: QWidget, value) -> None:
    """Apply a stored value; anything that no longer fits is left alone.

    The registry hands everything back as strings, and a value saved by an
    older version may name a choice that has since gone (hancom-ai left the AI
    modes in 2.5.13) - neither is worth failing the launch over.
    """
    try:
        if isinstance(widget, QCheckBox) or isinstance(widget, QPushButton):
            widget.setChecked(str(value).lower() in ("true", "1"))
        elif isinstance(widget, QComboBox):
            wanted = None if value in ("", None) else str(value)
            index = widget.findData(wanted)
            if index >= 0:
                widget.setCurrentIndex(index)
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value))
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(float(value)))
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))
    except (TypeError, ValueError):
        pass


def load_favorites() -> list[str]:
    """The saved destinations. Kept as JSON: the registry hands a one-item
    list back as a bare string."""
    try:
        value = json.loads(app_settings().value("favorite_dirs", "[]", str) or "[]")
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [str(p) for p in value if isinstance(p, str) and p][:FAVORITES_MAX]


def save_favorites(paths: list[str]) -> None:
    app_settings().setValue("favorite_dirs", json.dumps(paths[:FAVORITES_MAX], ensure_ascii=False))


def _folder_key(path: str) -> str:
    """Comparable form of a folder path: case, slashes and a trailing \\ aside."""
    path = path.strip()
    return os.path.normcase(os.path.abspath(path)) if path else ""


def show_in_explorer(path: str) -> None:
    """Open the folder holding `path` with `path` selected (Windows), or the
    folder itself elsewhere."""
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


def startup_paths(arguments: list[str]) -> list[str]:
    """Files handed to the program on launch.

    That is how a PDF dropped onto the exe, or opened through "Open with",
    arrives: as command-line arguments. Anything that is not an existing path
    is ignored rather than reported - it can only be a stray switch.
    """
    return [a for a in arguments if a and os.path.exists(a)]


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #


class DropZone(QFrame):
    """Dashed panel that accepts dropped PDF files and folders."""

    filesDropped = Signal(list)
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("DropZone")
        self.setProperty("dragActive", False)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(120)

        icon = QLabel("⬇")
        icon.setObjectName("DropIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel(tr("files.drop_hint"))
        hint.setObjectName("DropHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.addStretch()
        layout.addWidget(icon)
        layout.addWidget(hint)
        layout.addStretch()

    def _refresh(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._refresh(True)

    def dragLeaveEvent(self, event):
        self._refresh(False)

    def dropEvent(self, event):
        self._refresh(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class WheelGuard(QObject):
    """Keep the mouse wheel from editing whatever it happens to be hovering.

    Every selector and spin box sits inside a scroll area, so scrolling the
    page over one of them would silently change a setting instead - and the
    user has no reason to suspect the timeout or the DPI just moved. The
    gesture is handed to the scroll area behind the widget, which is what the
    person meant by it.
    """

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        parent = obj.parentWidget() if isinstance(obj, QWidget) else None
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                QApplication.sendEvent(parent.viewport(), event)
                break
            parent = parent.parentWidget()
        return True


def card(*widgets, spacing: int = 12, expand: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    # Without a fixed vertical policy Qt shrinks the cards past their sizeHint
    # when the window is short, and the option labels start overlapping.
    frame.setSizePolicy(
        QSizePolicy.Policy.Preferred,
        QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed,
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(spacing)
    for w in widgets:
        if isinstance(w, QWidget):
            layout.addWidget(w)
        else:
            layout.addLayout(w)
    return frame


def section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionLabel")
    return label


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def help_html(text: str) -> str:
    """Help text for a tooltip: one paragraph per blank-line-separated block.

    Rich text is what makes Qt wrap a tooltip; plain text runs off as one
    line as wide as the screen.
    """
    return "".join(f"<p>{html.escape(part)}</p>" for part in text.split("\n\n"))


class HelpPopup(QLabel):
    """The explanation box a (?) opens. One for the whole program.

    Its own window rather than QToolTip: Qt's tooltip came up again by itself
    a moment later, at the mouse pointer, so the box jumped and seemed slow.
    This one opens at once, in one place under the mark, and stays put until
    the mouse leaves the mark.
    """

    WIDTH = 360

    def __init__(self):
        super().__init__(
            None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
        )
        self.setObjectName("HelpPopup")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setFixedWidth(self.WIDTH)
        self.owner: QWidget | None = None

    def show_for(self, mark: QWidget, text: str) -> None:
        self.owner = mark
        self.setText(text)
        self.adjustSize()
        self.setFixedHeight(self.heightForWidth(self.WIDTH))
        # Under the mark, left edges lined up; above it, or shifted left,
        # when the screen ends first.
        screen = (mark.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        below = mark.mapToGlobal(QPoint(0, mark.height() + 6))
        x = min(below.x(), screen.right() - self.width() - 4)
        y = below.y()
        if y + self.height() > screen.bottom():
            y = mark.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6
        self.move(max(x, screen.left() + 4), max(y, screen.top() + 4))
        self.show()
        self.raise_()

    def hide_for(self, mark: QWidget) -> None:
        if self.owner is mark:
            self.hide()
            self.owner = None


_help_popup: HelpPopup | None = None


def help_popup() -> HelpPopup:
    """The shared popup, made again if Qt has deleted it (a top-level
    widget goes when the application's windows are torn down)."""
    global _help_popup
    if _help_popup is None or not shiboken6.isValid(_help_popup):
        _help_popup = HelpPopup()
    return _help_popup


class HelpMark(QWidget):
    """The (?) after a setting: pointing at it explains the setting.

    Drawn as one icon - the ring and the question mark from the same pen,
    the mark centred on its actual outline - rather than a "?" in a
    bordered label, where the character sat off-centre in its ring.
    """

    SIZE = 18

    def __init__(self, text: str):
        super().__init__()
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.help_text = help_html(text)
        self.setAccessibleName("?")
        self.setAccessibleDescription(text)
        self._hovered = False

    def _palette(self) -> dict:
        return theme.DARK if getattr(self.window(), "dark", False) else theme.LIGHT

    def paintEvent(self, event):
        palette = self._palette()
        color = QColor(palette["accent"] if self._hovered else palette["text_muted"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ring = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(ring)
            glyph = QColor("#ffffff")
        else:
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(ring.adjusted(0.5, 0.5, -0.5, -0.5))
            glyph = color
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        path = QPainterPath()
        path.addText(0, 0, font, "?")
        box = path.boundingRect()
        path.translate(ring.center() - box.center())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        painter.drawPath(path)
        painter.end()

    def show_help(self) -> None:
        help_popup().show_for(self, self.help_text)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        self.show_help()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        help_popup().hide_for(self)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.show_help()

    def hideEvent(self, event):
        # "More options" folded away under the pointer, or the window closed.
        help_popup().hide_for(self)
        super().hideEvent(event)


def with_help(widget: QWidget, key: str) -> QWidget:
    """The widget followed by its (?)."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(widget)
    row.addWidget(HelpMark(tr(key)))
    # Only as wide as its contents: a block that grew took the spare room of
    # its row, and the format after "Tagged PDF" drifted off to the right.
    box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return box


# Every setting's box is this wide, in Options and in More options alike.
FIELD_WIDTH = 166
# Label column: as wide as the longest label, but no wider than this - a
# longer one wraps onto a second line instead of pushing the boxes apart.
LABEL_MAX_WIDTH = 116


GRID_SPACING = 6
GAP_WIDTH = 18


def settings_grid(
    rows: list, checks: list, labels: list[QLabel], check_grids: list[QGridLayout]
) -> QVBoxLayout:
    """Two even columns: label, box, (?) | label, box, (?), checkboxes below.

    `rows` holds pairs of (label key, widget, help key) - either may be None -
    or a bare widget that spans the whole width. `checks` are (checkbox, help
    key) pairs, two to a line, in a grid of their own: a long checkbox text
    then cannot push the boxes above out of line. The labels and the checkbox
    grids are collected so the window can size them once the font is known
    (MainWindow._align_labels), which lines Options and More options up.
    """
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(GRID_SPACING)
    grid.setVerticalSpacing(8)
    # 0 label, 1 box, 2 (?), 3 gap between the halves, 4 label, 5 box, 6 (?)
    # The gap stays fixed and spare width goes to the right: in a maximised
    # window a stretching gap pushed the second column off to mid-screen.
    grid.setColumnMinimumWidth(3, GAP_WIDTH)
    grid.setColumnStretch(7, 1)
    # Qt leaves out the spacing next to an empty column - a spacer item counts
    # as empty too - so a grid without a full-width row put its second half
    # 6 px further left than one with. A blank widget keeps the column real.
    gap = QWidget()
    gap.setFixedSize(GAP_WIDTH, 1)
    grid.addWidget(gap, 0, 3)
    row = 0
    for item in rows:
        if isinstance(item, QWidget):
            grid.addWidget(item, row, 0, 1, 8)
            row += 1
            continue
        for side, cell in enumerate(item):
            if cell is None:
                continue
            key, widget, help_key = cell
            base = side * 4
            label = QLabel(tr(key))
            label.setWordWrap(True)
            labels.append(label)
            widget.setFixedWidth(FIELD_WIDTH)
            grid.addWidget(label, row, base)
            grid.addWidget(widget, row, base + 1)
            grid.addWidget(HelpMark(tr(help_key)), row, base + 2)
        row += 1
    boxes = QGridLayout()
    boxes.setContentsMargins(0, 0, 0, 0)
    boxes.setHorizontalSpacing(0)
    boxes.setVerticalSpacing(8)
    boxes.setColumnStretch(2, 1)
    for i, (box, help_key) in enumerate(checks):
        boxes.addWidget(with_help(box, help_key), i // 2, i % 2, Qt.AlignmentFlag.AlignLeft)
    check_grids.append(boxes)

    outer = QVBoxLayout()
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(8)
    outer.addLayout(grid)
    outer.addLayout(boxes)
    return outer


def eye_icon(open_: bool) -> QIcon:
    """The password field's show/hide button: an eye, struck through or not."""
    size = 32
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#8a909c"), 2.4)
    painter.setPen(pen)
    # Almond outline from two arcs, then the pupil.
    painter.drawArc(4, 8, 24, 20, 30 * 16, 120 * 16)
    painter.drawArc(4, 4, 24, 20, 210 * 16, 120 * 16)
    painter.setBrush(QColor("#8a909c"))
    painter.drawEllipse(QPointF(16, 16), 3.6, 3.6)
    if not open_:
        painter.drawLine(QPointF(6, 26), QPointF(26, 6))
    painter.end()
    return QIcon(pixmap)


def section_row(text: str, *widgets: QWidget) -> QHBoxLayout:
    """A section caption with controls at the right end of the same line."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(section(text))
    row.addStretch()
    for widget in widgets:
        row.addWidget(widget)
    return row


def reset_button(tooltip_key: str) -> QPushButton:
    button = QPushButton(tr("reset.defaults"))
    button.setObjectName("Small")
    button.setToolTip(tr(tooltip_key))
    return button


class AboutDialog(QDialog):
    """Help → About: version, what it is built on, licence and authors."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("About")
        self.setWindowTitle(tr("about.title", app=APP_NAME))
        blob = f"{REPO_URL}/blob/main"
        # Links take the theme's accent: the default link colour all but
        # vanished on the light background.
        accent = (theme.DARK if getattr(parent, "dark", False) else theme.LIGHT)["accent"]
        link = f"style='color:{accent}'"
        self.text = QLabel(
            f"<h2>{APP_NAME}</h2>"
            f"<p>{tr('about.version', version=__version__)}</p>"
            f"<p>{tr('about.description')}</p>"
            f"<p>opendataloader-pdf {odl_version()} · Qt {qVersion()}</p>"
            f"<p>© 2026 bantamer, k1llrouz</p>"
            f"<p>{tr('about.license')}<br>"
            f"<a {link} href='{blob}/LICENSE'>GNU GPL v3</a> · "
            f"<a {link} href='{blob}/THIRD_PARTY_NOTICES.md'>{tr('about.third_party')}</a> · "
            f"<a {link} href='{REPO_URL}'>{tr('about.source')}</a></p>"
        )
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setOpenExternalLinks(True)
        self.text.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(12)
        layout.addWidget(self.text)
        layout.addWidget(buttons)
        self.setMinimumWidth(420)


class GuideDialog(QDialog):
    """Help → User guide (F1): the guide that ships with the program.

    Bundled rather than a link to GitHub, so it opens offline and matches the
    version installed. Russian or English; the other languages get English.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Guide")
        self.setWindowTitle(tr("guide.title"))
        language = "ru" if i18n.current() == "ru" else "en"
        accent = (theme.DARK if getattr(parent, "dark", False) else theme.LIGHT)["accent"]

        self.browser = QTextBrowser()
        self.browser.setObjectName("GuideText")
        self.browser.setOpenExternalLinks(True)
        self.browser.document().setDefaultStyleSheet(f"a {{ color: {accent}; }}")
        try:
            text = resource(f"docs/guide.{language}.md").read_text(encoding="utf-8")
        except OSError:
            text = f"[{REPO_URL}]({REPO_URL})"
        self.browser.setMarkdown(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.addWidget(self.browser, 1)
        layout.addWidget(buttons)
        self.resize(700, 760)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #


class MainWindow(QWidget):
    languageChanged = Signal(str)
    resetRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(760)

        self.inputs: list[str] = []
        self.thread: QThread | None = None
        self.worker: ConvertWorker | None = None
        self.output_dir = ""
        # Outcome of the last run per PDF, keyed by normalised path, and the
        # formats it produced - what the file list marks and opens.
        self.results: dict[str, dict] = {}
        self.result_formats: list[str] = []
        # Set when the window is replaced after Settings → Reset, so that
        # closing it does not write the old options straight back.
        self.skip_save = False
        stored_theme = app_settings().value("dark", None)
        self.theme_mode = (
            "system"
            if stored_theme is None
            else ("dark" if str(stored_theme).lower() in ("true", "1") else "light")
        )
        _set_color_scheme(self.theme_mode)
        self.dark = (
            _system_prefers_dark() if self.theme_mode == "system" else self.theme_mode == "dark"
        )
        self.elapsed = QElapsedTimer()
        self.current_file = ""
        self.tick = QTimer(self)
        self.tick.setInterval(500)
        self.tick.timeout.connect(self._update_elapsed)

        self.update_release: dict | None = None
        self._guide: GuideDialog | None = None
        self.favorites = load_favorites()
        self._form_labels: list[QLabel] = []
        self._check_grids: list[QGridLayout] = []
        self._build_ui()
        # What every option looks like untouched - the "Reset" buttons go back
        # to this. Taken before the saved settings are applied on top.
        self._defaults = {
            widget: widget_value(widget)
            for widget in self._main_option_widgets() + self._advanced_widgets()
        }
        self._restore_settings()
        self._sync_format_options()
        self._apply_theme()
        self._align_labels()
        # "As in Windows" keeps following Windows while the program is open.
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._follow_system_theme)
        if _latest_release is not None:  # found before a language switch
            self.show_update(_latest_release)
        # The drop zone gives way to the file list after the first file, and the
        # list takes no drops - so without this, a second file had nowhere to
        # land. Children that do not take drops pass them up to the window.
        self.setAcceptDrops(True)
        # Open at the natural content height where the screen allows it, but
        # never demand more than a small laptop can give.
        natural = self.sizeHint().height()
        self.setMinimumHeight(min(560, natural))
        self.resize(820, min(natural, self.screen().availableGeometry().height() - 80))
        # Then where the user left it last time, as Windows programs do; Qt
        # pulls it back onto the screen if that monitor is gone.
        geometry = app_settings().value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        # Tied to the window, so the timer dies with it: a window replaced at
        # once (a language switch right after start) must not run this late.
        QTimer.singleShot(0, self, self._check_java)

    # -- construction ------------------------------------------------------ #

    def _build_ui(self) -> None:
        # Cards live inside a scroll area so a short window scrolls instead of
        # compressing the cards into each other; the header and the action bar
        # stay pinned.
        body = QWidget()
        body.setObjectName("Body")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 8, 0)
        self.body_layout.setSpacing(16)
        self.body_layout.addWidget(self._files_card())
        # Destination sits right under the source: both are answered before the
        # run and neither is worth scrolling past the options block to reach.
        self.body_layout.addWidget(self._output_card())
        self.body_layout.addWidget(self._options_card())
        self.log_card = self._log_card()
        self.body_layout.addWidget(self.log_card, 1)
        self.body_layout.addStretch()

        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setObjectName("Scroll")
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Layouts are made to fit 760 px in every language; should a future
        # translation still overflow, a scroll bar beats silently clipped
        # buttons.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(16)
        root.setMenuBar(self._menu_bar())
        root.addWidget(scroll, 1)
        root.addLayout(self._action_bar())
        self._sync_actions()

        self._wheel_guard = WheelGuard(self)
        for widget in self.findChildren(QComboBox) + self.findChildren(QAbstractSpinBox):
            widget.installEventFilter(self._wheel_guard)
            # Qt hands focus to a WheelFocus widget before the wheel event is
            # even delivered, so the guard alone still leaves a cursor blinking
            # in whatever the pointer passed over. StrongFocus is the same
            # policy minus the wheel.
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


    def _menu_bar(self) -> QMenuBar:
        """File, Settings and Help, the way a Windows program has them.

        Language and theme live here now rather than as controls in the
        window; every command that also has a button stays in step with it
        through _sync_actions().
        """
        bar = QMenuBar(self)

        file = bar.addMenu(tr("menu.file"))
        self.act_add_files = file.addAction(tr("menu.add_files"), self.pick_files)
        self.act_add_files.setShortcut(QKeySequence.StandardKey.Open)
        self.act_add_folder = file.addAction(tr("menu.add_folder"), self.pick_folder)
        self.act_add_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.act_clear = file.addAction(tr("menu.clear"), self.clear_inputs)
        file.addSeparator()
        self.act_output = file.addAction(tr("menu.output"), self.pick_output)
        self.act_open_output = file.addAction(tr("menu.open_output"), self.open_output)
        file.addSeparator()
        # Qt calls the main Enter key "Return"; Windows programs say Enter.
        # Text after a tab is what the menu shows as the shortcut, and the
        # keys themselves - main and keypad Enter - are wired up separately.
        self.act_convert = file.addAction(tr("menu.convert") + "	Ctrl+Enter", self.start)
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self, self._convert_shortcut)
        self.act_stop = file.addAction(tr("menu.stop"), self.cancel)
        self.act_stop.setShortcut(QKeySequence("Esc"))
        file.addSeparator()
        self.act_exit = file.addAction(tr("menu.exit"), self.close)
        self.act_exit.setShortcut(QKeySequence("Ctrl+Q"))

        settings = bar.addMenu(tr("menu.settings"))
        self.language_menu = settings.addMenu(tr("menu.language"))
        languages = QActionGroup(self)
        self.language_actions = {}
        for code, name in i18n.LANGUAGES.items():
            action = self.language_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(code == i18n.current())
            action.triggered.connect(lambda _=False, c=code: self._choose_language(c))
            languages.addAction(action)
            self.language_actions[code] = action

        theme_menu = settings.addMenu(tr("menu.theme"))
        themes = QActionGroup(self)
        self.theme_actions = {}
        for mode in ("system", "light", "dark"):
            action = theme_menu.addAction(tr(f"menu.theme_{mode}"))
            action.setCheckable(True)
            action.setChecked(mode == self.theme_mode)
            action.triggered.connect(lambda _=False, m=mode: self._set_theme(m))
            themes.addAction(action)
            self.theme_actions[mode] = action

        settings.addSeparator()
        self.act_show_log = settings.addAction(tr("menu.show_log"))
        self.act_show_log.setCheckable(True)
        self.act_show_log.setShortcut(QKeySequence("Ctrl+L"))
        self.act_show_log.toggled.connect(self._toggle_log)
        self.act_auto_updates = settings.addAction(tr("menu.auto_updates"))
        self.act_auto_updates.setCheckable(True)
        self.act_auto_updates.setChecked(auto_update_check_enabled())
        self.act_auto_updates.toggled.connect(
            lambda on: app_settings().setValue("check_updates", on)
        )
        settings.addSeparator()
        self.act_reset = settings.addAction(tr("menu.reset"), self._confirm_reset)

        help_menu = bar.addMenu(tr("menu.help"))
        self.act_guide = help_menu.addAction(tr("menu.guide"), self._open_guide)
        self.act_guide.setShortcut(QKeySequence.StandardKey.HelpContents)
        self.act_report = help_menu.addAction(
            tr("menu.report"),
            lambda: QDesktopServices.openUrl(QUrl(f"{REPO_URL}/issues/new")),
        )
        self.act_check_updates = help_menu.addAction(
            tr("menu.check_updates"), lambda: check_for_updates(manual=True)
        )
        help_menu.addSeparator()
        self.act_about = help_menu.addAction(
            tr("menu.about", app=APP_NAME), self._show_about
        )

        # Filled in by show_update() when a newer release turns up; it sits
        # at the right end of the menu bar, out of the way until then.
        self.update_link = QLabel()
        self.update_link.setObjectName("UpdateLink")
        self.update_link.setTextFormat(Qt.TextFormat.RichText)
        # A click installs the update where this copy can be updated in
        # place, and opens the download page otherwise.
        self.update_link.linkActivated.connect(lambda _: offer_update(self.update_release))
        self.update_link.hide()
        bar.setCornerWidget(self.update_link, Qt.Corner.TopRightCorner)
        return bar

    def _files_card(self) -> QFrame:
        self.drop = DropZone()
        self.drop.filesDropped.connect(self.add_paths)
        self.drop.clicked.connect(self.pick_files)

        self.file_list = QListWidget()
        self.file_list.setVisible(False)
        self.file_list.itemDoubleClicked.connect(self._open_result)
        self.file_list.setMaximumHeight(150)
        # Pick one or several (Ctrl, Shift) and take them off with Delete, the
        # "Remove" button or the right-click menu - without starting over.
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.itemSelectionChanged.connect(self._sync_actions)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._file_menu)
        self.file_list.installEventFilter(self)

        add_btn = QPushButton(tr("files.add_files"))
        add_btn.clicked.connect(self.pick_files)
        add_folder_btn = QPushButton(tr("files.add_folder"))
        add_folder_btn.clicked.connect(self.pick_folder)
        self.remove_btn = QPushButton(tr("files.remove"))
        self.remove_btn.setToolTip(tr("files.remove_tooltip"))
        self.remove_btn.clicked.connect(self.remove_selected)
        self.remove_btn.setVisible(False)
        self.clear_btn = QPushButton(tr("files.clear"))
        self.clear_btn.setObjectName("Danger")
        self.clear_btn.clicked.connect(self.clear_inputs)
        self.clear_btn.setVisible(False)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(add_btn)
        buttons.addWidget(add_folder_btn)
        buttons.addStretch()
        buttons.addWidget(self.remove_btn)
        buttons.addWidget(self.clear_btn)

        return card(section(tr("files.section")), self.drop, self.file_list, buttons)

    def _options_card(self) -> QFrame:
        self.format_boxes: dict[str, QCheckBox] = {}
        formats = QHBoxLayout()
        formats.setSpacing(12)
        for key, label in FORMATS:
            box = QCheckBox(tr(label))
            box.setChecked(key == "markdown")
            self.format_boxes[key] = box
            formats.addWidget(with_help(box, FORMAT_HELP[key]) if key in FORMAT_HELP else box)
        formats.addStretch()

        # -- images: the single setting that decides how long a datasheet takes
        self.image_mode = QComboBox()
        self.image_mode.addItem(tr("images.files"), "external")
        self.image_mode.addItem(tr("images.inside"), "embedded")
        self.image_mode.addItem(tr("images.none"), "off")
        self.image_mode.currentIndexChanged.connect(self._toggle_image_dpi)

        # Worth trying before turning images off entirely: 96 DPI keeps the
        # illustrations but cuts the memory the renderer needs for them.
        # A list of the usual values rather than a counter that crept along
        # one DPI per click.
        self.image_dpi = QComboBox()
        for dpi in IMAGE_DPI_CHOICES:
            self.image_dpi.addItem(f"{dpi} DPI", str(dpi))
        self.image_dpi.setCurrentIndex(IMAGE_DPI_CHOICES.index(IMAGE_DPI_DEFAULT))

        self.pages_edit = QLineEdit()
        self.pages_edit.setPlaceholderText(tr("pages.placeholder"))

        # The usual values in a list, like the resolution.
        self.timeout_box = QComboBox()
        for seconds in TIMEOUT_CHOICES:
            if seconds == 0:
                text = tr("timeout.none")
            elif seconds < 60:
                text = f"{seconds}{tr('timeout.suffix')}"
            else:
                text = tr("timeout.minutes", n=seconds // 60)
            self.timeout_box.addItem(text, str(seconds))
        self.timeout_box.setCurrentIndex(TIMEOUT_CHOICES.index(TIMEOUT_DEFAULT))

        self.opt_struct = QCheckBox(tr("struct.label"))
        self.opt_sanitize = QCheckBox(tr("sanitize.label"))
        # On by default: that is how every result looked before the switch.
        self.opt_page_sep = QCheckBox(tr("page_sep.label"))
        self.opt_page_sep.setChecked(True)
        self.opt_plain_md = QCheckBox(tr("plain_md.label"))
        # On by default: a third fewer tokens for a language model, nothing
        # lost (the clean-ups run on HTML tables first).
        self.opt_plain_md.setChecked(True)
        self.opt_clean_figures = QCheckBox(tr("clean_figures.label"))
        self.opt_clean_figures.setChecked(True)
        self.opt_plain_md.toggled.connect(self._sync_format_options)
        for box in self.format_boxes.values():
            box.toggled.connect(self._sync_format_options)

        grid = settings_grid(
            [
                (("images.label", self.image_mode, "images.tooltip"),
                 ("dpi.label", self.image_dpi, "dpi.tooltip")),
                hint(tr("images.hint")),
                (("pages.label", self.pages_edit, "pages.tooltip"),
                 ("timeout.label", self.timeout_box, "timeout.tooltip")),
            ],
            [
                (self.opt_struct, "struct.tooltip"),
                (self.opt_sanitize, "sanitize.tooltip"),
                (self.opt_page_sep, "page_sep.tooltip"),
                (self.opt_plain_md, "plain_md.tooltip"),
                (self.opt_clean_figures, "clean_figures.tooltip"),
            ],
            self._form_labels,
            self._check_grids,
        )

        self.more_btn = QPushButton(tr("advanced.button") + "  ▾")
        self.more_btn.setObjectName("Ghost")
        self.more_btn.setCheckable(True)
        self.more_btn.toggled.connect(self._toggle_advanced)
        more_row = QHBoxLayout()
        more_row.addWidget(self.more_btn)
        more_row.addStretch()

        self.reset_main_btn = reset_button("reset.main_tooltip")
        self.reset_main_btn.clicked.connect(
            lambda: self._reset_options(self._main_option_widgets())
        )

        return card(
            section_row(tr("formats.section"), self.reset_main_btn),
            formats,
            section(tr("options.section")),
            grid,
            more_row,
            self._advanced_panel(),
        )

    def _advanced_panel(self) -> QWidget:
        panel = QWidget()
        panel.setVisible(False)
        self.advanced = panel

        self.reading_order = QComboBox()
        self.reading_order.addItem("xycut", "xycut")
        self.reading_order.addItem(tr("reading_order.off"), "off")

        self.table_method = QComboBox()
        self.table_method.addItem(tr("tables.default"), "default")
        self.table_method.addItem(tr("tables.cluster"), "cluster")

        self.image_format = QComboBox()
        self.image_format.addItem("PNG", "png")
        self.image_format.addItem("JPEG", "jpeg")

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, max(1, os.cpu_count() or 1))
        self.threads_spin.setValue(1)

        self.space_ratio = QDoubleSpinBox()
        self.space_ratio.setRange(0.01, 1.00)
        self.space_ratio.setSingleStep(0.01)
        self.space_ratio.setDecimals(2)
        self.space_ratio.setValue(SPACE_RATIO_DEFAULT)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText(tr("password.placeholder"))
        self.password_eye = self.password_edit.addAction(
            eye_icon(open_=True), QLineEdit.ActionPosition.TrailingPosition
        )
        self.password_eye.setToolTip(tr("password.show"))
        self.password_eye.triggered.connect(self._toggle_password)

        self.opt_line_breaks = QCheckBox(tr("line_breaks.label"))
        self.opt_header_footer = QCheckBox(tr("header_footer.label"))
        self.opt_strikethrough = QCheckBox(tr("strikethrough.label"))
        self.opt_hidden = QCheckBox(tr("hidden_text.label"))

        self.hybrid = QComboBox()
        self.hybrid.addItem(tr("hybrid.off"), None)
        self.hybrid.addItem("docling-fast", "docling-fast")
        self.hybrid.currentIndexChanged.connect(self._toggle_hybrid)

        self.hybrid_url = QLineEdit()
        self.hybrid_url.setPlaceholderText(HYBRID_DEFAULT_URL)
        self.hybrid_url.setEnabled(False)

        grid = settings_grid(
            [
                (("reading_order.label", self.reading_order, "reading_order.tooltip"),
                 ("tables.label", self.table_method, "tables.tooltip")),
                (("image_format.label", self.image_format, "image_format.tooltip"),
                 ("threads.label", self.threads_spin, "threads.tooltip")),
                (("password.label", self.password_edit, "password.tooltip"),
                 ("space_ratio.label", self.space_ratio, "space_ratio.tooltip")),
                *(
                    [(("hybrid.label", self.hybrid, "hybrid.tooltip"),
                      ("hybrid_url.label", self.hybrid_url, "hybrid_url.tooltip"))]
                    if AI_MODE else []
                ),
            ],
            [
                (self.opt_line_breaks, "line_breaks.tooltip"),
                (self.opt_header_footer, "header_footer.tooltip"),
                (self.opt_strikethrough, "strikethrough.tooltip"),
                (self.opt_hidden, "hidden_text.tooltip"),
            ],
            self._form_labels,
            self._check_grids,
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addLayout(grid)

        self.reset_advanced_btn = reset_button("reset.advanced_tooltip")
        self.reset_advanced_btn.clicked.connect(
            lambda: self._reset_options(self._advanced_widgets())
        )
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_row.addWidget(self.reset_advanced_btn)
        outer.addLayout(reset_row)
        return panel

    def _output_card(self) -> QFrame:
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText(tr("output.placeholder"))
        self.output_edit.setText(app_settings().value("output_dir", "", str))
        self.output_edit.setClearButtonEnabled(True)
        self.output_edit.editingFinished.connect(self._remember_output_dir)
        self.output_edit.textChanged.connect(self._refresh_favorites)
        browse = QPushButton(tr("output.browse"))
        browse.clicked.connect(self.pick_output)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.output_edit, 1)
        row.addWidget(browse)

        # Favourite folders: folded away like "More options", since most
        # people never need more than one destination.
        self.fav_btn = QPushButton()
        self.fav_btn.setObjectName("Ghost")
        self.fav_btn.setCheckable(True)
        self.fav_btn.toggled.connect(self._toggle_favorites)
        fav_row = QHBoxLayout()
        fav_row.addWidget(self.fav_btn)
        fav_row.addStretch()

        self.fav_panel = QWidget()
        self.fav_panel.setVisible(False)
        panel = QVBoxLayout(self.fav_panel)
        panel.setContentsMargins(0, 0, 0, 0)
        panel.setSpacing(6)
        self.fav_rows = QVBoxLayout()
        self.fav_rows.setSpacing(6)
        panel.addLayout(self.fav_rows)
        self.fav_add = QPushButton(tr("favorites.add"))
        self.fav_add.clicked.connect(self.add_favorite)
        add_row = QHBoxLayout()
        add_row.addWidget(self.fav_add)
        add_row.addStretch()
        panel.addLayout(add_row)
        self._refresh_favorites()

        return card(
            section(tr("output.section")),
            row,
            hint(tr("output.hint")),
            fav_row,
            self.fav_panel,
        )

    def _log_card(self) -> QFrame:
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(90)
        # An unbounded document is what turns a long conversion into a frozen
        # window; 5000 lines is far more than anyone scrolls back through.
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.log_view.setPlaceholderText(tr("log.placeholder"))

        frame = card(section(tr("log.section")), self.log_view, expand=True)
        # Hidden until asked for: a log is noise for the audience this app
        # targets, and it costs ~140 px of window height.
        frame.setVisible(False)
        return frame

    def _action_bar(self) -> QVBoxLayout:
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(False)

        self.status = QLabel(tr("status.empty"))
        self.status.setObjectName("StatusText")

        self.open_btn = QPushButton(tr("action.open_output"))
        self.open_btn.clicked.connect(self.open_output)
        self.open_btn.setVisible(False)

        # The log is for when something went wrong, so that is when the way to
        # it shows up; otherwise it waits in Settings → Show log.
        self.details_link = QLabel()  # text set with the theme's accent
        self.details_link.setObjectName("DetailsLink")
        self.details_link.setTextFormat(Qt.TextFormat.RichText)
        self.details_link.linkActivated.connect(lambda _: self._toggle_log(True))
        self.details_link.setVisible(False)

        self.cancel_btn = QPushButton(tr("action.cancel"))
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.setVisible(False)

        self.run_btn = QPushButton(tr("action.convert"))
        self.run_btn.setObjectName("Primary")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self.start)

        row = QHBoxLayout()
        row.addWidget(self.status)
        row.addWidget(self.details_link)
        row.addStretch(1)
        row.addWidget(self.open_btn)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.run_btn)

        bar = QVBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(self.progress)
        bar.addLayout(row)
        return bar

    # -- theming ----------------------------------------------------------- #

    def _apply_theme(self) -> None:
        palette = theme.DARK if self.dark else theme.LIGHT
        QApplication.instance().setStyleSheet(theme.build_qss(palette))
        for mark in self.findChildren(HelpMark):  # drawn, not styled
            mark.update()
        self.details_link.setText(
            f"<a style='color:{palette['accent']}' href='#log'>{tr('status.details')}</a>"
        )
        if self.update_release is not None:  # the link takes the accent colour
            self.show_update(self.update_release)

    def _set_theme(self, mode: str) -> None:
        """"system" follows Windows; "light" or "dark" is remembered as chosen."""
        self.theme_mode = mode
        _set_color_scheme(mode)
        if mode == "system":
            app_settings().remove("dark")
            self.dark = _system_prefers_dark()
        else:
            self.dark = mode == "dark"
            app_settings().setValue("dark", self.dark)
        self._apply_theme()
        self.theme_actions[mode].setChecked(True)

    def _follow_system_theme(self, *_) -> None:
        if self.theme_mode != "system":
            return  # a scheme the program set itself, or a theme chosen by hand
        dark = _system_prefers_dark()
        if dark != self.dark:
            self.dark = dark
            self._apply_theme()

    # -- updates ----------------------------------------------------------- #

    def show_update(self, release: dict) -> None:
        """The link in the header to a newer release."""
        self.update_release = release
        self.update_link.setToolTip(
            tr("update.install_tooltip" if updates.can_install(release) else "update.link_tooltip")
        )
        accent = (theme.DARK if self.dark else theme.LIGHT)["accent"]
        self.update_link.setText(
            f"<a style='color:{accent}; text-decoration:none' href='{release['url']}'>"
            f"↑ {tr('update.available', version=release['version'])}</a>"
        )
        self.update_link.show()

    # -- menu commands ------------------------------------------------------ #

    def _convert_shortcut(self) -> None:
        if self.act_convert.isEnabled():
            self.start()

    def _choose_language(self, code: str) -> None:
        if code != i18n.current():
            self.languageChanged.emit(code)

    def _confirm_reset(self) -> None:
        answer = QMessageBox.question(self, APP_NAME, tr("reset.confirm"))
        if answer == QMessageBox.StandardButton.Yes:
            self.resetRequested.emit()

    def _open_guide(self) -> None:
        # Not modal: the guide stays open next to the window while you work.
        if self._guide is None:
            self._guide = GuideDialog(self)
        self._guide.show()
        self._guide.raise_()
        self._guide.activateWindow()

    def _show_about(self) -> None:
        AboutDialog(self).exec()

    def _sync_actions(self) -> None:
        """Keep the menu commands in step with the buttons and the state."""
        busy = self._busy()
        has_files = bool(self.inputs)
        self.act_convert.setEnabled(has_files and not busy)
        self.act_stop.setEnabled(busy)
        self.act_clear.setEnabled(has_files)
        self.remove_btn.setEnabled(bool(self.file_list.selectedItems()) and not busy)
        self.act_open_output.setEnabled(
            bool(self.output_dir) and os.path.isdir(self.output_dir) and not busy
        )
        # A new window would lose the running conversion.
        self.language_menu.setEnabled(not busy)
        self.act_reset.setEnabled(not busy)

    # -- defaults --------------------------------------------------------------- #

    def _main_option_widgets(self) -> list[QWidget]:
        """Output formats and the options under them."""
        return [
            *self.format_boxes.values(),
            self.image_mode,
            self.image_dpi,
            self.pages_edit,
            self.timeout_box,
            self.opt_struct,
            self.opt_sanitize,
            self.opt_page_sep,
            self.opt_plain_md,
            self.opt_clean_figures,
        ]

    def _advanced_widgets(self) -> list[QWidget]:
        return [
            self.reading_order,
            self.table_method,
            self.image_format,
            self.threads_spin,
            self.password_edit,
            self.hybrid,
            self.space_ratio,
            self.hybrid_url,
            self.opt_line_breaks,
            self.opt_header_footer,
            self.opt_strikethrough,
            self.opt_hidden,
        ]

    def _reset_options(self, widgets: list[QWidget]) -> None:
        for widget in widgets:
            set_widget_value(widget, self._defaults[widget])

    def _toggle_password(self) -> None:
        hidden = self.password_edit.echoMode() == QLineEdit.EchoMode.Password
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password
        )
        self.password_eye.setIcon(eye_icon(open_=not hidden))
        self.password_eye.setToolTip(tr("password.hide" if hidden else "password.show"))

    # -- favourite folders ------------------------------------------------------ #

    def _toggle_favorites(self, shown: bool) -> None:
        self.fav_panel.setVisible(shown)
        self._refresh_favorites()

    def _refresh_favorites(self, *_) -> None:
        """Redraw the saved folders and the buttons around them."""
        while self.fav_rows.count():
            item = self.fav_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hidden at once: deleteLater alone left the old rows floating
                # over the new ones until the next event loop pass.
                widget.hide()
                widget.deleteLater()

        current = _folder_key(self.output_edit.text())
        metrics = QFontMetrics(self.font())
        for path in self.favorites:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(6)
            use = QPushButton(metrics.elidedText(path, Qt.TextElideMode.ElideMiddle, 520))
            use.setObjectName("Favorite")
            use.setProperty("current", _folder_key(path) == current)
            use.setToolTip(tr("favorites.use", path=path))
            use.clicked.connect(lambda _=False, p=path: self.use_favorite(p))
            remove = QPushButton("✕")
            remove.setObjectName("FavoriteRemove")
            remove.setToolTip(tr("favorites.remove"))
            remove.clicked.connect(lambda _=False, p=path: self.remove_favorite(p))
            line.addWidget(use, 1)
            line.addWidget(remove)
            self.fav_rows.addWidget(row)
        if not self.favorites:
            self.fav_rows.addWidget(hint(tr("favorites.empty")))

        text = self.output_edit.text().strip()
        known = any(_folder_key(p) == current for p in self.favorites)
        full = len(self.favorites) >= FAVORITES_MAX
        self.fav_add.setEnabled(bool(text) and not known and not full)
        self.fav_add.setToolTip(
            tr("favorites.full", max=FAVORITES_MAX) if full
            else tr("favorites.add_tooltip")
        )
        arrow = "  ▴" if self.fav_btn.isChecked() else "  ▾"
        self.fav_btn.setText(tr("favorites.button", count=len(self.favorites)) + arrow)

    def add_favorite(self) -> None:
        path = self.output_edit.text().strip()
        if not path or len(self.favorites) >= FAVORITES_MAX:
            return
        if any(_folder_key(p) == _folder_key(path) for p in self.favorites):
            return
        self.favorites.append(os.path.abspath(path))
        save_favorites(self.favorites)
        self._refresh_favorites()

    def remove_favorite(self, path: str) -> None:
        self.favorites = [p for p in self.favorites if p != path]
        save_favorites(self.favorites)
        self._refresh_favorites()

    def use_favorite(self, path: str) -> None:
        self.output_edit.setText(path)
        self._remember_output_dir()

    # -- removing files from the list -------------------------------------------- #

    def remove_selected(self) -> None:
        """Take the selected entries off the list; nothing is deleted on disk."""
        if self._busy():
            return
        items = self.file_list.selectedItems()
        if not items:
            return
        first = min(self.file_list.row(item) for item in items)
        chosen = {item.data(Qt.ItemDataRole.UserRole) for item in items}
        self.inputs = [path for path in self.inputs if path not in chosen]
        self._refresh_file_list()
        # Keep a selection where the removed ones were, so Delete can be
        # pressed again to go on down the list.
        if self.file_list.count():
            self.file_list.setCurrentRow(min(first, self.file_list.count() - 1))

    def eventFilter(self, obj, event):
        # Delete in the file list. A key event rather than a shortcut: a
        # shortcut fires only in the active window, a key press wherever the
        # list has the focus.
        if (
            obj is self.file_list
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Delete
        ):
            self.remove_selected()
            return True
        return super().eventFilter(obj, event)

    def _file_menu(self, position) -> None:
        item = self.file_list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.file_list.setCurrentItem(item)
        path = item.data(Qt.ItemDataRole.UserRole)
        status, _ = self._status_of(path)

        menu = QMenu(self)
        remove = menu.addAction(tr("files.menu_remove"), self.remove_selected)
        remove.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        remove.setEnabled(not self._busy())
        menu.addSeparator()
        open_result = menu.addAction(tr("files.menu_open_result"), lambda: self._open_result(item))
        open_result.setEnabled(status in ("ok", "degraded", "partial"))
        menu.addAction(tr("files.menu_show"), lambda: show_in_explorer(path))
        menu.exec(self.file_list.viewport().mapToGlobal(position))

    # -- remembered settings ---------------------------------------------- #

    def _persisted_widgets(self) -> list[tuple[str, QWidget]]:
        """Everything worth having back on the next launch.

        Left out on purpose: the page range, which belongs to one document, and
        the PDF password, which has no business sitting in the registry. The
        destination folder is saved on its own, the moment it is chosen.
        """
        widgets: list[tuple[str, QWidget]] = [
            (f"format/{key}", box) for key, box in self.format_boxes.items()
        ]
        widgets += [
            ("image_mode", self.image_mode),
            ("image_dpi", self.image_dpi),
            ("timeout", self.timeout_box),
            ("use_struct_tree", self.opt_struct),
            ("sanitize", self.opt_sanitize),
            ("page_separators", self.opt_page_sep),
            ("plain_markdown_tables", self.opt_plain_md),
            ("clean_figures", self.opt_clean_figures),
            ("reading_order", self.reading_order),
            ("table_method", self.table_method),
            ("image_format", self.image_format),
            ("threads", self.threads_spin),
            ("space_ratio", self.space_ratio),
            ("keep_line_breaks", self.opt_line_breaks),
            ("include_header_footer", self.opt_header_footer),
            ("detect_strikethrough", self.opt_strikethrough),
            ("drop_hidden_text", self.opt_hidden),
            ("hybrid", self.hybrid),
            ("hybrid_url", self.hybrid_url),
            ("advanced_open", self.more_btn),
            ("favorites_open", self.fav_btn),
        ]
        return widgets

    def _save_settings(self) -> None:
        settings = app_settings()
        settings.setValue("geometry", self.saveGeometry())
        for key, widget in self._persisted_widgets():
            settings.setValue(f"options/{key}", widget_value(widget))

    def _restore_settings(self) -> None:
        settings = app_settings()
        for key, widget in self._persisted_widgets():
            value = settings.value(f"options/{key}")
            if value is not None:
                set_widget_value(widget, value)

    # -- input handling ---------------------------------------------------- #

    def pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr("dialog.pick_files"), str(Path.home()), "PDF (*.pdf)"
        )
        self.add_paths(paths)

    def pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, tr("dialog.pick_folder"), str(Path.home())
        )
        if path:
            self.add_paths([path])

    def pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            tr("dialog.pick_output"),
            self.output_edit.text() or str(Path.home()),
        )
        if path:
            self.output_edit.setText(path)
            self._remember_output_dir()

    def _remember_output_dir(self) -> None:
        app_settings().setValue("output_dir", self.output_edit.text().strip())

    def add_paths(self, paths: list[str]) -> None:
        added = 0
        for raw in paths:
            path = os.path.abspath(raw)
            if path in self.inputs:
                continue
            if os.path.isdir(path) or path.lower().endswith(".pdf"):
                self.inputs.append(path)
                added += 1
        if added:
            self._refresh_file_list()

    def clear_inputs(self) -> None:
        self.inputs.clear()
        self.results.clear()
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.file_list.clear()
        for path in self.inputs:
            marker = "📁" if os.path.isdir(path) else "📄"
            item = QListWidgetItem(f"{marker}  {os.path.basename(path)}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.file_list.addItem(item)
        self._apply_statuses()

        has_files = bool(self.inputs)
        self.file_list.setVisible(has_files)
        self.clear_btn.setVisible(has_files)
        self.remove_btn.setVisible(has_files)
        self.drop.setVisible(not has_files)
        self._sync_actions()
        if self._busy():
            # Files added mid-run wait for the next one; re-enabling Convert
            # here would let a second run start on top of the first.
            return
        self.run_btn.setEnabled(has_files)
        self._set_status(
            tr("status.ready", count=len(self.inputs))
            if has_files
            else tr("status.empty")
        )

    def _busy(self) -> bool:
        return self.thread is not None

    # -- per-file outcome in the list ------------------------------------- #

    def _status_of(self, path: str) -> tuple[str | None, list[dict]]:
        """What the last run made of a list entry - a PDF or a whole folder."""
        key = os.path.normcase(os.path.abspath(path))
        if os.path.isdir(path):
            prefix = key.rstrip("\\/") + os.sep
            runs = [r for k, r in self.results.items() if k.startswith(prefix)]
        else:
            runs = [self.results[key]] if key in self.results else []
        if not runs:
            return None, []
        good = [r for r in runs if r["status"] in ("ok", "degraded")]
        if len(good) == len(runs):
            degraded = any(r["status"] == "degraded" for r in runs)
            return ("degraded" if degraded else "ok"), runs
        if good:
            return "partial", runs
        timed_out = all(r["status"] == "timed_out" for r in runs)
        return ("timed_out" if timed_out else "failed"), runs

    def _apply_statuses(self) -> None:
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            status, runs = self._status_of(path)
            if status is None:
                item.setIcon(QIcon())
                item.setToolTip(path)
                continue
            item.setIcon(status_icon(status))
            good = sum(r["status"] in ("ok", "degraded") for r in runs)
            text = (
                tr("list.partial", ok=good, total=len(runs))
                if status == "partial"
                else tr(f"list.{status}")
            )
            tip = f"{path}\n{text}"
            if good:
                tip += "\n" + tr("list.open_hint")
            item.setToolTip(tip)

    def _open_result(self, item: QListWidgetItem) -> None:
        """Double-click: the converted file itself, or its folder."""
        path = item.data(Qt.ItemDataRole.UserRole)
        _, runs = self._status_of(path)
        good = [r for r in runs if r["status"] in ("ok", "degraded")]
        if not good:
            return
        target = os.path.commonpath([r["output_dir"] for r in good])
        if len(good) == 1 and not os.path.isdir(path):
            run = good[0]
            stem = Path(run["pdf"]).stem
            for fmt, suffix in RESULT_SUFFIXES:
                candidate = Path(run["output_dir"]) / (stem + suffix)
                if fmt in self.result_formats and candidate.exists():
                    target = str(candidate)
                    break
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    # -- drag & drop onto the whole window -------------------------------- #

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._highlight_list(True)

    def dragLeaveEvent(self, event):
        self._highlight_list(False)

    def dropEvent(self, event):
        self._highlight_list(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def _highlight_list(self, active: bool) -> None:
        self.file_list.setProperty("dragActive", active)
        self.file_list.style().unpolish(self.file_list)
        self.file_list.style().polish(self.file_list)

    def _toggle_advanced(self, shown: bool) -> None:
        self.advanced.setVisible(shown)
        self.more_btn.setText(tr("advanced.button") + ("  ▴" if shown else "  ▾"))

    def _align_labels(self) -> None:
        """One label width for both grids, so Options and More options line
        up. Measured with the styled font - the stylesheet sets the size, so
        this has to come after the theme is applied."""
        widths = []
        for label in self._form_labels:
            label.ensurePolished()
            widths.append(label.fontMetrics().horizontalAdvance(label.text()))
        width = min(LABEL_MAX_WIDTH, max(widths) + 2)
        for label in self._form_labels:
            label.setFixedWidth(width)
        # The right-hand checkboxes start where the right-hand labels do:
        # label, box, (?), the gap, and the spacing between those four.
        half = width + FIELD_WIDTH + HelpMark.SIZE + GAP_WIDTH + 4 * GRID_SPACING
        for grid in self._check_grids:
            grid.setColumnMinimumWidth(0, half)

    def _sync_format_options(self, *_) -> None:
        """Grey out what the ticked formats would ignore."""
        ticked = {key for key, box in self.format_boxes.items() if box.isChecked()}
        self.opt_page_sep.setEnabled(bool(ticked & {"markdown", "text", "html"}))
        self.opt_plain_md.setEnabled("markdown" in ticked)
        self.opt_clean_figures.setEnabled("markdown" in ticked)

    def _toggle_image_dpi(self) -> None:
        self.image_dpi.setEnabled(self.image_mode.currentData() != "off")

    def _toggle_hybrid(self) -> None:
        self.hybrid_url.setEnabled(bool(self.hybrid.currentData()))

    def _toggle_log(self, shown: bool) -> None:
        if self.act_show_log.isChecked() != shown:
            self.act_show_log.setChecked(shown)  # same, from the menu side
            return
        if self.log_card.isHidden() != shown:
            return  # already in that state; resizing again would grow the window

        self.log_card.setVisible(shown)
        # Grow the window to make room rather than pushing the cards into the
        # scroll area, and give the space back when the log is folded away.
        delta = self.log_card.sizeHint().height() + self.body_layout.spacing()
        available = self.screen().availableGeometry().height()
        if shown:
            self.resize(self.width(), min(self.height() + delta, available))
            # Bring it into view: it sits at the bottom of the scroll area.
            QTimer.singleShot(0, self, lambda: self.scroll.ensureWidgetVisible(self.log_card))
        else:
            self.resize(self.width(), max(self.height() - delta, self.minimumHeight()))

    # -- run --------------------------------------------------------------- #

    def _collect_options(self) -> dict | None:
        formats = [key for key, box in self.format_boxes.items() if box.isChecked()]
        if not formats:
            QMessageBox.warning(self, APP_NAME, tr("error.no_format"))
            return None

        options: dict = {
            "format": ",".join(formats),
            # Off for speed unless hidden text is to go: then every filter
            # stays on, and the worker adds the hidden-text one on top.
            "content_safety_off": None if self.opt_hidden.isChecked() else CONTENT_SAFETY_OFF,
            # TI and friends ship subset fonts without ToUnicode; the default
            # replacement is a space, so those glyphs vanish silently.
            "replace_invalid_chars": "?",
            "image_output": self.image_mode.currentData(),
            "use_struct_tree": self.opt_struct.isChecked(),
            "sanitize": self.opt_sanitize.isChecked(),
            "keep_line_breaks": self.opt_line_breaks.isChecked(),
            "include_header_footer": self.opt_header_footer.isChecked(),
            "detect_strikethrough": self.opt_strikethrough.isChecked(),
            "reading_order": self.reading_order.currentData(),
            "table_method": self.table_method.currentData(),
            "image_format": self.image_format.currentData(),
        }

        separators = self.opt_page_sep.isChecked()
        if "markdown" in formats:
            # Always HTML from the converter: the clean-ups read HTML tables
            # (merged cells, MIN/TYP/MAX). Plain tables are made from them at
            # the end (ConvertWorker plain_tables).
            options["markdown_with_html"] = True
            if separators:
                options["markdown_page_separator"] = PAGE_SEPARATOR
        if "text" in formats and separators:
            options["text_page_separator"] = PAGE_SEPARATOR
        if "html" in formats and separators:
            options["html_page_separator"] = PAGE_SEPARATOR

        dpi = int(self.image_dpi.currentData())
        if dpi != IMAGE_DPI_DEFAULT:
            options["image_resolution"] = str(dpi)
        if round(self.space_ratio.value(), 2) != SPACE_RATIO_DEFAULT:
            options["space_ratio"] = f"{self.space_ratio.value():.2f}"

        pages = self.pages_edit.text().strip()
        if pages:
            options["pages"] = pages
        if self.threads_spin.value() > 1:
            options["threads"] = str(self.threads_spin.value())
        if self.password_edit.text():
            options["password"] = self.password_edit.text()

        hybrid = self.hybrid.currentData() if AI_MODE else None
        if hybrid:
            options["hybrid"] = hybrid
            # Never again without these two: no timeout means an unanswered
            # request waits forever, no fallback means no way back to Java.
            options["hybrid_timeout"] = HYBRID_TIMEOUT_MS
            options["hybrid_fallback"] = True
            url = self.hybrid_url.text().strip()
            if url:
                options["hybrid_url"] = url
        return options

    def _confirm_hybrid(self, options: dict) -> bool:
        """Check the hybrid server before handing it the whole batch."""
        if not options.get("hybrid"):
            return True
        url = options.get("hybrid_url") or HYBRID_DEFAULT_URL
        if server_reachable(url):
            return True
        answer = QMessageBox.question(
            self,
            APP_NAME,
            tr("hybrid.unreachable", url=url),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        for key in ("hybrid", "hybrid_timeout", "hybrid_fallback", "hybrid_url"):
            options.pop(key, None)
        return True

    def _confirm_unsupported(self, options: dict, supported: set[str] | None) -> bool:
        """Say out loud which options the installed version will ignore."""
        if supported is None:
            return True
        dropped = sorted(k for k in options if k not in supported)
        if not dropped:
            return True
        answer = QMessageBox.question(
            self,
            APP_NAME,
            tr("unsupported.message", options="\n    ".join(dropped)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _resolve_output_dir(self) -> str:
        chosen = self.output_edit.text().strip()
        if chosen:
            return chosen
        first = Path(self.inputs[0])
        base = first if first.is_dir() else first.parent
        return str(base / "output")

    def start(self) -> None:
        options = self._collect_options()
        if options is None:
            return

        output_dir = self._resolve_output_dir()
        jobs = plan_jobs(self.inputs, output_dir)
        if not jobs:
            QMessageBox.warning(self, APP_NAME, tr("error.no_pdfs"))
            return

        supported = supported_option_names()
        if not self._confirm_unsupported(options, supported):
            return
        if not self._confirm_hybrid(options):
            return

        # A path typed and never tabbed away from would otherwise be forgotten;
        # the options are saved here too, as the set the user actually ran with.
        self._remember_output_dir()
        self._save_settings()
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, tr("error.mkdir", error=exc))
            return

        self.output_dir = output_dir
        self.results = {}
        self.result_formats = options["format"].split(",")
        self._apply_statuses()
        self.log_view.clear()
        if options.get("hybrid"):
            self.append_log(
                tr(
                    "hybrid.log",
                    mode=options["hybrid"],
                    url=options.get("hybrid_url") or HYBRID_DEFAULT_URL,
                )
            )
        self._set_busy(True, len(jobs))
        self.elapsed.start()
        self.tick.start()

        self.thread = QThread(self)
        self._sync_actions()
        self.worker = ConvertWorker(
            jobs, output_dir, options, int(self.timeout_box.currentData()), supported,
            filter_hidden=self.opt_hidden.isChecked(),
            clean_figures=self.opt_clean_figures.isChecked(),
            plain_tables=self.opt_plain_md.isChecked(),
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.thread.start()

    def cancel(self) -> None:
        if self.worker is not None:
            self.cancel_btn.setEnabled(False)
            self.tick.stop()  # otherwise the next tick overwrites the message
            self._set_status(tr("status.stopping"))
            self.worker.cancel()

    def on_progress(self, index: int, total: int, name: str) -> None:
        self.current_file = name
        self.progress.setValue(index - 1)
        self._update_elapsed()

    def on_finished(self, summary: dict) -> None:
        self.tick.stop()
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
        self.thread = None
        self.worker = None
        self._set_busy(False)

        seconds = self.elapsed.elapsed() / 1000
        ok, total = summary["ok"], summary["total"]
        self.results = {
            os.path.normcase(os.path.abspath(r["pdf"])): r
            for r in summary.get("results", [])
        }
        self._apply_statuses()

        problems = bool(
            summary["error"] or summary["degraded"] or summary["timed_out"]
            or summary["failed"] or summary.get("no_text")
            or (ok == 0 and not summary["cancelled"])
        )
        self.details_link.setVisible(problems)

        if summary["error"]:
            self._set_status(tr("summary.failed_status"), state="error")
            self.append_log(summary["error"])
            self._toggle_log(True)  # the details are the point when it breaks
            QMessageBox.critical(self, APP_NAME, summary["error"])
            return

        notes = []
        if summary["degraded"]:
            notes.append(tr("summary.degraded", files=", ".join(summary["degraded"])))
        if summary["timed_out"]:
            notes.append(tr("summary.timed_out", files=", ".join(summary["timed_out"])))
        if summary["failed"]:
            notes.append(tr("summary.failed", files=", ".join(summary["failed"])))
        if summary.get("no_text"):
            notes.append(tr("summary.no_text", files=", ".join(summary["no_text"])))

        elapsed = f"{seconds:.1f}"
        if summary["cancelled"]:
            headline = tr("summary.cancelled", ok=ok, total=total)
            state = "error" if ok == 0 else ""
        elif ok == total and not notes:
            headline = tr("summary.done", seconds=elapsed)
            state = "ok"
        elif ok:
            headline = tr("summary.partial", ok=ok, total=total, seconds=elapsed)
            state = "ok"
        else:
            headline = tr("summary.none", total=total)
            state = "error"

        self._set_status(headline, state=state)
        self.append_log(headline + ("; " + "; ".join(notes) if notes else ""))
        if ok:
            self.open_btn.setVisible(True)
        if notes:
            QMessageBox.information(
                self, APP_NAME, headline + ":\n\n" + "\n".join(notes)
            )
        elif ok == 0 and not summary["cancelled"]:
            self._toggle_log(True)
            QMessageBox.critical(self, APP_NAME, headline)

    def open_output(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.output_dir))

    # -- small helpers ------------------------------------------------------ #

    def _set_busy(self, busy: bool, total: int = 0) -> None:
        self.progress.setVisible(busy)
        self.progress.setRange(0, total if busy else 1)
        self.progress.setValue(0)
        self.run_btn.setEnabled(not busy)
        self.run_btn.setText(tr("action.converting") if busy else tr("action.convert"))
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        if busy:
            self.open_btn.setVisible(False)
            self.details_link.setVisible(False)
            self.current_file = ""
            self._set_status(tr("status.working"))
        self._sync_actions()

    def _set_status(self, text: str, state: str = "") -> None:
        self.status.setText(text)
        self.status.setProperty("state", state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _update_elapsed(self) -> None:
        seconds = self.elapsed.elapsed() / 1000
        total = self.progress.maximum()
        done = self.progress.value()
        prefix = (
            tr("status.file_of", index=done + 1, total=total)
            if total > 1
            else tr("status.working").rstrip("…").rstrip(".")
        )
        name = f" · {self.current_file}" if self.current_file else ""
        self._set_status(
            tr("status.progress", prefix=prefix, name=name, seconds=f"{seconds:.0f}")
        )

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def _check_java(self) -> None:
        """opendataloader-pdf needs a JVM; say so early and in plain words."""
        self.append_log(f"{APP_NAME} {__version__} · opendataloader-pdf {odl_version()}")
        java = java_executable()
        if java:
            self.append_log(tr("java.found", path=java))
            # The path alone does not tell which runtime actually answers; when
            # something goes wrong the version is the first thing worth knowing.
            try:
                result = subprocess.run(
                    [java, "-version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                    creationflags=_no_window(),
                )
                line = (result.stderr or result.stdout or "").strip().splitlines()
                if line:
                    self.append_log(tr("java.version", version=line[0].strip()))
            except (OSError, subprocess.SubprocessError):
                pass
            return
        self.append_log(tr("java.missing_log"))
        QMessageBox.warning(
            self,
            APP_NAME,
            tr("java.missing"),
        )

    def closeEvent(self, event):
        if self.thread is not None and self.thread.isRunning():
            answer = QMessageBox.question(
                self, APP_NAME, tr("close.running")
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            # Stop the JVM explicitly: it is a child process and would keep
            # running - and keep writing files - after the window is gone.
            if self.worker is not None:
                self.worker.cancel()
            self.thread.quit()
            self.thread.wait(15000)
        if not self.skip_save:
            self._save_settings()
        event.accept()


def _set_color_scheme(mode: str) -> None:
    """Tell Qt which theme the program shows.

    Qt colours the title bars - of the window and of every dialog - and its
    own palette by this. Left alone it follows Windows, so a light program on
    a dark Windows got a black title bar, and the other way round. "system"
    hands the choice back to Windows. Qt < 6.8 cannot be told.
    """
    hints = QGuiApplication.styleHints()
    if not hasattr(hints, "setColorScheme"):
        return
    if mode == "system":
        hints.unsetColorScheme()
    else:
        hints.setColorScheme(
            Qt.ColorScheme.Dark if mode == "dark" else Qt.ColorScheme.Light
        )


def _system_prefers_dark() -> bool:
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is None:  # Qt < 6.5
        return True
    return scheme() == Qt.ColorScheme.Dark


# -- update check ------------------------------------------------------------- #

# A newer release found this session; a window rebuilt for another language
# shows it again.
_latest_release: dict | None = None
# Checks still waiting for GitHub; held so they are not collected meanwhile.
_update_checks: list[UpdateCheck] = []


def auto_update_check_enabled() -> bool:
    return str(app_settings().value("check_updates", True)).lower() in ("true", "1")


def check_for_updates(manual: bool = False) -> None:
    """Ask GitHub for the latest release, in the background.

    At startup only a newer release is worth a word - the link in the header.
    From the Help menu every outcome gets an answer.
    """
    check = UpdateCheck()
    _update_checks.append(check)
    check.finished.connect(lambda release: _update_checked(check, release, manual))
    check.start()


def _update_checked(check: UpdateCheck, release: dict | None, manual: bool) -> None:
    global _latest_release
    if check in _update_checks:
        _update_checks.remove(check)
    check.deleteLater()
    window = _windows[-1] if _windows else None
    if window is None:
        return
    newer = release is not None and is_newer(release["version"])
    if newer:
        _latest_release = release
        window.show_update(release)
    if not manual:
        return
    if release is None:
        QMessageBox.warning(window, APP_NAME, tr("update.failed"))
    elif newer:
        offer_update(release, ask=True)
    else:
        QMessageBox.information(window, APP_NAME, tr("update.latest", version=__version__))


def offer_update(release: dict | None, ask: bool = False) -> None:
    """The user wants the newer release: install it, or open its page.

    An installed copy downloads the installer and lets it replace the
    program - after a yes, since the program closes for it. Any other copy
    opens the download page: straight away from the link in the header,
    after a question from Help -> Check for updates (`ask`).
    """
    window = _windows[-1] if _windows else None
    if window is None or release is None:
        return
    if not updates.can_install(release):
        if ask:
            answer = QMessageBox.question(
                window,
                APP_NAME,
                tr("update.ask", version=release["version"], current=__version__),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        QDesktopServices.openUrl(QUrl(release["url"]))
        return
    if _downloads:
        return  # already on its way
    answer = QMessageBox.question(
        window,
        APP_NAME,
        tr("update.ask_install", version=release["version"], current=__version__),
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    if window._busy():
        # The installer replaces the Java runtime the conversion is using.
        QMessageBox.information(window, APP_NAME, tr("update.busy"))
        return
    _download_update(window, release)


# A download in progress, with its progress window; held so neither is
# collected meanwhile.
_downloads: list = []


def _download_update(window: MainWindow, release: dict) -> None:
    download = updates.UpdateDownload(release)
    label = tr("update.downloading", version=release["version"])
    dialog = QProgressDialog(label, tr("action.cancel"), 0, 100, window)
    # The theme's progress bar is a thin 6 px strip with no room for text, so
    # the percentage goes into the label instead, and the window gets a width
    # that fits the label in any language.
    bar = QProgressBar(dialog)
    bar.setTextVisible(False)
    dialog.setBar(bar)
    dialog.setMinimumWidth(420)
    dialog.setWindowTitle(APP_NAME)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setValue(0)
    dialog.canceled.connect(download.cancel)
    def show_progress(done: int, total: int) -> None:
        percent = min(100, done * 100 // max(total, 1))
        dialog.setValue(percent)
        dialog.setLabelText(f"{label}  {percent}%")

    download.progress.connect(show_progress)
    download.finished.connect(
        lambda path, error: _update_downloaded(download, dialog, release, path, error)
    )
    _downloads.append((download, dialog))
    dialog.show()
    download.start()


def _update_downloaded(download, dialog, release: dict, path, error: str) -> None:
    if (download, dialog) in _downloads:
        _downloads.remove((download, dialog))
    if shiboken6.isValid(dialog):
        dialog.canceled.disconnect()  # closing it is not a cancel
        dialog.close()
        dialog.deleteLater()
    download.deleteLater()
    window = _windows[-1] if _windows else None
    if path is None and not error:
        return  # cancelled
    if path is not None:
        try:
            updates.start_installer(path)
        except OSError:
            pass  # on to the page
        else:
            _quit_for_update()
            return
    # Whatever went wrong, the page has the installer to download by hand.
    if window is not None:
        QMessageBox.warning(window, APP_NAME, tr("update.install_failed"))
    QDesktopServices.openUrl(QUrl(release["url"]))


def _quit_for_update() -> None:
    """Close so the installer can replace the program's files."""
    for window in list(_windows):
        window.close()
    QApplication.quit()


# The window on screen. Switching the language replaces it, and a reference has
# to live somewhere or the new one would be garbage-collected on the spot.
_windows: list[MainWindow] = []


def open_window() -> MainWindow:
    window = MainWindow()
    window.languageChanged.connect(lambda code: switch_language(window, code))
    window.resetRequested.connect(lambda: reset_settings(window))
    _windows.append(window)
    return window


def _replace_window(old: MainWindow) -> MainWindow:
    new = open_window()
    new.results = dict(old.results)
    new.result_formats = list(old.result_formats)
    new.output_dir = old.output_dir
    new.add_paths(list(old.inputs))
    new._sync_actions()
    new.setGeometry(old.geometry())
    if old.isVisible():
        new.show()
    old.close()
    if old in _windows:
        _windows.remove(old)
    old.deleteLater()
    return new


def switch_language(old: MainWindow, code: str) -> MainWindow:
    """Rebuild the window in another language, keeping what the user set up.

    Every label is set once while the window is built, so the honest way to
    change all of them is to build it again: options and the destination are
    saved settings already, the file list and the geometry are carried over.
    """
    old._save_settings()
    app_settings().setValue("language", code)
    i18n.set_language(code)
    return _replace_window(old)


def reset_settings(old: MainWindow) -> MainWindow:
    """Settings → Reset: forget every option, folder, theme and language.

    The file list is the user's work rather than a setting, so it stays.
    """
    app_settings().clear()
    old.skip_save = True
    i18n.set_language(i18n.system_language())
    new = _replace_window(old)
    new._apply_theme()
    return new


def bring_forward(paths: list[str]) -> None:
    """Another launch handed its files over: add them and show the window."""
    if not _windows:
        return
    window = _windows[-1]
    window.add_paths(paths)
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()


def main() -> int:
    use_bundled_jre()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(str(resource("icon.ico"))))
    # app.arguments() rather than sys.argv: Qt has already taken its own
    # switches out of it.
    paths = startup_paths(app.arguments()[1:])
    instance = SingleInstance()
    if not instance.start(paths):
        return 0  # handed over to the window that is already open
    instance.received.connect(bring_forward)
    i18n.set_language(app_settings().value("language") or i18n.system_language())
    window = open_window()
    window.add_paths(paths)
    window.show()
    if auto_update_check_enabled():
        check_for_updates()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

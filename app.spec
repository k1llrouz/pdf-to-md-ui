# PyInstaller spec for the PDF to Markdown converter.
#
# Build with:   pyinstaller app.spec
#
# Notes:
#  * opendataloader_pdf ships a Java jar inside its wheel; collect_all pulls
#    the jar in as a data file, which a plain module scan would miss.
#  * onedir, not onefile: onefile unpacks the whole bundle to a temp folder on
#    every launch, which adds seconds of startup for no benefit here.

import os

from PyInstaller.utils.hooks import collect_all

odl_datas, odl_binaries, odl_hidden = collect_all("opendataloader_pdf")

# icon.ico is drawn by tools/make_icon.py. Kept optional all the same: without
# the file PyInstaller would abort.
app_icon = "icon.ico" if os.path.exists("icon.ico") else None

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=odl_binaries,
    # patches/ and locales/ go next to converter.py and i18n.py inside the
    # bundle, where HIDDEN_PATCH_ROOT and LOCALE_DIR look for them.
    datas=odl_datas
    + [
        ("patches", "patches"),
        ("locales", "locales"),
        # The licence and the notices travel with every copy handed out.
        ("LICENSE", "."),
        ("THIRD_PARTY_NOTICES.md", "."),
        # The exe carries the icon in its resources; the window loads it
        # from here (gui.resource).
        ("icon.ico", "."),
        # Help → User guide (F1) reads these; it has to work offline.
        ("docs/guide.en.md", "docs"),
        ("docs/guide.ru.md", "docs"),
    ],
    hiddenimports=odl_hidden,
    hookspath=[],
    runtime_hooks=[],
    # Qt modules the app never touches; excluding them saves ~60 MB.
    excludes=[
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtNetworkAuth",
        "PySide6.QtPdf",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDF to Markdown",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # no console window for non-technical users
    icon=app_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PDF to Markdown",
)

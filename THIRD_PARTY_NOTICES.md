# Third-party components

PDF → Markdown is licensed under the GNU General Public License v3.0 (see
[LICENSE](LICENSE)). The Windows build bundles the components below, each
under its own licence. All of them are compatible with distributing the
program under the GPL v3.

| Component | Used for | Licence | Source |
|---|---|---|---|
| [opendataloader-pdf](https://github.com/opendataloader-project/opendataloader-pdf) | The PDF converter itself (Python package and CLI jar) | Apache-2.0, © 2025–2026 Hancom, Inc. | [GitHub](https://github.com/opendataloader-project/opendataloader-pdf) |
| Java libraries inside the opendataloader-pdf jar (PDFBox, Jackson, veraPDF and others) | PDF parsing | Various; listed in `opendataloader_pdf/THIRD_PARTY/` inside the installed program | See that folder |
| [opendataloader-pdf](https://github.com/opendataloader-project/opendataloader-pdf) `FilterConfig.class` in `patches/hidden-text/`, **modified** (one line, marked in the source) | Lets "Drop hidden text" switch on the hidden-text filter, see [patches/README.md](patches/README.md) | Apache-2.0, © 2025–2026 Hancom, Inc. | [patches/hidden-text-src/FilterConfig.java](patches/hidden-text-src/FilterConfig.java) |
| [Qt for Python (PySide6)](https://www.qt.io/qt-for-python) and Qt 6 | The window | LGPL-3.0 (also offered under GPL-2.0 / GPL-3.0), © The Qt Company Ltd. | [code.qt.io](https://code.qt.io/cgit/pyside/pyside-setup.git/) |
| [pikepdf](https://github.com/pikepdf/pikepdf) and [qpdf](https://github.com/qpdf/qpdf) | Copies of PDFs with readable fonts (fontfix.py) | MPL-2.0 (pikepdf), Apache-2.0 (qpdf) | [pikepdf on GitHub](https://github.com/pikepdf/pikepdf), [qpdf on GitHub](https://github.com/qpdf/qpdf) |
| [fontTools](https://github.com/fonttools/fonttools) | Glyph names to characters (fontfix.py) | MIT | [GitHub](https://github.com/fonttools/fonttools) |
| [Python](https://www.python.org) | Runs the program | PSF License Agreement | [python.org](https://www.python.org/downloads/source/) |
| [OpenSSL](https://www.openssl.org) 3 (comes with Python) | HTTPS for the update check | Apache-2.0, © The OpenSSL Project Authors | [GitHub](https://github.com/openssl/openssl) |
| [PyInstaller](https://pyinstaller.org) bootloader | Starts the bundled Python | GPL-2.0-or-later with an exception that allows distributing programs built with it under any licence | [GitHub](https://github.com/pyinstaller/pyinstaller) |
| [Eclipse Temurin](https://adoptium.net) (OpenJDK) runtime, in `jre/` | Runs the opendataloader-pdf jar | GPL-2.0 with the Classpath Exception; notices in `jre/legal/` | [adoptium.net](https://adoptium.net) |

The Qt and Java runtimes are separate files in the program folder and can be
replaced with other builds of the same version.

Releases are built by GitHub Actions with the Temurin runtime. A local build
made with `build.bat` bundles whatever JDK is installed on that machine; an
Oracle JDK may only be redistributed unmodified, which a jlink-trimmed runtime
is not — use Temurin for anything you hand out.

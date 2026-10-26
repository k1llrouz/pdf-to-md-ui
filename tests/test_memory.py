"""Plot pages in a small heap, and the hidden-text switch (patches/hidden-text).

The fixture is a page of 3000 separate strokes, built on the fly by
tests/pdfgen.py - the shape of a datasheet's plot pages. opendataloader-pdf
2.5.9 to 2.5.13 needed memory quadratic in the number of strokes for it (well
over 256 MB; a 48-page TI datasheet needed 14 GB), which the app worked around
with a veraPDF class of its own. 2.5.15 fixed it upstream; the tests run the
page in 128 MB so that a release bringing it back fails here.
"""

import os
import subprocess
import zipfile

import pytest

import converter
from converter import ConvertWorker, Job
from tests.pdfgen import dense_line_pdf

HEAP_MB = 128
STROKES = 3000

needs_java = pytest.mark.skipif(
    converter.java_executable() is None or converter.jar_path() is None,
    reason="needs Java and the opendataloader-pdf JAR",
)


@pytest.fixture
def dense_pdf(tmp_path):
    return dense_line_pdf(tmp_path / "plots.pdf", STROKES)


# -- plot pages ---------------------------------------------------------------- #


@needs_java
def test_plot_pages_convert_in_a_small_heap(qapp, dense_pdf, tmp_path, monkeypatch):
    monkeypatch.delenv("JAVA_TOOL_OPTIONS", raising=False)
    monkeypatch.setattr(converter, "max_heap_mb", lambda: HEAP_MB)
    out = tmp_path / "out"
    worker = ConvertWorker(
        [Job(str(dense_pdf), str(out), "plots.pdf")],
        str(out), {"format": "markdown", "image_output": "off"}, 120,
        converter.supported_option_names(),
    )
    result, log = {}, []
    worker.finished.connect(result.update)
    worker.log.connect(log.append)

    worker.run()

    text = "\n".join(log)
    assert "OutOfMemoryError" not in text
    assert result["ok"] == 1, text[-2000:]
    assert "Typical Characteristics" in (out / "plots.md").read_text(encoding="utf-8")


@needs_java
def test_pinned_jar_handles_dense_line_art(dense_pdf, tmp_path):
    """The bare jar, nothing in front of it on the classpath: veraPDF's line-art
    memory regression (veraPDF-library#1641) must stay fixed."""
    out = tmp_path / "out"
    out.mkdir()
    result = subprocess.run(
        [
            converter.java_executable(),
            f"-Xmx{HEAP_MB}m",
            "-Djava.awt.headless=true",
            "-jar", converter.jar_path(),
            str(dense_pdf), "--output-dir", str(out),
            "--format", "markdown", "--image-output", "off",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )

    output = result.stdout + result.stderr
    assert "OutOfMemoryError" not in output, (
        "opendataloader-pdf needs far more memory for dense line art again - "
        "see patches/README.md"
    )
    assert result.returncode == 0, output[-2000:]
    assert (out / "plots.md").is_file()


def test_plain_command_runs_the_jar_as_is():
    jar = converter.jar_path()

    command = converter.java_command("java", jar, ["in.pdf"])

    assert command[-3:] == ["-jar", jar, "in.pdf"]
    assert "-cp" not in command


# -- the hidden-text switch (patches/hidden-text) ------------------------------------------------- #


def test_hidden_text_patch_fits_the_pinned_jar():
    assert converter.hidden_text_patch(converter.jar_path()), (
        "patches/hidden-text no longer matches the bundled FilterConfig - "
        "see patches/README.md"
    )


def test_hidden_text_switch_only_when_asked():
    jar = converter.jar_path()

    plain = converter.java_command("java", jar, ["in.pdf"])
    asked = converter.java_command("java", jar, ["in.pdf"], filter_hidden=True)

    assert converter.HIDDEN_PROPERTY not in plain
    assert str(converter.HIDDEN_PATCH_ROOT) not in " ".join(plain)
    assert converter.HIDDEN_PROPERTY in asked
    classpath = asked[asked.index("-cp") + 1].split(os.pathsep)
    assert classpath[0] == str(converter.HIDDEN_PATCH_ROOT) and classpath[-1] == jar


def test_hidden_text_switch_skips_a_jar_it_does_not_fit(tmp_path):
    jar = tmp_path / "other.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Main-Class: x.Main\n")
        z.writestr(converter.HIDDEN_PATCHED_CLASS, b"another release")

    command = converter.java_command("java", str(jar), ["in.pdf"], filter_hidden=True)

    assert converter.HIDDEN_PROPERTY not in command
    assert command[-3:-1] == ["-jar", str(jar)]


def test_hidden_text_switch_skips_an_unreadable_jar(tmp_path):
    broken = tmp_path / "broken.jar"
    broken.write_bytes(b"not a zip")

    assert not converter.hidden_text_patch(str(broken))
    assert not converter.hidden_text_patch(str(tmp_path / "missing.jar"))
    command = converter.java_command("java", str(broken), ["in.pdf"], filter_hidden=True)
    assert command[-3:] == ["-jar", str(broken), "in.pdf"]

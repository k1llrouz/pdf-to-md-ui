# Patches

One single class, put in front of the opendataloader-pdf jar on the classpath
so it wins over the copy inside; the jar itself is not modified. It applies
only to the exact class it was made for (a SHA-256 check in `converter.py`), so
a different opendataloader-pdf runs untouched.

Up to version 2.2.0 of this program there was a second one, a veraPDF
`ChunkParser` working around memory that grew with the square of the number of
strokes on a page (opendataloader-pdf 2.5.9 to 2.5.13: a 48-page TI datasheet
needed 14 GB). Reported as
[veraPDF/veraPDF-library#1641](https://github.com/veraPDF/veraPDF-library/issues/1641)
and [opendataloader-pdf#790](https://github.com/opendataloader-project/opendataloader-pdf/issues/790),
fixed upstream; opendataloader-pdf 2.5.15 converts the same datasheets in 1 GB
on its own, so the class is gone.

# Upgrading opendataloader-pdf

`requirements.txt` pins the exact version. After raising it, run the tests:

- `test_hidden_text_patch_fits_the_pinned_jar` fails when the new jar's
  `FilterConfig` differs — see [below](#upgrading-the-switch).
- `test_pinned_jar_handles_dense_line_art` (`tests/test_memory.py`) runs the
  bare jar on a page of 3000 strokes in 128 MB. If it fails, the line-art
  memory problem is back: report it upstream and stay on the old version.
- `tests/test_real_converter.py` converts a table fixture and the hidden-text
  page end to end.

Then convert a set of real datasheets with the old and the new version and
compare the Markdown: size, words, and every file whose words change by more
than about half a percent.

# Hidden-text switch

`hidden-text/org/opendataloader/pdf/api/FilterConfig.class` is opendataloader-pdf
2.5.15's `FilterConfig` with one line changed; the source, with the change
marked, is [`hidden-text-src/FilterConfig.java`](hidden-text-src/FilterConfig.java).

## Why

opendataloader-pdf can drop text a reader cannot see: it renders each page and
compares every piece of text with what is behind it, and text with almost no
contrast — invisible, or white on white — goes. That is the classic way of
smuggling instructions to an AI inside a document. But 2.5.15 leaves this
filter off (`filterHiddenText = false`), and its command line can only switch
filters off (`--content-safety-off hidden-text`), never on. The tiny-text,
off-page and hidden-layer filters are on by default and the CLI handles them;
this one cannot be reached at all.

The patched class takes the default from a system property instead:

```java
private boolean filterHiddenText = Boolean.getBoolean("opendataloader.filterHiddenText");
```

`converter.java_command` adds `-Dopendataloader.filterHiddenText=true` and the
patch folder only when **More options → Drop hidden text** is ticked; without
the property the patched class behaves exactly like the original.

Compiled with `javac --release 11` against the 2.5.15 jar. The unmodified
v2.5.15 source compiled the same way gives bytecode identical to the class in
the jar (`javap -c` compared), so the patch differs from the shipped class in
that one field initialiser and nothing else.

## What it does to the output

On a generated page (`tests/pdfgen.py: hidden_text_pdf`) with a visible line
and four hidden ones:

| | invisible | white on white | half a point | off the page |
|---|---|---|---|---|
| filters off (the app's default) | kept | kept | kept | kept |
| all CLI filters on | kept | kept | dropped | dropped |
| all CLI filters + this switch | dropped | dropped | dropped | dropped |

The visible line stays in every case. `tests/test_real_converter.py` checks
this on every run.

On real datasheets (measured on 2.5.13) it costs about twice the time — 1 to 3 s more per file
(tps7a02 3.1 → 5.2 s, stm32u083kc 2.4 → 5.1 s, lm2917-n 1.3 → 3.1 s, MCP1700
1.1 → 2.1 s) — and the contrast test has false positives: from the Microchip
MCP1700 datasheet it dropped the minus and equals signs in two formulas, from
TI tps7a02 a "Width (mm)" label. It also, correctly, dropped invisible "SPACER"
text from TI lm2917-n. Hence off by default, with the trade-off spelled out
under the setting's (?).

## When it applies

Only when the jar's `FilterConfig` has the SHA-256 in
`converter.HIDDEN_PATCH_TARGETS` (the 2.5.15 one). With any other jar "Drop
hidden text" still switches every CLI filter on, and the log says that
invisible and same-colour text stays.

## Upgrading the switch

`test_hidden_text_patch_fits_the_pinned_jar` fails when the new jar's
`FilterConfig` differs. Then: if upstream has added a way to switch the filter
on, use that and delete `hidden-text/` and `hidden-text-src/`; otherwise take
`FilterConfig.java` from the new release tag, make the same one-line change,
compile it as above, check that the unmodified source compiles to the
shipped bytecode (`javap -c -p`), and replace the hash.

## Licence (opendataloader-pdf)

`FilterConfig` is part of opendataloader-pdf, © 2025–2026 Hancom Inc., under
the Apache License 2.0. The modified source is in `hidden-text-src/`, with a
notice of the change at the top as the licence asks.

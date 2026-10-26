# Changes

## 2.2.3

- **Updates no longer leave 90 MB behind.** Each update left its downloaded
  installer in the Windows temporary folder. The program now removes it the
  next time it starts; the installer's log stays for a week, in case an
  update went wrong.

## 2.2.2

Fixes for updating from inside the program.

- **About showed the old opendataloader-pdf version after an update.** The
  installer only added and overwrote files, so the old package's version
  file stayed next to the new one (and the old veraPDF patch folder with
  it). It now clears the program's own files before installing; settings
  are kept. Updating 2.2.1 to 2.2.2 this way clears the leftovers, so
  About shows 2.5.15.
- **The update download window** was too narrow and cut the percentage off.
  It is wider now, with the percentage next to the text.

## 2.2.1

The converter engine updated; results are the same, the program is simpler.

- **opendataloader-pdf 2.5.15** (was 2.5.13). The memory problem with
  plot-heavy datasheets that the program worked around with its own veraPDF
  class is fixed upstream (reported as veraPDF-library#1641), so the
  workaround is gone; the datasheets still convert in seconds in 1 GB. On 57
  test datasheets the Markdown is byte-for-byte the same as before. The log no
  longer has the "veraPDF patch" line.
- **Drop hidden text** rebuilt for 2.5.15 and works as before.

## 2.2.0

The program now updates itself in one click.

- **The program updates itself.** Clicking "↑ Version X is available" (or
  Yes in Help → Check for updates) in the installed program downloads the
  new installer with a progress bar and a Cancel button, checks its size
  and checksum against GitHub's, closes the program, installs over it
  without asking anything and opens it again. The install folder, language,
  shortcuts and context menu entry stay as they were. If the download or
  the installer fails, the release page opens as before. A copy run from
  source or from a build folder still just opens the page. It works in any
  install folder - the one the previous version was installed in is kept -
  and needs no administrator rights. **If you have 2.1.0 or earlier, install
  the next version by hand once** (the old program only opens the download
  page); after that, every update is one click.
- The installer names its AppId explicitly (the same value it had
  implicitly), so a new version always finds the installed one.

## 2.1.0

Datasheets come out shorter and cleaner for a language model: plain
Markdown tables by default, figure clutter replaced with a short note,
fewer unreadable characters and no lost pages.

- **MIN / TYP / MAX split more often.** Two numbers close together ("3 7"
  under MIN TYP MAX in LM358) were kept in one cell; a footnote mark written
  apart ("TYP (2)") added an empty column; "GND – 0.3" and "(V+) – 1.5" were
  cut at the minus. All three now land in their columns.
- **More unreadable fonts fixed.** TI pin drawings and plots set single
  characters in tiny fonts with no character map: "IN1?" for IN1–, "–40?C",
  "10 k?". They are now read from the glyph's own name, from its number in
  the installed Arial or Times, or for Symbol from its exact shape. Question
  marks: LM358 38 → 3, MAX232 44 → 0.
- **More drawing clutter removed.** Schematics, timing diagrams, block
  diagrams and plots that came out as runs of loose labels ("IO21 IO19 IO18
  IO5", tick values) or as one table cell stuffed with them now become
  "[Figure omitted, page N]" as well, with their caption and any real
  sentence kept. Rows of a table set as text, ordering examples and notes stay.
  1.3% of the text over 53 datasheets, up to 12% in single files.
  Drawings stamped with their makers' drawing numbers ("mna554",
  "003aab351", "MSv41659V2", "4214862/A 04/2023") go as well - pinning
  drawings and timing diagrams nested in tables included - keeping every
  caption, note and the dimension table of a package. Memory maps stay.
  Checked against a hand-cleaned 74HC595: 84% of what was cut by hand, and
  nothing that was kept.
- **Plain Markdown tables that keep everything.** With the option on, the
  converter used to write pipe tables itself: MIN, TYP and MAX stayed in one
  column and no figure clutter was removed. Now the HTML tables are cleaned
  first and turned into pipe tables at the end - a quarter shorter than
  HTML, about a third fewer tokens over 57 datasheets, no word lost. Now
  on by default.
- **A hint for the language model** on top of a file with figures left out:
  it may ask the user for a screenshot of the page it needs (a schematic, a
  design equation) instead of filling the gap from memory.
- **The figure note is always English**, "[Figure omitted, page N]",
  whatever the interface language: the datasheets are English, and the same
  PDF now gives the same Markdown for everyone.
- **Scans are named.** A PDF with no text (pictures of pages) used to give an
  empty file without a word; the log and the summary now say it looks like a
  scan.
- **AI mode is hidden** for now: the hybrid backend does not work at the
  moment, so its two settings are not shown and a saved choice is ignored.
- **Even more fonts read.** Figure labels in ST's LIS3DH ("CHARGE AMPLIFIER"
  instead of a row of "?"), Ω and – in TI's DRV8825, θ and ≤ in STM32,
  μ for "m" and Δ for "D" in Symbol fonts (AP2112: "1μF", not "1mF"). A font
  name in a legacy code page no longer stops the font repair for the whole
  file.
- **Pages no longer go missing after an inline picture.** A small picture
  set right inside a page's text could make the converter's PDF parser skip
  the rest of the page - WCH's CH340 datasheet lost 9 pages of 16. Such
  pictures are now moved out first; the page looks the same.
- **`<`, `>` and `&` read plainly.** The converter writes them as `&lt;`,
  `&gt;` and `&amp;`; they are put back wherever no tag or entity could start there
  (BME280 code: `adc_T >> 4`, not `adc_T &gt;&gt; 4`).
- **Drop hidden text** explains that it also takes thin signs (+, −, =) out
  of formulas and code — not for such documents.

## 2.0.0

A ground-up rework since 1.0.0: results you can hand to a person or a
language model, and a program that behaves like a Windows application.

### Better results

- **Figure clutter removed.** Plots, package drawings and flow charts used to
  come out as huge tables of empty cells — up to three quarters of a
  datasheet. Each is now one line, "[Figure omitted, page N]" with its
  caption; real sentences inside (flow chart steps) are kept. Over 23 test
  datasheets from 13 makers: 17% fewer tokens, up to 78% on single files.
- **Unreadable fonts fixed.** Fonts that number their glyphs instead of
  naming them came out as "?": a whole Infineon datasheet, minus signs in TI
  plot labels, µ in onsemi tables. The program now restores them before
  converting (the original PDF is never changed). Question marks over the
  test set: 8970 → 90.
- **MIN / TYP / MAX put back apart.** TI and ADI tables with no lines between
  those columns came out with all three values in one cell. They are now
  split by where each number sits on the page; cells that cannot be read
  reliably are left whole.
- **Images tidied.** Identical image files are merged, decorations repeated on
  every page are left out, and files nothing links to are deleted.
- **Drop hidden text** (More options): for results that go to an AI —
  invisible, white-on-white, microscopic and off-page text is left out.
- **Page separators** and **plain Markdown tables** can be switched.
- Plot-heavy datasheets convert in seconds instead of running out of memory
  (veraPDF memory fix, reported upstream).
- Every file has a timeout; a file that runs over is retried without images;
  **Cancel** really stops; one broken PDF never stops the batch.

### A proper Windows program

- **File / Settings / Help** menu with the usual shortcuts; About dialog.
- **Six languages**: English, Русский, Polski, Français, Español, Italiano.
- Light and dark theme or as in Windows — title bars included.
- **Open in PDF → Markdown** in the Explorer context menu; one window — files
  sent while it is open join it.
- Icon; the window reopens where it was left; a mark next to each file after a
  run, double-click opens the result.
- A **?** next to every setting explains it; **Reset** puts a section back to
  its defaults.
- Remove single files from the list (Delete, button or right-click).
- Up to five **favourite output folders**.
- Resolution and timeout chosen from the usual values; eye button for the PDF
  password.
- **User guide** built in (F1), works offline.
- Tells you when a new version is out (can be turned off).
- Bundled Java 25; installer for Windows 10/11, no admin rights needed.

Licence: GNU GPL v3.


# User guide: PDF → Markdown

The program turns PDFs — datasheets and technical documents especially — into
Markdown, JSON, HTML or plain text. Everything it needs, Java included, comes
with it.

## Quick start

1. Add PDFs or a whole folder.
2. Tick the output formats you want.
3. Press **Convert**.

By default the results go to an `output` folder next to the source files.

## Adding files

- Drag PDFs or a folder into the window.
- Or press **Add files** / **Add folder** (Ctrl+O / Ctrl+Shift+O).
- Or drop PDFs onto the program's icon.
- Or right-click PDFs or a folder in Explorer and pick
  **Open in PDF → Markdown**. On Windows 11 it is under
  **Show more options**.

If the program is already open, new files join the same window.

**Removing a file you don't need:** select it in the list and press
**Remove** or the **Delete** key; Ctrl and Shift select several. The
right-click menu has the same. **Clear** empties the whole list. Nothing is
deleted from the disk.

## Output formats

| Format | What you get |
|---|---|
| Markdown | Text with headings and tables. Tables are written as HTML, so merged cells stay intact. |
| JSON | The document's structure for other programs: blocks, positions, types. |
| HTML | A page for the browser. |
| Text | Just the text, no markup. |
| Tagged PDF | The source PDF with structure tags — for screen readers and other programs. |
| Annotated PDF | The source PDF with everything recognised drawn over it. Shows why a table came out wrong. |

You can tick several formats at once.

## Options

- **Images** — **Separate files**, **In the document** (inside the .md, one
  big file) or **Don't save**. Images are the slowest part of the work.
- **Resolution** — how sharp the images are. 96 DPI is usually enough for
  plots.
- **Pages** — e.g. `1-12` or `1,3,5-7`. Empty means all pages.
- **Timeout per file** — a file that runs over is tried once more without
  images.
- **Page separators** — a `---- page 3 ----` line between the pages; untick
  it for one continuous text.
- **Drop figure clutter** — plots, drawings and flow charts often come out
  as huge tables of empty cells. Each is replaced by a line
  "[Figure omitted, page N]" with its caption; see the figure itself in the
  PDF. The same goes for schematics and timing diagrams that come out as a
  long run of loose labels ("IO21", "GND", "R2 100R", tick values). A line
  on top of the file then tells a language model that figures are left out
  and that it can ask you for a screenshot of the page it needs. On by
  default.
- **Plain Markdown tables** — tables in Markdown's own | pipe | markup
  instead of HTML: about a quarter shorter, a third fewer tokens for a
  language model. All the clean-ups are done first, so MIN, TYP and MAX keep
  their own columns. A merged cell's other places are left empty. On by
  default.

Next to every setting there is a **?** — point at it for a detailed
explanation. **Reset** puts the defaults back: one button for the formats
and options, another inside **More options**.

## More options

Press **More options** for the settings you rarely need: reading order, table
detection, image format, PDF password and others. Usually there is
no need to touch them — each is explained under its **?**.

**Drop hidden text** — for results that go to an AI: drops text you cannot
see on the page (invisible, white on white, microscopic, outside the page).
It takes about twice as long, and thin signs go with it — plus, minus,
equals: "var1 + var2" in a formula comes out as "var1 var2". Do not use it for
documents with formulas or code. It is off by default.

The password for encrypted PDFs is never saved. The eye button in the
password field shows what you typed.

## Where to save

- Empty field — results go to an `output` folder next to the source files.
- **Browse…** — pick another folder. It is remembered for next time.
- The cross in the field — back to "next to the source files".

**Favourite folders** — up to 5 folders to switch between with one click.
Open them with the button under the field, pick a folder with **Browse…** and
press **Add current folder**. The cross on the right removes a folder from
the favourites (the folder itself stays on the disk).

## After converting

- Every file in the list gets a mark: a green tick — done; an amber "!" —
  converted without images (ran out of time); a red cross — it didn't work.
- Double-click a file to open the result.
- **Open results folder** opens the folder in Explorer.
- If something went wrong, a **Details** link appears next to the status line;
  it opens the log. The log is always at **Settings → Show log** (Ctrl+L).

## What the program fixes by itself

- **Unreadable fonts.** In some PDFs (Infineon, TI pin drawings and plot
  labels, Microchip drawings) the font's letters are not named and the text
  came out as "?".
  The program restores them before converting — the original file is not
  changed.
- **Pages lost after a picture.** A small picture set inside a page's text
  could make the converter skip the rest of the page. Such pictures are
  moved out of the text first; the page looks the same.
- **Merged MIN/TYP/MAX.** In TI and ADI tables with no lines between the MIN,
  TYP and MAX columns, the numbers are put back under their words. Where the
  page does not make clear which number belongs where, the cell is left as it
  was, spread over the three columns.
- **Surplus images.** Identical pictures become one file; a picture repeated
  on many pages (a logo, a bit of a frame) is left out of the text; files
  nothing links to are deleted.
- **Escaped signs.** The converter writes `<`, `>` and `&` as `&lt;`, `&gt;`
  and `&amp;`. They are put back wherever they cannot be taken for markup, so
  code reads `x >> 3`, not `x &gt;&gt; 3`.

## Tips for datasheets

- Take only the pages you need — the description and the parameter tables,
  say `1-12` — and skip dozens of pages of plots.
- Too slow? Set **Resolution** to 96 DPI or **Images → Don't save**.
- A table came out broken? Tick **Annotated PDF** to see what the converter
  found, and try **More options → Tables: by clusters**.

## Settings

- **Settings → Language** — the program's language.
- **Settings → Theme** — light, dark, or as in Windows.
- **Settings → Check for updates at startup** — at startup the program asks
  GitHub whether there is a newer version and shows a link in the corner of
  the window. Click it to update: the program downloads the new version,
  closes, installs it and opens again — your settings stay. If something
  goes wrong, the download page opens instead. Version 2.1.0 and earlier
  only open the page: install the next version by hand once, and every
  update after that is one click. To check by hand: **Help → Check for
  updates**.
- **Settings → Reset settings** — everything back as after installing.

## Keyboard shortcuts

| Keys | Action |
|---|---|
| Ctrl+O | Add files |
| Ctrl+Shift+O | Add folder |
| Delete | Remove the selected files from the list |
| Ctrl+Enter | Convert |
| Esc | Stop |
| Ctrl+L | Show or hide the log |
| F1 | This guide |
| Ctrl+Q | Exit |

## If something doesn't work

- **Windows says it protected your PC** when installing: click
  **More info → Run anyway**. The installer is not code-signed; that is
  expected.
- **A file was not converted** — press **Details** or open the log. A broken
  or password-protected PDF never stops the other files.
- **Found a bug** — **Help → Report a problem**.

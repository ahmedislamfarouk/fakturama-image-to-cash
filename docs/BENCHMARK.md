# Which reader should read the grid?

Fakturama custom-paints its result grids, so UIA exposes no rows and they have to be
read from pixels. That leaves a choice: a vision model, or local OCR. This is the
measurement behind it.

Reproduce with `python3 tools/bench_readers.py` (add the local row by running it on the
Windows VM, where `rapidocr` is installed).

## Method

| | |
|---|---|
| Images | `docs/screenshots/dbg-items.png` (VATs, 4 columns) and `docs/bench/address-grid.png` (address picker, 8 columns) |
| Ground truth | read by hand and checked against the database |
| Columns | exact match on the header name |
| Values | the cell text must appear, in the right row |
| Time | wall clock per grid, warm |

## Result

| Reader | Columns | Values | Time per grid |
|---|---|---|---|
| **Gemini `gemini-3.5-flash-lite`** | **10/10** | **8/8** | **1,075 ms** |
| Gemini `gemini-flash-lite-latest` | 10/10 | 8/8 | 1,123 ms |
| local `rapidocr-onnxruntime` | 10/10 | **6/8** | 1,218 ms |
| OpenRouter `inclusionai/ling-3.0-flash-vl:free` | 10/10 | 8/8 | 26,158 ms |
| OpenRouter `nex-agi/nex-n2.5-pro:free` | 10/10 | 8/8 | 59,211 ms |

## What the numbers say

> **Nobody misreads a glyph.** These are crisp, machine-rendered tables at 1:1. Character
accuracy was never the differentiator and choosing on it would have been choosing on the
wrong axis.

**Structure is the differentiator, and it is where local OCR fails.** On the address
grid, `rapidocr` returned:

```
["1", "CUST000001", "Marta", "Klein", "NorthstarOffice...10117", "Berlin", "BILLING"]
                                       ^^^^^^^^^^^^^^^^^^^^^^^
```

Company and ZIP merged into one cell. Every character is correct and the record is
still wrong: an exact-match check for ZIP `10117` in the ZIP column fails, and the
Debtor looks absent when it is not. It also read the clipped `addit` header as `A!ppe`.
The models kept all eight columns separate on the same image.

**Latency is the differentiator between the models, not capability.** Every model read
both grids correctly. Gemini's free tier is **24-55x** faster than the free OpenRouter
models on the same images.

## What this project therefore does

| Job | Reader | Why |
|---|---|---|
| *Where* are the columns and rows? | **local OCR** | measured bounding boxes, deterministic to two decimals. Asked for a column centre, a model drifted 12-15 points between two reads of the *same* table -- enough to type into the neighbouring cell |
| *What* does each cell say? | **the model** | it keeps cells in their columns, which is the thing that actually decides whether a record exists |
| Everything, if no key is configured | local OCR | the run still completes without an account, a key or a network. Less reliable on adjacent cells, and the arithmetic gate plus the exact-match rule catch the difference |

That split -- **OCR measures geometry, the model reads content** -- is not a preference.
It is what these two measurements force.

## On Tesseract

`DESIGN.md` originally named Tesseract, written before any of this ran. What shipped is
`rapidocr-onnxruntime`: one pip package, no system binary, which matters when a reviewer
has to reproduce the environment on a Windows VM. Tesseract's advantage is a TSV mode
with per-word boxes, which is the same thing rapidocr gives here, so the trade came down
to installation. It has not been benchmarked, and this document does not claim it was.

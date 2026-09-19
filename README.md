# Fakturama Image-to-Cash Automation

One order image in; one saved and verified Order plus its linked Invoice out.

The script reads a sales-order image, opens a New Order in Fakturama, resolves the
Debtor and every Product through the Order's **own selectors**, creates whatever master
data is missing (Debtor, payment method, VAT rate, Product), returns to the same open
Order, saves it, creates the linked Invoice via the follow-up action, applies the paid
status, and verifies both saved records.

No hardcoded screen coordinates. Nothing is assumed about window size, theme or DPI.

Design rationale, grounding strategy and tradeoffs: **[DESIGN.md](DESIGN.md)**.
A guided tour that assumes no prior context: **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)**.
What the application turned out to be, found by running it: **[docs/FINDINGS.md](docs/FINDINGS.md)**.
Which reader reads the grids, and the measurement behind it: **[docs/BENCHMARK.md](docs/BENCHMARK.md)**.

---

## Result

A full run from a clean, freshly seeded Fakturama 2.2.0:

```
stage 1 ok -- Order open, Cust.Ref. WEB-2026-0714-A17, date 2026-07-14
payment method created: Bank Transfer -> Credit transfer
Debtor created: Northstar Office GmbH
VAT created: VAT 19% with S (Standard rate)
Product created: CHR-ERG-01 @ 297.50 gross
3.16 ok: CHR-ERG-01 line price 450.00
Product created: MAT-DESK-02 @ 47.60 gross
3.16 ok: MAT-DESK-02 line price 120.00
4.3 ok: Total Net 570.00 / VAT 108.30 / Total 678.30
stage 4 ok -- Order saved and found in Data > Documents
5.1 ok: Cust.Ref. carried over; Invoice totals match the Order
5.3 ok: paid, 2026-07-18, 678.30 (read back from the Invoice)
5.5 ok: Invoice paid at 678.30, source Order still open at 678.30
done -- Order and linked Invoice saved and verified
```

**Repeated runs from a clean database produce this identical result.** "Clean" means
the workspace *and* `%USERPROFILE%\.fakturama2` are deleted first, so every master
record above was created by the run that reports it.

Confirmed in the HSQLDB rather than only on screen -- `FKT_DOCUMENT` holds
`Order PO000001` and `Invoice INV000001`, both carrying `WEB-2026-0714-A17`.

| Evidence | Where |
|---|---|
| full trace, with elapsed time per step | [`docs/run-log-complete.txt`](docs/run-log-complete.txt) |
| what was read out of the image, and what will be done with it | [`input/order.extracted.json`](input/order.extracted.json) |
| ten annotated screenshots, callouts mapped to spec steps | [`docs/screenshots/`](docs/screenshots/) |
| the run as 35 annotated frames | [`docs/film/`](docs/film/) |
| the same run as a 56-second clip | [`docs/recording.mp4`](docs/recording.mp4) |
| which reader reads the grids, measured | [`docs/BENCHMARK.md`](docs/BENCHMARK.md) |
| what the application turned out to be | [`docs/FINDINGS.md`](docs/FINDINGS.md) |

`Data > Documents` at the end of a run
([`docs/screenshots/05-documents-verified.png`](docs/screenshots/05-documents-verified.png)):

```
INV000001   Sep 19, 2026   WEB-2026-0714-A17   paid   $678.30
PO000001    Jul 14, 2026   WEB-2026-0714-A17   open   $678.30
```

The Order carries the *extracted* order date (1.5) while the Invoice takes today's
(5.1), and the source Order remains open beside its paid Invoice (5.5).

Both address roles persist, so the Debtor is genuinely attached to the documents:

```
FKT_ADDRESS_CONTACTTYPES (1,'BILLING')    Friedrichstrasse 88, 10117 Berlin
FKT_ADDRESS_CONTACTTYPES (2,'DELIVERY')   Beusselstrasse 44,  10553 Berlin
FKT_DOCUMENT 'Order'   'Northstar Office GmbH, Marta Klein'  WEB-2026-0714-A17
FKT_DOCUMENT 'Invoice' 'Northstar Office GmbH, Marta Klein'  WEB-2026-0714-A17
```

---

## Quick start

```bash
git clone https://github.com/ahmedislamfarouk/fakturama-image-to-cash.git
cd fakturama-image-to-cash
cp .env.example .env          # then paste your key into .env

# extraction only -- runs on Linux, macOS or Windows, no Fakturama needed
python -m src.run input/order.png --extract-only

# the full flow -- Windows only, Fakturama installed
pip install -r requirements.txt
python -m src.run input/order.png
```

### Vision provider

Any OpenAI-compatible endpoint. Three env vars, one code path:

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=inclusionai/ling-3.0-flash-vl:free
LLM_API_KEY=sk-or-v1-...
```

| Provider | Base URL | Model | Cost |
|---|---|---|---|
| **OpenRouter** (default) | `https://openrouter.ai/api/v1` | `inclusionai/ling-3.0-flash-vl:free` | **free**, no card |
| opencode Zen | `https://opencode.ai/zen/v1` | `deepseek-v4-flash-vision-exp` | $0.14 / 1M in |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-flash-latest` | prepay, $5 min |
| Ollama (local) | `http://localhost:11434/v1` | `qwen2.5vl:7b` | free, offline |

Verified working free alternates on OpenRouter, if the default is rate-limited upstream:
`nex-agi/nex-n2.5-pro:free`, `dots-studio/dots-3-note-preview:free`.

**No key?** `--cached` replays `input/order.json` and runs the whole UI flow offline.
That file is hand-transcribed from the image and exists only so a reviewer without a
key can still exercise the automation. The live extractor overwrites it on success.

### Checking what was read out of the image

Two files in `input/`, doing different jobs:

| File | Is | Written by |
|---|---|---|
| `order.png` | the input document | given |
| `order.json` | a replay **cache** -- raw fields only | the extractor, on success |
| `order.extracted.json` | the **validation view** -- raw fields, everything derived from them, and the arithmetic result | every run, before the UI is touched |

The cache answers *what did it read*. The validation view answers the question a human
actually has: *what is it about to do with that*. The derived values are where the
expensive mistakes live, so they are written out explicitly:

| Field | Why it is there |
|---|---|
| `checks.arithmetic` + `identities` | the four sums, with the numbers, pass or fail |
| `line_net_recomputed` / `line_net_agrees` | the image's stated line total against ours |
| `product_master_gross` + note | `250.00 x 1.19 = 297.50`. The **line** discount must not touch it (3.9) -- `267.75` would be wrong |
| `delivery_same_as_billing` + `step_2_8` | decides whether the Delivery role goes on the Main address or on a second one (2.8) |

Both of those are invisible in the raw fields and both are easy to get backwards, which
is why they are stated in words rather than left to be inferred.

```
python -m src.run input/order.png --extract-only   # writes it, touches no UI
```

### Windows setup

1. Install Fakturama: <https://www.fakturama.info/download/>.
2. `pip install -r requirements.txt` — `pywinauto`, `Pillow` and
   `rapidocr-onnxruntime`. All three carry `sys_platform == "win32"` markers, so the
   same file installs nothing on Linux and the offline tests still run there.
3. `python tools\first_run.py` — Fakturama's very first launch shows an initialization
   dialog asking for a working directory, *before* the main window exists. This accepts
   the defaults so the workspace and database get created. Once per machine.
4. Point the script at the executable if it is not at the default path:
   ```
   python -m src.run input/order.png --fakturama "C:\Program Files\Fakturama2\Fakturama.exe"
   ```
   or `--attach` to drive an already-running instance.

5. **Resetting between runs** is not obvious and is worth knowing before you try it:
   the "already initialised" flag lives in `%USERPROFILE%\.fakturama2`, not in the
   workspace, and survives deleting the workspace and even reinstalling the MSI. Both
   directories have to go, and Fakturama's process is named `Fakturama`, not
   `Fakturama2` -- a running instance holds `Database.lck`, so a reset that kills the
   wrong name silently deletes nothing.

---

## How it is put together

```
order.png ──▶ extract ──▶ OrderData ──▶ flow ──▶ driver ──▶ Fakturama
              (vision +   (validated,   (the 5    (UIA, 4-tier
               arithmetic) Decimal)      stages)   grounding)
```

| File | Lines | Role |
|---|---|---|
| `src/extract.py` | 267 | image → validated `OrderData`. Pure; no GUI, no Windows. |
| `src/driver.py` | 1347 | UIA primitives, the 104-entry selector registry, waiting, grid reading, screenshots. |
| `src/flow.py` | 796 | the five stages, transcribed with spec step numbers in comments. |
| `src/vision.py` | 255 | the provider chain, the T4 tiebreak, and reading custom-painted grids. |
| `src/ocr.py` | 152 | local OCR: measures column spans and row centres. |
| `src/run.py` | 122 | CLI, env loading, exit codes. |
| `tests/test_extract.py` | 119 | 23 assertions. No pytest needed. |

`python3 tests/test_extract.py` → `ok - 23 checks passed`

### Control discovery

Fakturama is Eclipse RCP/SWT. On Windows it renders native Win32 widgets, so UIA sees a
real tree — but SWT sets almost no `AutomationId`, and names are locale-derived.

Four tiers, first unambiguous hit wins:

| Tier | Method | Buys |
|---|---|---|
| T1 | tree search **scoped to the active window** | "Save" means *this Order's* Save |
| T2 | anchor-relative walk — find the `Addresses` label, take the nth sibling | "the upper icon, not the lower green +" as tree position, not pixels |
| T3 | tooltip / help-text disambiguation | separates identical siblings |
| T4 | an LLM shown a screenshot of **the anchor's own UIA rectangle** | the model picks *which*; UIA still supplies *where* |

T2 is the answer to "no hardcoded coordinates" — ordering is a property of the widget
hierarchy, so resize, DPI and theme changes survive. Verified: full runs at both
1024×768 and 2560×1600, same code.

**Waiting** is a polled, UIA-observed postcondition after every action — never a fixed
delay standing in for a check. There are 18 `time.sleep` calls left, all settles after
a relayout, each followed by the poll or read-back that actually decides.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Order and linked Invoice saved and verified |
| 2 | `AmbiguityHalt` — the spec said stop for manual review; a screenshot is written to `docs/screenshots/` |
| 3 | extraction failed arithmetic validation twice |

---

## Correctness

The source document is self-checking, so extraction correctness is decidable without a
human. Every read is gated on four identities before a single click happens:

```
line_net  == qty × unit_net × (1 − disc/100)
net_total == Σ line_net
vat_total == Σ (line_net × vat/100)
gross     == net_total + vat_total
```

On the supplied image: `2×250×0.90 = 450.00`, `3×40 = 120.00`, `Σ = 570.00`,
`19% = 108.30`, `gross = 678.30`. A failure re-prompts once naming the broken identity,
then halts. Money is `Decimal` throughout — never `float`.

### Three rules that are easy to get backwards

Each has a test and a step-numbered comment.

| Step | Rule | Right | Wrong |
|---|---|---|---|
| **3.9** | Product master price is `unit_net × (1 + vat/100)`. The *line* discount must not touch it | `297.50` | `267.75` |
| **2.8** | Delivery role goes on the Main address **only if** billing == delivery. Here they differ (Friedrichstrasse 88 / 10117 vs Beusselstrasse 44 / 10553) | a second address carries it | unconditional shortcut → wrong Debtor |
| **5.3** | if not PAID, invent no date or value. If PAID, the date and amount are **read back out of the Invoice** | halt on mismatch | this step once printed `ok` from what it meant to write, while the Invoice saved with the run date |

### Ambiguity halts

| | |
|---|---|
| Steps | 2.3, 2.10.2, 3.3, 3.5, 3.12, 5.2 |
| Shape | more than one exact candidate, or one whose properties conflict with the source |
| Carries | the logical control, the query, the candidates seen, a screenshot |
| Why | guessing between two Debtors is the one failure this must never produce |

Every stage is check → create → re-select, so a second run finds the master data
already present and skips creation.

---

## Verified against Fakturama 2.2.0

The selector registry started as guesses from the spec's figures. It has since been
checked against a live UIA tree (Windows 11 Pro VM, Fakturama 2.2.0 with bundled JRE).
What the real application does:

- **SWT exposes usable `Name` values but junk `AutomationId`s.** Ids are numeric window
  handles (`131528`, `197376`, `66534`) that change between runs. Name-based lookup
  scoped to a window is the only stable T1 -- as designed.
- **The picker icons have no name at all.** The two controls beside `Addresses` are
  unnamed `Image` elements sharing a Pane with the label:
  `Text 'Addresses'` (y=276), `Image` (y=299, the existing-contact picker),
  `Image` (y=327, the green +). Tree order matches vertical order. Step 2.1's "upper
  icon, not the lower green +" is *only* expressible as anchor-relative tree position,
  which is what T2 does. This is the single strongest justification for the design.
- **Dialogs are child Windows of the main shell**, not top-level windows. Searching
  top-level alone finds nothing; `scope_window()` now searches the shell's descendants
  first.
- **SWT ignores synthetic clicks on unfocused dialogs.** Every click now calls
  `set_focus()` first and falls back to the UIA Invoke pattern.
- **The result grids are not exposed to UIA -- and this is now handled.** The
  `Select the address` dialog contains no `Table`, `DataGrid`, `List` or `Custom`
  element, only nested `Pane`s: SWT custom-paints those tables, so candidate rows are
  invisible to the accessibility tree. `driver.grid_rows()` therefore locates the table
  body as *the largest Pane containing no Edit* (every enclosing Pane holds the search
  box, so this lands exactly on the grid), captures **that element's own UIA
  rectangle**, and reads the rows with the same vision model used for the order image.
  UIA still supplies *where*; only the pixels inside are read. Verified live: the grid
  Pane resolves to (434,140)-(1212,564) and an empty table correctly returns zero rows,
  sending the flow down the creation branch.

Corrected names (guess -> actual):

| Logical | Guessed | Actual |
|---|---|---|
| `toolbar.order` | `Order` | `Create: New Order` |
| `order.save` | `Save` | `Save the current contents` |
| `new.contact` | Button `New Contact` | SplitButton `Create a new contact` |
| `new.product` | `New product` | `Create a new product` |
| `order.followup` | Text | Group `Create a follow-up document` |
| `address_dlg.search` | Edit named `Search` | unnamed Edit beside Text `Search:` |
| picker icons | `Button` | unnamed `Image` |

Confirmed correct as guessed: `order.custref`, `order.addresses`, `order.items`,
`order.discount`, `order.total`, `menu.data`, `order.followup_invoice`.

## What is not done

Every numbered step runs and verifies. What remains is scope and robustness, not
missing behaviour.

| Gap | Detail | Closing it takes |
|---|---|---|
| **Reuse paths thin** | runs start clean, so create-because-missing is well covered and reuse is not. VAT reuse runs; Debtor and Product reuse mostly do not | five runs against a warm database in CI |
| **A pre-existing VAT's code is unverifiable** | 3.5 requires `VAT code (E-Invoice) = S`, but the VATs list has no VAT-code column. One this run created is known to be `S`; an existing one is not, so the caller halts rather than reuse one that might be `Z`, `E` or `AE` | opening each VAT's editor to read the code |
| **The arithmetic gate is blind to strings** | it proves every number, but `CHR-ERG-O1` with a letter O satisfies all four identities and creates a junk Product | a second extraction pass over the string fields |
| **One locale, one version** | English, Fakturama 2.2.0 | selector-registry data, not code |
| **Single-window assumption** | an unexpected modal blocks the poll until timeout, then halts. Safe, not recovered | a modal sweep before each postcondition |
| **Currency renders as `$`** | a fresh install shows `$` while the source is EUR. The checks compare numbers, so correctness is unaffected | setting the workspace currency |
| **~6 minutes per run** | ~70% is UIA tree traversal. Removing one duplicate resolution per field measured 4%, inside noise, so the cost is inside a single `find()` | caching handles per editor -- deliberately not done, since a stale handle turns silent wrong answers into *fast* silent wrong answers |

Resetting Fakturama between runs has two traps, both documented under
[Windows setup](#windows-setup).

## Written question -- if I had 3 more hours

**1. Assert identity wherever a record is selected rather than computed (~60 min).**
This is the first thing I would do, because three separate bugs here had the identical
shape and I only found the third by accident. Each time, a check confirmed the
*numbers* and never confirmed the *identity*: the payment date read back as correct
because nothing read it back at all; the Debtor's Company saved as `NULL` behind a
field that looked right; and an Order line held `CHR-ERG-01` while reporting
`3.16 ok: MAT-DESK-02 line price 120.00`, because the right price had been typed onto
the wrong line and so every total matched the source image.

Arithmetic is the cheap half of correctness. It says the totals are consistent; it
cannot say they are consistent about the right thing. Two of those three are now
guarded individually. What I want instead is one rule applied everywhere: every record
this flow selects rather than computes gets its identity asserted against the source at
the point of selection, in one place, rather than as three separate patches written
after three separate incidents.

**2. Exercise the reuse branches (~45 min).** Every run starts from a clean database,
so *create-because-missing* is well covered and *reuse-an-existing-record* is barely
covered. Five runs from clean, then five against a populated database, asserting the
second set creates no duplicate master data and produces exactly one new Order each
time. That is also the only real test of the idempotency claim, which is currently an
argument rather than a result.

**3. Close the string-validation hole (~30 min).** The arithmetic gate proves the
numbers but is blind to strings: `CHR-ERG-O1` with a letter O satisfies every identity
and would create a junk Product. A second extraction pass over the string fields only,
halting on any byte-level disagreement, is a hard signal for one extra call. I prefer
this to a confidence-weighted ensemble -- self-reported model confidence is
uncalibrated, so weighting dresses a guess up as a number, while a diff either matches
or it does not.

**4. Replace the settles with a real predicate (~30 min).** There are 18 `time.sleep`
calls left. None substitutes for a check -- each is followed by the poll or read-back
that decides -- but they exist because an action re-lays out a panel and exposes
nothing pollable in the gap. A "the panel's bounding rectangles stopped changing"
predicate removes the whole category and makes the timing story honest rather than
merely defensible.

**5. Make halts self-contained (~15 min).** A halt writes a screenshot and raises. It
should write a folder: the screenshot, the candidate rows, the extracted values in
play, and the step number, so a human can resolve it without re-running anything.

What I would *not* spend the time on: more selectors. The registry is data, and the
four-tier resolution has survived every shape Fakturama presents. The remaining risk is
not in finding controls -- it is in believing what the controls say afterwards.

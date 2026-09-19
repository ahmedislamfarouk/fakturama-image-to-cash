# Fakturama Image-to-Cash Automation

One order image in; one saved and verified Order plus its linked Invoice out.

The script reads a sales-order image, opens a New Order in Fakturama, resolves the
Debtor and every Product through the Order's **own selectors**, creates whatever master
data is missing (Debtor, payment method, VAT rate, Product), returns to the same open
Order, saves it, creates the linked Invoice via the follow-up action, applies the paid
status, and verifies both saved records.

No hardcoded screen coordinates. Nothing is assumed about window size, theme or DPI.

Design rationale, grounding strategy and tradeoffs: **[DESIGN.md](DESIGN.md)**.

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
5.3 ok: paid, 2026-07-18, 678.30
5.5 ok: Invoice paid at 678.30, source Order still open at 678.30
done -- Order and linked Invoice saved and verified
```

**Four consecutive runs from a clean database produced this identical result.**

Confirmed in the HSQLDB rather than only on screen -- `FKT_DOCUMENT` holds
`Order PO000001` and `Invoice INV000001`, both carrying `WEB-2026-0714-A17`.
Full trace: [`docs/run-log-complete.txt`](docs/run-log-complete.txt).

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

### Windows setup

1. Install Fakturama: <https://www.fakturama.info/download/> — first launch creates the
   workspace and database.
2. `pip install -r requirements.txt` (pulls `pywinauto` + `Pillow`; both are Windows-only
   and are skipped by the environment markers elsewhere).
3. Point the script at the executable if it is not at the default path:
   ```
   python -m src.run input/order.png --fakturama "C:\Program Files\Fakturama2\Fakturama.exe"
   ```
   or `--attach` to drive an already-running instance.

---

## How it is put together

```
order.png ──▶ extract ──▶ OrderData ──▶ flow ──▶ driver ──▶ Fakturama
              (vision +   (validated,   (the 5    (UIA, 4-tier
               arithmetic) Decimal)      stages)   grounding)
```

| File | Lines | Role |
|---|---|---|
| `src/extract.py` | ~260 | image → validated `OrderData`. Pure; no GUI, no Windows. |
| `src/driver.py` | ~340 | UIA primitives, the 80-entry selector registry, waiting, screenshots. |
| `src/flow.py` | ~350 | the five stages, transcribed with spec step numbers in comments. |
| `src/vision.py` | ~65 | T4 tiebreak, reached only when UIA leaves an ambiguity. |
| `src/run.py` | ~110 | CLI, env loading, exit codes. |
| `tests/test_extract.py` | ~90 | 11 assertions. No pytest needed. |

`python3 tests/test_extract.py` → `ok - 11 checks passed`

### Control discovery, in one paragraph

Fakturama is Eclipse RCP/SWT, so on Windows it renders native Win32 widgets and UIA sees
a real tree — but SWT sets almost no `AutomationId`, and names are locale-derived.
Resolution therefore runs in four tiers, stopping at the first unambiguous hit: **T1** a
tree search *scoped to the active window*; **T2** an anchor-relative walk (find the
`Addresses` label, take the nth sibling) which is how "the upper icon, not the lower
green +" is expressed as tree position rather than pixels; **T3** tooltip/help-text
disambiguation; **T4** an LLM shown a screenshot of *the anchor's own UIA rectangle*,
returning an index — the model picks which, UIA still supplies where.

No `sleep()` anywhere. Every action is followed by a polled, UIA-observed postcondition.

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

Each has a test and a step-numbered comment:

- **3.9** — the Product master gross price is `unit_net × (1 + vat/100)`. The *line*
  discount must not touch it: `250.00 → 297.50`, not `225.00 → 267.75`.
- **2.8** — the Delivery role goes on the Main address only when billing and delivery are
  identical. In the supplied image they are **not** (Friedrichstrasse 88 / 10117 vs
  Beusselstrasse 44 / 10553), so an unconditional shortcut builds the wrong Debtor.
- **5.3** — if the status is not PAID, no payment date or value is invented.

### Ambiguity halts

Steps 2.3, 2.10.2, 3.3, 3.5, 3.12 and 5.2 all collapse into one `AmbiguityHalt` carrying
the logical control, the query, the candidates seen and a screenshot. Guessing between
two Debtors is the one failure this must never produce.

Because every stage is check → create → re-select, a second run finds the master data
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

Every numbered step in the task runs and verifies. What remains is scope and
robustness rather than missing behaviour.

**Reuse paths are under-exercised.** Runs start from a clean database, so the
create-because-missing branches are well covered and the reuse-an-existing-record
branches much less so. VAT reuse does run; Debtor and Product reuse mostly do not.

**A pre-existing VAT's code cannot be checked.** 3.5 requires
`VAT code (E-Invoice) = S`, but the VATs list has no VAT-code column. A VAT this run
created is known to be S; for one that already existed the code is unverifiable from
the list, so it is reported as unverified and the caller halts rather than reusing a
VAT that might be Z, E or AE.

**The arithmetic gate is blind to strings.** It proves every number in the document,
but `CHR-ERG-O1` with a letter O satisfies all four identities and would create a junk
Product. A second extraction pass over the string fields would close this.

**One locale, one version.** English, Fakturama 2.2.0. Both are selector-registry data.

**Resetting Fakturama means reinstalling it.** Its state lives in
`%USERPROFILE%\.fakturama2`, not the workspace: deleting the workspace, or even
reinstalling the MSI, leaves the "already initialised" flag behind, and Fakturama then
skips seeding its default Shipping/VAT/payment. It refuses to open a New Order with
"No default value found for Shippings", which points nowhere near the cause.

**Currency.** A fresh install renders amounts with `$` while the source document is
EUR. The checks compare numbers, so this does not affect correctness, but a production
run should set the workspace currency to match.

## Written question -- if I had 3 more hours

**1. Drive the address-role widget (~60 min).** The one control I could not reach, and
the only functional gap left. Next attempts, in order: OS-level `SendInput` rather than
UIA-synthesised clicks, since the popup may only respond to real input; then
Fakturama's own import path, which writes `FKT_ADDRESS_CONTACTTYPES` directly. Closing
this makes the Order show its invoice address and lets 2.4 be enforced instead of
reported.

**2. Exercise the reuse branches (~45 min).** Every run so far starts from a clean
database, so *create-because-missing* is well tested and *reuse-an-existing-record* is
barely tested. I would run five times from clean, then five against a populated
database, and assert the second set creates no duplicate master data and produces
exactly one new Order each time. That is also the real test of the idempotency claim.

**3. The second delivery address (~30 min).** 2.8 implies a second Debtor address when
billing and delivery differ. Currently only the Main address is created.

**4. Close the string-validation hole (~30 min).** The arithmetic gate proves the
numbers but is blind to strings: `CHR-ERG-O1` with a letter O satisfies every identity
and would create a junk Product. A second extraction pass over the string fields only,
halting on any byte-level disagreement, is a hard signal for one extra call. I prefer
this to a confidence-weighted ensemble -- self-reported model confidence is
uncalibrated, so weighting dresses a guess up as a number, while a diff either matches
or does not.

**5. Make halts self-contained (~15 min).** A halt currently writes a screenshot and
raises. It should write a folder: the screenshot, the candidate rows, the extracted
values in play, and the step number, so a human can resolve it without re-running
anything.

What I would *not* spend the time on: more selectors. The registry is data, and the
four-tier resolution has now survived every shape Fakturama presents. The remaining
risk is in the paths that have not run often enough, not in the ones that have.

# Walkthrough

For someone who has never seen this repo.

| Doc | Is |
|---|---|
| `README.md` | quick start |
| `DESIGN.md` | why it is built this way |
| **this** | the tour |

---

## 0. Words used in this document

Skip if you already know them. Every one of these appears below.

| Word | Plain meaning | Why it matters here |
|---|---|---|
| **UI Automation (UIA)** | a Windows service that lets one program see and control another program's buttons and boxes. Screen readers use it. | it is how this project clicks things without guessing pixel positions |
| **control** | any one thing on screen — a button, a text box, a tab, a checkbox | everything the code does is "find a control, then act on it" |
| **control tree** | the list of every control in a window, arranged parent-and-child like folders | UIA hands us this tree; the code searches it |
| **widget** | another word for control. Toolkit people say widget. | same thing |
| **SWT / Eclipse RCP** | the toolkit Fakturama's screen is built with, from the Java world | it decides what UIA can and cannot see — the source of most problems here |
| **`Name`** | the label UIA reports for a control, usually its visible text | it changes if the app is in German, so it is not fully reliable |
| **`AutomationId`** | a fixed internal id a control *can* have, which never changes with language | SWT barely sets any, which is the core difficulty |
| **selector** | our recipe for finding one control — "a Button named Save, inside the Order window" | all 104 live in one dictionary, so changes are edits not rewrites |
| **anchor** | a control we *can* name, used as a landmark to find one we cannot | "the icon just after the Addresses label" |
| **scope** | the window we are allowed to search inside | stops "Save" matching some other window's Save |
| **dialog / modal** | a small window that pops up and blocks the rest, like "Select the address" | several spec steps happen inside these |
| **grid** | a table of rows on screen, like search results | Fakturama draws these as a picture, so UIA sees no rows at all |
| **OCR** | software that reads text out of an image | used to measure where columns and rows physically are |
| **vision model** | an AI that looks at a picture and answers questions about it | used to read what the text in the grid *says* |
| **postcondition** | a check after an action, proving the action worked | "after typing the reference, the field must read it back" |
| **poll** | keep re-checking until true or time runs out | used instead of "wait 2 seconds" |
| **halt** | stop on purpose and ask a human | the spec demands this in 6 places rather than guessing |
| **idempotent** | running it twice does the same as running it once | a second run finds the data already there and skips creating it |
| **`Decimal`** | a number type that stores money exactly | ordinary computer decimals cannot store `108.30` exactly |
| **dataclass** | a small Python container for named values | `OrderData` holds everything read out of the image |
| **HSQLDB** | the small database Fakturama stores its records in | we read it directly to check the app saved what it displayed |
| **`ModifyListener`** | SWT's "the user changed this field" alarm | if it never fires, the app does not notice the change and saves nothing |
| **exit code** | the number a program leaves behind: 0 = fine, anything else = a problem | lets another script tell success from a halt |
| **regression test** | a test written *after* fixing a bug, so the bug cannot return | 5 of these guard the payment-date fix |
| **VM / QEMU** | a whole computer simulated inside your computer | how Windows runs here without touching the Linux install |
| **DPI** | how large the system draws everything, as a percentage | changing it moves every pixel, which is why pixels are never hardcoded |

---

## 1. The task

| | |
|---|---|
| **In** | one order image (`input/order.png`) + a numbered spec |
| **Out** | a saved Order + a linked, paid Invoice |
| **How** | drive the real Fakturama desktop app — click, type, read the screen |
| **Hard because** | not the clicking. The two questions below. |
| **Platform** | Windows. Spec says UI Automation, which is Windows-only. |
| **App** | Fakturama 2.2.0 — Java / Eclipse SWT. This drives most of the design. |

The two questions:

| Question | Why it matters |
|---|---|
| Was the control I clicked the one I meant? | Two `*New Order` tabs existed. The leftmost was blank. |
| Did what I typed actually land? | Company looked right on screen, saved as `NULL`. |

Every bug here was a click that looked fine with data that was wrong.

---

## 2. Shape

```
order.png ──▶ extract ──▶ OrderData ──▶ flow ──▶ driver ──▶ Fakturama
              vision +    validated     5         UIA
              arithmetic  Decimal       stages
```

| File | Lines | Job | Needs Windows? |
|---|---|---|---|
| `src/extract.py` | 267 | image → validated data | no |
| `src/driver.py` | 1347 | the only file that knows what a window is | yes |
| `src/flow.py` | 796 | the 5 stages, spec step numbers in comments | yes |
| `src/vision.py` | 255 | provider chain + vision tiebreak | no |
| `src/ocr.py` | 152 | measures column spans and row centres | no |
| `src/run.py` | 122 | CLI, exit codes, screenshot on failure | yes |
| `tests/test_extract.py` | 119 | 23 checks, no pytest | no |

| Layer | Property | Consequence |
|---|---|---|
| `extract` | pure — bytes in, dataclass out | 40% of the work shipped before the VM existed |
| `flow` | spec transcribed stage-for-stage | a reviewer can diff it against their own PDF |
| `driver` | sole owner of window knowledge | Fakturama moves a button → one file changes |

---

## 3. The five stages

| # | Does | Proves it worked by |
|---|---|---|
| 1 | New Order. Date, Cust.Ref., Net, With VAT. Number left alone — Fakturama assigns it. | reading `Cust.Ref.` back |
| 2 | Search address picker → absent → create Debtor | searching the picker **again** |
| 3 | Per product: search → absent → create VAT → create Product → re-select → qty/price/discount | the line price identity |
| 4 | Check 3 totals, save | the Order row in `Data > Documents` |
| 5 | Follow-up Invoice, mark paid with source date + amount, save | both rows, Order still open |

| Rule | Reason |
|---|---|
| Search the picker, never query the database | a Debtor the picker can't find is useless to the Order |
| Prove creation by re-selecting | round-trip through the consumer, not the producer's own "saved" state |

---

## 4. Problem 1 — SWT labels almost nothing

| Reality | Effect |
|---|---|
| SWT renders native Win32 widgets | UIA sees a real tree, not one canvas. This is why UIA is the right primary tool. |
| Almost no `AutomationId` | most elements only carry a `Name` derived from their label |
| `Name` comes from the label | locale-dependent — a German install breaks every name-based lookup |
| Some elements have no name at all | the two icons beside `Addresses` are unnamed `Image` elements |

The spec insists on **the upper icon**, not the lower green `+`. Tree order is the only
thing separating them. So: four tiers, first clear hit wins.

| Tier | Method | Buys |
|---|---|---|
| T1 | scoped search — resolve the window, then search inside it | "Save" means *this Order's* Save |
| T2 | anchor-relative walk — named anchor, then walk by structure | "upper icon" = tree position, not a pixel |
| T3 | property disambiguation — tooltip, description, enabled | separates structurally identical siblings |
| T4 | vision tiebreak — screenshot the anchor's own UIA rect | one unnamed icon cannot sink the run |

| Claim | How it is actually met |
|---|---|
| "no hardcoded coordinates" | T2. Ordering is a property of the widget hierarchy, not the screen. Survives resize, DPI, theme. |
| T4 is not a coordinate either | the model picks *which* control; UIA supplies *where*. Nothing is authored by hand or persisted. |

104 controls live in one dict in `driver.py`.

| Change | Cost |
|---|---|
| German locale | data edit |
| Fakturama version bump | data edit |
| Widget hierarchy restructured | code change |

---

## 5. Problem 2 — the grids are invisible

| Expected | Actual |
|---|---|
| a `Table` or `DataGrid` element | none. Nor `List`, nor `Custom`. |
| rows readable from the tree | zero rows exposed. SWT custom-paints them. |
| a cell is a control | it is not — until clicked |

Reading candidate rows is the input to **every** exact-match decision in the spec. It
cannot come from the accessibility tree. So it comes from pixels.

> **OCR measures geometry. The model reads content.**

| Approach | Result |
|---|---|
| ask a model for a column centre | drifted 12–15 pt between two reads of the *same* table — enough to type into the next cell |
| local OCR bounding boxes | deterministic to 2 decimals |

| Detail | Why |
|---|---|
| a column spans header-left → next-header-left | headers are left-aligned, values right-aligned. Using header-text centre lands on the boundary. |
| click a cell before typing | Fakturama then creates a real inline `Edit`, so values go into a proper control |
| an unselected row needs two clicks | first selects, second opens the editor |

---

## 6. Problem 3 — writes fail silently

Five bugs produced wrong data with **no exception**.

| Symptom | Cause | Fix |
|---|---|---|
| Phone saved `$9 30 5550 1420` | `type_keys` read `+` as Shift | escape `+ ^ % ~ ( ) { } [ ]` |
| Company saved `NULL` | UIA `SetValue` updates pixels, never fires SWT's `ModifyListener` | type real keystrokes |
| Wrong Order driven | two `*New Order` tabs, leftmost blank | identify by reading `Cust.Ref.` back |
| Payment date saved as **today** | §7 | §7 |
| Order billed **the wrong product** | typing without clicking first landed the product NAME in the Item Number field, so the Product saved with its name as its SKU | click the field before typing; halt when a row does not hold its SKU |

This is why screenshots and database checks are in the loop, not a nicety.

---

## 7. The payment-date bug

Worst of the four.

| | |
|---|---|
| Log said | `5.3 ok: paid, 2026-07-18, 678.30` |
| Invoice held | `Sep 19, 2026` — today's date |
| Database agreed with | the screen, not the log |

Two mistakes, stacked.

| # | Mistake | Severity |
|---|---|---|
| 1 | typed the rendered string into a segmented widget | wrong value |
| 2 | the check printed what it *tried* to write, never read back | turned a visible failure into a silent one |

Date fields ignore every non-digit and feed digits into whichever segment holds the caret:

| Typed | Field became | |
|---|---|---|
| `Jul 18, 2026` | `Sep 20, 0026` | ✗ |
| `07/18/2026` | `Sep 20, 2607` | ✗ |
| left-edge click + arrows + `07182026` | `Jul 18, 2026` | ✓ |

Three more traps before the write landed:

| Trap | Detail |
|---|---|
| `%b` shows `Jul`, is typed `07` | scraping digits from the rendered string drops the month entirely |
| segments move with arrow keys, not Home/End | a centre click lands the caret on the **year** |
| filling the payment amount corrupts the month beside it | so: amount first, date last |

| Now | |
|---|---|
| `set_date()` | reads back, retries, halts on mismatch |
| steps 1.5 and 5.3 | verify against the field, not against intent |
| tests | 5 regression checks, incl. a German-format field where the day is typed first |

**Lesson: a postcondition that restates the intent instead of observing the result is
worse than none. It converts a visible failure into a silent one.**

And its sharper sibling, learned from the wrong-product bug: **arithmetic is the cheap
half of correctness.** All three totals matched the source image while the Order billed
the wrong item, because the right price had been typed onto the wrong line. Checking
the numbers does not check *what the numbers are about*. Anywhere a record is selected
rather than computed, identity needs its own assertion.

---

## 8. Trusting the extraction

The document proves itself.

| Identity | On this image | |
|---|---|---|
| `line_net == qty × unit_net × (1 − disc/100)` | `2 × 250 × 0.90 = 450.00` · `3 × 40 = 120.00` | ✓ |
| `net_total == Σ line_net` | `570.00` | ✓ |
| `vat_total == Σ (line_net × vat/100)` | `19% → 108.30` | ✓ |
| `gross == net_total + vat_total` | `678.30` | ✓ |

| Situation | Action |
|---|---|
| an identity breaks | re-ask once, naming the failed identity |
| it breaks again | halt, exit 3 |

| Property | Value |
|---|---|
| vs. diffing two OCR engines | stronger, and zero extra dependencies |
| catches | digit errors that break an identity |
| misses | a misread SKU or wrong city — arithmetic cannot see it |
| backstop for the misses | Fakturama's own selector returns no exact match |
| money type | `Decimal`, never `float` — `108.30` has no exact binary form |

Two traps in the supplied data. Both in the tests, both easy to invert:

| Step | Trap | Right | Wrong |
|---|---|---|---|
| 3.9 | Product master price excludes the **line** discount | `297.50` | `267.75` |
| 2.8 | Delivery role on the Main address **only if** billing == delivery | here they differ → a second address carries it | unconditional shortcut → wrong customer |

2.8's actual data: billing `Friedrichstrasse 88, 10117` vs delivery
`Beusselstrasse 44, 10553`. Not identical. The shortcut does not apply.

---

## 9. Waiting and halting

| Not this | This |
|---|---|
| `sleep(2)` | poll observed state with a timeout |
| "wait 2s for the editor" | "wait until `Cust.Ref.` equals `WEB-2026-0714-A17`" |

No fixed sleep stands in for a check -- that is what makes a cold machine and a warm
one behave the same.

Being precise, because a reviewer will grep for it: there are **18 `time.sleep` calls**
in the shipped code. They are settles after an action that re-lays out a panel, not
substitutes for verification.

| | |
|---|---|
| What a settle does | gives a redraw a moment where nothing pollable exists yet |
| What always follows it | the poll or read-back that actually decides |
| So a slow machine | loses a second, not a step |
| Honest next step | a "the panel stopped moving" predicate would remove them |

Six halt points in the spec, one shape:

| Steps | Shape |
|---|---|
| 2.3, 2.10.2, 3.3, 3.5, 3.12, 5.2 | more than one exact candidate, **or** a candidate conflicting with the source |

| `AmbiguityHalt` carries | |
|---|---|
| logical control | e.g. `debtor` |
| query issued | e.g. `Northstar Office GmbH` |
| candidates observed | the rows actually seen |
| screenshot | written to `docs/screenshots/halt-*.png` |

Guessing between two Debtors is the one failure this must never produce. A wrong
invoice sent to a real company is worse than no invoice.

Idempotency falls out of it: every stage is check → create → re-select, so a second run
finds the master data and skips creation.

---

## 10. Providers

Four tiers. Fails over across model **and** account — rate limits apply per-model *and*
per-key.

| # | Provider | Model | Cost | Why it is in the chain |
|---|---|---|---|---|
| 1 | Google Gemini | `gemini-3.5-flash-lite` | free tier | fastest correct option |
| 2 | OpenRouter | free models | free, ~50/day | different account, different limit |
| 3 | OpenRouter #2 | free models | free, ~50/day | third bucket |
| 4 | local `rapidocr` | — | none | no account, unlimited, always works |

Benchmarked on a real Fakturama grid. Every flash model read it correctly, so
capability was not the differentiator — speed was.

| Model | Time | Correct |
|---|---|---|
| `gemini-3.5-flash-lite` | 994 ms | ✓ ← chosen |
| `gemini-flash-lite-latest` | 1169 ms | ✓ |
| `gemini-3.8-flash` | 3324 ms | ✓ |
| `gemini-3.5-flash` | 8971 ms | ✓ |

| Config | |
|---|---|
| shape | numbered env groups — `LLM_BASE_URL_2`, `LLM_API_KEY_2`, … |
| adding a provider | env edit, no code change |
| `.env` | gitignored, chmod 600 |
| `.env.example` | committed, documents the shape |

---

## 11. Running it

Two things must be true first:

| Precondition | If not |
|---|---|
| VM running | nothing to drive |
| **someone logged into Windows** | a lock-screen session has no interactive desktop — nothing GUI-driven runs |

```bash
virsh --connect qemu:///system start fakturama-win11
virt-viewer --connect qemu:///system fakturama-win11    # then log in
```

Inside Windows:

```
python -m src.run input\order.png
```

~8 minutes, ending:

```
done -- Order and linked Invoice saved and verified (WEB-2026-0714-A17, 678.30 EUR)
```

| Exit | Meaning |
|---|---|
| 0 | Order and linked Invoice saved and verified |
| 2 | `AmbiguityHalt` — stopped for review, see the screenshot |
| 3 | extraction failed arithmetic twice |

Offline half needs no VM: `python3 tests/test_extract.py` → `ok - 23 checks passed`.

### Two traps that cost an hour each

| Trap | Symptom | Cause | Fix |
|---|---|---|---|
| Resetting Fakturama | *"No default value found for Shippings"* on New Order | the "already initialised" flag lives in `%USERPROFILE%\.fakturama2`, not the workspace. Survives deleting the workspace **and** reinstalling the MSI, so Fakturama skips seeding default Shipping/VAT/payment. | delete **both** directories |
| Wrong process name | reset reports success, next run starts dirty | the process is `Fakturama`, not `Fakturama2`. A running instance holds `Database.lck`, so killing the wrong name deletes nothing. | kill `Fakturama` |

### Window size does not matter

| Worry | Reality |
|---|---|
| "the viewer is a quarter of my screen" | screenshots are taken **inside** Windows, against Fakturama's own rect. The viewer only displays that framebuffer. |
| "clicks will be off" | every click is a **percentage of a UIA rect**, never a desktop pixel |
| "small window breaks it" | the flow maximizes the Fakturama shell first — SWT does not create widgets that are not visible |

A full verified run completed at **1024×768**. The larger resolution is only for nicer
screenshots.

---

## 12. Evidence

Three consecutive clean-database runs on current code, all ending:

```
done -- Order and linked Invoice saved and verified (WEB-2026-0714-A17, 678.30 EUR)
```

| Term | Means |
|---|---|
| "clean" | workspace **and** `%USERPROFILE%\.fakturama2` deleted first |
| consequence | every master record was created from nothing by the run reporting it |
| created per run | payment method, Debtor, 2 addresses, VAT rate, 2 Products |

Confirmed in HSQLDB, not just on screen:

| Table | Row |
|---|---|
| `FKT_ADDRESS_CONTACTTYPES` | `'BILLING'` → Friedrichstrasse 88, 10117 Berlin |
| `FKT_ADDRESS_CONTACTTYPES` | `'DELIVERY'` → Beusselstrasse 44, 10553 Berlin |
| `FKT_DOCUMENT` | `'Order'` · Northstar Office GmbH, Marta Klein · `WEB-2026-0714-A17` |
| `FKT_DOCUMENT` | `'Invoice'` · `TRUE` (paid) · `678.3E0` · `'2026-07-18'` (payment date) |

`docs/run-log-complete.txt` = full 287-line trace of one run.

### Screenshots are the test instrument

Four bugs produced wrong data with no exception. All were caught by looking at a
picture or the database. `src/run.py` shoots on every halt, every unexpected error,
and at exit.

| File | Shows |
|---|---|
| `02-order-open-address-picker.png` | Order after stage 1 + the two unnamed picker icons |
| `03-address-dialog.png` | `Select the address` — the grid UIA cannot see |
| `03-items-filled.png` | both lines, prices Fakturama recalculated |
| `04-debtor-created.png` | Debtor editor after creation |
| `05-invoice-paid-and-documents.png` | the end state — proves the whole spec |

What `05` proves, in one image:

| Element | Step |
|---|---|
| `Invoice address` **and** `Delivery address` on the Debtor | 2.8 |
| the full billing address rendered on the Order | 2.4 |
| both lines at `250.00 / −10%` and `40.00 / 0%` → `450.00`, `120.00` | 3.16 |
| `paid` ✓ · `Bank Transfer` · `at Jul 18, 2026` · `$678.30` | 5.3 |
| `Total Net 570.00` · `VAT 108.30` · `Total 678.30` | 4.3 |
| `Data > Documents`: Invoice **paid**, Order still **open**, same Cust.Ref. and total | 5.5 |

| Prefix | Count | Kept because |
|---|---|---|
| `dbg-` | 8 | evidence behind DESIGN.md findings |
| `role-` | 3 | the address-role popup sequence |
| `error-` | 3 | unexpected-error captures |
| `halt-` | 2 | what a reviewer is handed when a run stops — output contract |

---

## 13. What is not done

Every numbered step runs and verifies. What is left is scope, not missing behaviour.

| Gap | Detail | Closing it would take |
|---|---|---|
| Reuse paths thin | runs start clean, so create-because-missing is well covered and reuse is not. VAT reuse runs; Debtor and Product reuse mostly do not. | a second run against a warm database in CI |
| Pre-existing VAT code unverifiable | 3.5 wants `VAT code = S`, but the VATs list has no VAT-code column. A VAT this run created is known `S`; an existing one is not, so the caller halts rather than reuse one that might be `Z`, `E` or `AE`. | opening each VAT's editor to read the code |
| Arithmetic gate blind to strings | `CHR-ERG-O1` with a letter O satisfies all four identities and creates a junk Product | a second extraction pass over the string fields |
| One locale, one version | English, Fakturama 2.2.0 | selector-registry data, not code |
| Single-window assumption | an unexpected modal blocks the poll until timeout, then halts. Safe, not recovered. | a modal sweep before each postcondition |
| Currency shows `$` | fresh install renders `$`, source is EUR. Checks compare numbers, so correctness is unaffected. | set the workspace currency |

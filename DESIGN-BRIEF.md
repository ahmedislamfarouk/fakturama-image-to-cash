# Fakturama Image-to-Cash — Design, in brief

**Ahmed Islam Farouk Abbas** · TJM Labs take-home, Part 1 · full version in `DESIGN.md`

| | |
|---|---|
| **In** | one order image |
| **Out** | a saved, verified Order and its linked Invoice |
| **Forbidden** | hardcoded coordinates; any assumption about window size, theme or DPI |
| **The hard part** | not clicking — deciding whether what I am looking at is the thing I meant, and stopping when I cannot tell |

**Shape:** `extract → flow → driver`. `extract` is pure, so it ships and is tested
before a Windows VM exists. `flow` is the spec transcribed, step numbers in the
comments. `driver` is the only module that knows what a window is.

## Finding controls

Eclipse/SWT: UIA sees a real control tree, but almost nothing carries an `AutomationId`,
names are locale-derived, and the two icons beside `Addresses` — where the spec insists
on the *upper* one — have no name at all.

| Tier | Method | Buys |
|---|---|---|
| **T1** | scoped search — resolve the window, then search inside it | "Save" means *this Order's* Save |
| **T2** | anchor-relative walk — a named landmark, then the nth child | **"the upper icon" as tree position, not a pixel** |
| **T3** | tooltip / description / enabled state | separates identical siblings |
| **T4** | screenshot the anchor's **own UIA rectangle**, ask a model which | one unnamed icon cannot sink the run |

**T2 is the answer to "no hardcoded coordinates."** Ordering is a property of the widget
hierarchy, read from the live tree at call time. 104 selectors in one dict.

## Reading the image

| Job | Tool | Why |
|---|---|---|
| *What* the table says | LLM vision, JSON schema | returns **structure** — which column is `Disc.`, which value is `BILLING` |
| *Where* the cells are | local OCR | measured boxes; a model drifted 12–15 pt between two reads of the same table. Also the last tier — no account, no key, still runs |

Then the document is checked against itself, so correctness is decidable without a human:

| Identity | Here |
|---|---|
| `line_net == qty × unit_net × (1 − disc)` | `450.00`, `120.00` |
| `net == Σ line_net` · `vat == Σ (line × vat%)` · `gross == net + vat` | `570.00` · `108.30` · `678.30` |

A break means a misread digit: re-prompt once, then halt. Money is `Decimal`, never
`float`. Honest limit — a misread SKU passes all four, caught later by Fakturama's own
selector finding no match.

## Verifying, and stopping

| Invariant | Meaning |
|---|---|
| **Selection is the existence check** | never query the database — ask the Order's own picker. Not selectable *from the Order* = correctly absent |
| **Saving is confirmed by re-selection** | the proof a Debtor persisted is that the Order's picker now finds it |
| **Ambiguity halts** | >1 exact candidate, or one conflicting with the source → one `AmbiguityHalt` with the control, query, candidates and a screenshot; non-zero exit |

Guessing between two Debtors is the one failure this must never produce. Idempotency
falls out: every stage is check → create → re-select.

## What it costs

| Decision | Paid |
|---|---|
| UIA first, vision only as a tiebreak | Windows-only; SWT's thin `AutomationId` coverage is what forces T2 |
| Arithmetic self-check over a second OCR pass | blind to errors that preserve the identities |
| Halt on ambiguity | the interesting cases need a human |

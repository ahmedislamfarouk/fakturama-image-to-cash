# Fakturama Image-to-Cash — Design, in brief

**Ahmed Islam Farouk Abbas** · TJM Labs take-home, Part 1 · full version in `DESIGN.md`

One order image in; a saved, verified Order and its linked Invoice out. No hardcoded
coordinates, nothing assumed about window size, theme or DPI.

**The interesting part is not clicking. It is deciding, at each step, whether what I am
looking at is the thing I meant — and stopping when I cannot tell.**

**Shape.** `extract → flow → driver`. `extract` is pure, so it ships and is tested
before a Windows VM exists. `flow` is the spec transcribed, step numbers in the
comments. `driver` is the only module that knows what a window is.

## Finding controls

Fakturama is Eclipse/SWT: UIA sees a real control tree, but almost nothing carries an
`AutomationId`, names are locale-derived, and the two icons beside `Addresses` — where
the spec insists on the *upper* one — have no name at all. So, first unambiguous hit wins:

| Tier | Method |
|---|---|
| T1 | scoped search — resolve the window first, then search inside it |
| T2 | anchor-relative walk — a named landmark, then the nth child |
| T3 | tooltip / description / enabled state |
| T4 | screenshot the **anchor's own UIA rectangle**, ask a model which, click by handle |

**T2 is the answer to "no hardcoded coordinates":** ordering is a property of the widget
hierarchy, not of the screen, read from the live tree at call time. All 104 selectors
live in one dict, so a locale or version change is a data edit.

## Reading the image

LLM vision constrained to a JSON schema, because it returns *table structure* — which
column is `Disc.`, which value is `BILLING` — rather than a bag of words whose geometry
I then rebuild. Local OCR is the last tier and needs no account at all.

Then the document is checked against itself, so correctness is decidable without a
human: `qty × unit_net × (1 − disc) = line_net` per line, lines summing to `570.00`,
VAT `108.30`, gross `678.30`. A break means a misread digit: re-prompt once, then halt.
Money is `Decimal`, never `float`. The limit is honest — a misread SKU passes all four,
and is caught later by Fakturama's own selector finding no match.

## Verifying, and stopping

- **Selection is the existence check.** Never query the database — ask the Order's own
  picker. A record not selectable *from the Order* is correctly absent.
- **Saving is confirmed by re-selection.** The proof a Debtor persisted is that the
  Order's picker now finds it.

Ambiguity — more than one exact candidate, or one conflicting with the source — raises
one `AmbiguityHalt` carrying the control, the query, the candidates and a screenshot,
and exits non-zero. Guessing between two Debtors is the one failure this must never
produce. Idempotency falls out: every stage is check → create → re-select.

## Tradeoffs

UIA first, vision only as a tiebreak: fast and deterministic, but Windows-only.
Anchor-relative resolution survives resize, DPI and theme, and breaks only if the
hierarchy is restructured. The arithmetic gate is free and decidable, and blind to
errors that preserve the identities. Halting on ambiguity means no silently wrong
records, at the cost of needing a human for the interesting cases.

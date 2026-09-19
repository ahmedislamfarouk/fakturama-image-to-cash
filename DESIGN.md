# Fakturama Image-to-Cash — Design Document

**Ahmed Islam Farouk Abbas** · TJM Labs take-home, Part 1

---

## 1. Problem

One order image in; one saved and verified Order plus its linked Invoice out. The Debtor,
Payment Method, VAT rate and every Product are resolved from Fakturama's own selectors, and
created only when no exact match exists. No hardcoded screen coordinates, no assumption about
window size, theme or layout.

The interesting part is not clicking. It is deciding, at each step, whether what I am looking at
is the thing I meant — and stopping when I cannot tell.

## 2. Shape of the system

Three layers, one direction:

```
order.png ──▶ extract ──▶ OrderData ──▶ flow ──▶ driver ──▶ Fakturama
              (vision +   (validated,   (the 5    (UIA primitives)
               arithmetic) Decimal)      stages)
```

- **extract** is pure: image bytes in, validated dataclass out. Testable with no GUI, no Windows,
  no Fakturama. This is why ~40% of the work ships before the VM exists.
- **flow** is the spec transcribed. It reads next to the PDF, stage for stage, so a reviewer can
  diff it against their own document.
- **driver** is the only module that knows what a window is. When Fakturama 2.2 moves a button,
  one file changes.

## 3. Control discovery and grounding

### The actual obstacle

Fakturama is an Eclipse RCP / SWT application. On Windows, SWT renders **native Win32 widgets**,
so UIA sees a real control tree rather than one opaque canvas — that is the good news, and it is
why UIA is the right primary tool here.

The bad news is specific and worth naming: SWT sets almost no `AutomationId`. Most elements
surface only a `Name` derived from their label, which is **locale-dependent**; and a few — notably
the two icons beside *Addresses*, where the spec is emphatic about picking the upper one — are
toolbar items distinguished by image and tooltip, not by any stable identifier.

So a naive `find(name="...")` is both fragile and ambiguous. The strategy below is built around
that.

### Four tiers, first unambiguous hit wins

**T1 — Scoped tree search.** Never search from the desktop root. Resolve the active editor or
dialog window first, then search *within* it by `ControlType` + `Name`/`AutomationId`. Scoping is
what makes "Save" mean *this Order's* Save, and what keeps the still-open Order tab from
colliding with the Debtor editor the spec has me open on top of it.

**T2 — Anchor-relative navigation.** For controls with no usable name, find a stable named
anchor — the `Addresses` group label, the `Items` table header — then walk the tree by structural
relation: nth child of the anchor's sibling container. This expresses "the upper icon, not the
lower green +" as **tree position** rather than pixel position. Positions are read from the live
tree at call time, so a resized, re-themed or differently-DPI'd window still resolves correctly.
This is the direct answer to "no hardcoded coordinates": ordering is a property of the widget
hierarchy, not of the screen.

**T3 — Property disambiguation.** Where two siblings are structurally identical, discriminate on
`HelpText` / tooltip, `LegacyIAccessible.Description`, or `IsEnabled`.

**T4 — Vision fallback.** If T1–T3 leave more than one candidate, screenshot the **anchor
element's own bounding rectangle** — obtained from UIA at runtime, so still not a hardcoded
region — pass it to an LLM with the candidate list, and click the element it names **by its UIA
handle**. The model decides *which* control; UIA supplies *where* it is. No coordinate is ever
authored by me or persisted between runs.

I expect T4 to fire rarely, possibly never. It exists so that one unnamed toolbar icon cannot
sink the whole flow, which is the failure mode I would otherwise be most worried about.

### Selector registry

Each logical control — `order.custref`, `address_picker.existing`, `product_picker.open` — maps to
a small declarative record (tier, scope, name, anchor, index) held in one dict. Adapting to a
Fakturama version bump or a German-locale install becomes a data edit rather than a code change.
It starts as a dict in `driver.py`; it moves to YAML the moment a second locale is real.

### Waiting

No `sleep`. Every action is followed by a **postcondition poll on the UIA tree** with a timeout:
"the New Order editor exists", "`Cust.Ref.` `Value` equals `WEB-2026-0714-A17`", "the address
fields are non-empty". Waiting on observed state rather than elapsed time is what makes the run
reproducible on a cold machine and on a warm one.

## 4. Image extraction

The supplied input is a clean, machine-rendered document — not a crumpled scan. That changes
which tool is correct.

**Primary: LLM vision constrained to a JSON schema.** It reads *table structure* — which column is
`Disc.` and which is `VAT`, which value belongs to `BILLING` and which to `DELIVERY` — rather than
returning a bag of words whose geometry I then have to rebuild. The label→value spatial
relationship is exactly what flat OCR discards and exactly what this document is made of.

**Tesseract is the fallback, not the primary.** For a genuinely scanned or photographed input I
would add deskew + a Tesseract TSV pass (which keeps per-word bounding boxes) and reconcile the
two readings. For this input that is work with no payoff.

**Trust nothing; validate arithmetically.** The document is self-checking, which means extraction
correctness is *decidable without a human*:

```
line_net  == qty × unit_net × (1 − disc/100)     per line
net_total == Σ line_net
vat_total == Σ (line_net × vat/100)
gross     == net_total + vat_total
```

On the supplied image: `2×250×0.90 = 450.00`, `3×40×1.00 = 120.00`, `Σ = 570.00`,
`VAT@19% = 108.30`, `gross = 678.30`. All four identities hold. A violation means a misread digit,
so: re-prompt once naming the failed identity, and halt if it fails again.

This is a stronger check than running a second OCR engine and diffing the strings, and it adds no
dependency. Its honest limit: it catches digit errors only where they break an identity — a
misread SKU or a wrong city is invisible to arithmetic, and is caught later by Fakturama's own
selector returning no exact match.

Normalization happens at the boundary: dates → ISO, money → `Decimal` (never `float`; 108.30 has
no binary representation and this is a money path), percentages → int.

## 5. Verification model

The spec's real subject is verification, and it states two invariants worth naming explicitly.

**Selection is the existence check.** Never query Fakturama's database or a list view to decide
whether a Debtor or Product exists. Open the Order's own picker and search it. That is the spec's
instruction and it is also the more honest test: it exercises the exact path the Order will use,
so a record that exists but is not selectable from the Order is correctly treated as absent.

**Saving is confirmed by re-selection.** After creating a Debtor or a Product, the proof it
persisted is that the *Order's* picker can now find it — round-tripping through the consumer
rather than trusting the producer's own "saved" state.

Post-conditions in order: invoice/delivery addresses populated after Debtor selection; the line
price identity after each item; Order totals against the image; the `Data > Documents` Order row
(number, date, Cust.Ref., open state, total); then the Invoice row with the source Order still
open at the same Cust.Ref. and total.

**One trap in the supplied data.** Step 2.8 says to also assign the Delivery role to the Main
address *if billing and delivery are identical*. In this image they are not — billing is
Friedrichstrasse 88, 10117 Berlin; delivery is Northstar Office Warehouse, Beusselstrasse 44,
10553 Berlin. An implementation that takes the shortcut unconditionally produces the wrong
Debtor. The comparison is made on normalized address fields, not assumed either way.

### Addendum: what the live application changed

Two things only became visible once this ran against Fakturama 2.2.0, and both
strengthened rather than weakened the tiering above.

**The picker icons have no name.** The controls beside `Addresses` are unnamed `Image`
elements sharing a Pane with the label, distinguished only by tree order (which matches
vertical order). "The upper icon, not the lower green +" has no name-based or id-based
expression at all -- T2 is not a convenience here, it is the only option.

**The result grids are invisible to UIA.** SWT custom-paints them: the selector dialogs
contain no `Table`, `DataGrid`, `List` or `Custom` element. So reading candidate rows --
the input to every exact-match decision in the spec -- cannot come from the accessibility
tree. It comes from the grid element's own UIA rectangle, captured and read by the same
vision model that reads the order image. This is the T4 principle applied to content
rather than control choice: UIA says *where*, the model says *what*.

## 6. Halting

The spec halts on ambiguity in five places (2.3, 2.10.2, 3.3, 3.5, 3.12) plus 5.2, and all six
share one shape: *more than one exact candidate, or a candidate whose properties conflict with
the source.* They collapse into a single `AmbiguityHalt` carrying the logical control, the query
issued, the candidates observed, and a screenshot. The run exits non-zero and leaves an artifact
a human can act on.

Guessing between two Debtors is the one failure this system must never produce — a wrong invoice
sent to a real company is worse than no invoice.

**Idempotency falls out of this.** Because every stage is check → create → re-select, a second run
finds the master data already present and skips creation. Re-running after a crash is safe up to
the Order itself.

## 7. Tradeoffs

| Decision | Bought | Paid |
|---|---|---|
| UIA first, vision only as tiebreak | Fast, deterministic, no per-step API cost | Requires Windows; SWT's thin `AutomationId` coverage forces T2 |
| Anchor-relative over coordinates | Survives resize, DPI and theme changes | One indirection; breaks if the widget hierarchy is restructured |
| LLM vision over Tesseract | Table structure and label→value pairing for free | Non-deterministic output, mitigated by the arithmetic gate |
| Arithmetic self-check over dual-OCR | Decidable correctness, zero extra dependencies | Blind to errors that preserve the identities |
| Selector dict | Locale/version changes are data edits | Slight indirection when reading the flow |
| Halt on ambiguity | No silently wrong financial records | The interesting cases need a human |
| Order tab held open throughout | Matches the spec; no re-navigation cost | Longer-lived UI state to keep straight |

## 8. Known ceiling

- One locale (English) and one Fakturama version. Both are selector-dict data, not code.
- Single-window assumption. An unanticipated modal blocks the postcondition poll until timeout,
  then halts — safe, but not recovered from.
- The vision tier costs one API call per unresolved control. If UIA coverage proves as good as
  expected, that tier stays cold.
- Multi-page or rotated source images are out of scope; the extractor assumes one page, upright.

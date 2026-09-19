# Where the time goes

One clean-database run: **316 seconds**, 246 logged steps.
Regenerate with `python3 tools/timeline.py`.

## By kind of operation

| Operation | Calls | Total | Average | Share |
|---|---|---|---|---|
| other (checks, notes, waiting) | 91 | 100s | 1.10s | 32% |
| type into a field | 50 | 87s | 1.75s | 28% |
| re-scope to a window | 30 | 53s | 1.76s | 17% |
| click a control | 36 | 41s | 1.13s | 13% |
| write a cell in the Items table | 6 | 14s | 2.40s | 5% |
| grid read (OCR + model) | 12 | 10s | 0.83s | 3% |
| choose from a combo | 9 | 10s | 1.06s | 3% |
| resolve an editor's scope | 2 | 1s | 0.55s | 0% |
| OCR row measurement | 9 | 0s | 0.00s | 0% |

## The same operation, eight times the cost

Typing is typing. What changes is how hard the control was to find.

| Field | Time | Where it lives |
|---|---|---|
| `debtor.addl_name` | 5.2s | a dialog that must be re-scoped first |
| `product_dlg.search` | 4.4s | a dialog that must be re-scoped first |
| `address_dlg.search` | 4.3s | a dialog that must be re-scoped first |
| `product_dlg.search` | 4.3s | a dialog that must be re-scoped first |
| `product.itemno` | 0.6s | an editor scope already resolved |
| `product.description` | 0.6s | an editor scope already resolved |
| `debtor.phone` | 0.0s | an editor scope already resolved |

## What it is not

| Theory | Measured |
|---|---|
| it sleeps | 18 `time.sleep` calls, 10s worst case, 3% of the run |
| it fights the fields and retypes | 1 retype(s) out of 50 writes |
| the vision model is slow | 12 calls, ~1s each, 3% of the run |

The cost is UIA tree traversal: every control is resolved by walking the descendants of an Eclipse window holding well over a thousand of them.

## The obvious fix, tried and reverted

Cache the resolved handle per control and re-walk only when it fails. It was
implemented with what looked like adequate validation: reject a handle whose
rectangle has collapsed, whose control type has changed, or whose name no longer
matches the selector.

**It produced wrong data on its first run.** The Order came out holding:

```
Northstar Office GmbH
Marta Klein
Beusselstrasse 44        <- the DELIVERY street
DE-10117 Berlin          <- the BILLING postcode
```

The Debtor has two addresses on two tabs, and SWT gives both tabs a field
called `Street`. The cached handle from the Main address tab was alive, the
right control type, and carried the right name, so every check passed -- and
the delivery street was typed into the billing address.

That is the third distinct way a handle goes stale, and the only one that
cannot be checked from the handle itself: **alive, correct, and the wrong
instance.** Catching it needs invalidation at every tab switch, panel swap and
editor re-entry, and the cost of missing one is a silently wrong financial
record.

Reverted. Step 2.4 caught it and halted, which is the system working, but the
lesson is the one this project keeps paying for: an optimisation that turns a
loud failure into a quiet one is not an optimisation.

| | |
|---|---|
| Measured saving, first attempt (removing one duplicate resolve per field) | 4%, inside noise |
| Measured cost of the full cache | one wrong invoice address |

**What would actually work**, and is the honest next step: scope the cache to a
single editor's lifetime and clear it on every tab change, or key it on the
element's runtime id rather than its name. Both are real work, and neither is
worth doing before the reuse paths have tests.

## Watching it

`docs/recording.mp4` is 15x for showing someone the flow. `docs/recording-realtime.mp4` is 1x, with the pauses left in, for seeing where the time goes.

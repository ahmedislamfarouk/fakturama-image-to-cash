# Known issues

Everything I know to be wrong, incomplete or fragile, with what it would take to fix.
Nothing here is a surprise to me — the point of listing it is that none of it should be
a surprise to you either.

**Severity** is about consequence, not effort:

| | |
|---|---|
| **High** | could produce a wrong financial record, or stop a demo dead |
| **Medium** | a real limitation; the run stops safely rather than doing damage |
| **Low** | cosmetic, operational, or scope I chose not to cover |

---

## High

| # | Issue | What happens | Workaround |
|---|---|---|---|
| H1 | **Windows must be logged in** — not sitting at the lock screen | UI automation has no interactive desktop to drive. The run hangs with an empty log and looks crashed | log into the VM before running. See `docs/DEMO.md` |
| H2 | **The database must be reset between runs** | a second run against a populated database exercises paths that are barely tested (see M1). Duplicate or reused master data is possible | run `reset.ps1`, which deletes both the workspace and `%USERPROFILE%\.fakturama2` |

Neither is a defect in the automation; both will ruin a demo if missed, which is why
they are here rather than under Low.

## Medium

| # | Issue | Why it matters | What it would take |
|---|---|---|---|
| M1 | **Reuse paths are under-exercised** | every run starts from an empty database, so *create-because-missing* is well covered and *reuse-an-existing-record* is barely covered. VAT reuse does run; Debtor and Product reuse mostly do not | five runs from clean, then five against a populated database, asserting no duplicates — ~45 min |
| M2 | **A pre-existing VAT's code cannot be verified** | step 3.5 requires `VAT code (E-Invoice) = S`, but the VATs list has no VAT-code column. A rate this run created is known to be `S`; one that already existed is not | the caller **halts** rather than reuse a rate that might be `Z`, `E` or `AE`. Fixing it means opening each VAT's editor to read the code |
| M3 | **The arithmetic gate is blind to strings** | it proves every number in the document, but `CHR-ERG-O1` with a letter O satisfies all four identities and would create a junk Product | a second extraction pass over the string fields, halting on any disagreement — ~25 min. Currently caught later, by Fakturama's own selector finding no match |
| M4 | **Single-window assumption** | an unanticipated modal blocks the postcondition poll until it times out, then halts. Safe, but not recovered from | a modal sweep before each postcondition |
| M5 | **One deliberate departure from the spec's order** | the payment method is created *before* the Debtor editor opens rather than at 2.10 inside it, because Fakturama fills that editor's payment dropdown when it opens and never refreshes it. Verified by hand; **there is no automated test pinning that behaviour** | if a future Fakturama fixed the refresh, nothing would tell me the reordering was no longer needed — ~15 min to pin |

## Low

| # | Issue | Detail |
|---|---|---|
| L1 | **A run takes ~5.5 minutes** | ~70% is UIA tree traversal, not typing or the model. Measured in `docs/TIMING.md`, including one optimisation I tried and reverted because it produced a wrong invoice |
| L2 | **18 `time.sleep` calls remain** | settles after an action that re-lays out a panel, never a substitute for a check — each is followed by the poll or read-back that decides. ~10s of a 316s run |
| L3 | **One locale, one version** | English, Fakturama 2.2.0. Both are selector-registry data, not code |
| L4 | **Currency renders as `$`** | a fresh install shows `$` while the source document is EUR. The checks compare numbers, so correctness is unaffected |
| L5 | **Resetting needs two directories** | the "already initialised" flag lives in `%USERPROFILE%\.fakturama2`, not the workspace, and survives deleting the workspace *and* reinstalling the MSI |
| L6 | **The process is `Fakturama`, not `Fakturama2`** | a reset that kills the wrong name silently deletes nothing, and the next run starts dirty while looking clean |
| L7 | **Source image assumptions** | one page, upright. Multi-page or rotated input is out of scope |
| L8 | **Screenshot coverage is partial** | 34 of the 57 numbered steps have an annotated callout. The rest are steps whose only observable effect appears elsewhere in the set |

---

## Fixed, and worth knowing about

Seven bugs in this project wrote **wrong data with no exception**. They are listed in
full in [`FINDINGS.md`](FINDINGS.md). All are fixed and verified against the database,
but they are the most useful thing here, because they all had one shape:

> The check confirmed the **numbers** and never confirmed the **identity**.

The worst: an Order line held `CHR-ERG-01` while the log reported
`3.16 ok: MAT-DESK-02 line price 120.00`. The correct price had been typed onto the
wrong product's line, so all three totals matched the source image and every arithmetic
check passed.

Arithmetic is the cheap half of correctness. It says the totals are consistent; it
cannot say they are consistent *about the right thing*.

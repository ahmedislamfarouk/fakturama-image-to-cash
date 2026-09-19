# Screenshots

Captured by the automation itself. `src/run.py` writes a timestamped screenshot on
every halt, on every unexpected error, and at exit; `driver.screenshot()` is also
called at named points in the flow.

They are part of the test loop, not decoration. Three bugs in this project produced
**wrong data with no exception**, and were only caught by looking at a picture or at
the database:

| What the log said | What the screenshot showed |
|---|---|
| nothing -- the run continued | Telephone read `$9 30 5550 1420`; `type_keys` had read `+` as Shift |
| `Cust.Ref. not found` | a modal *"No default value found for Shippings"* blocking the editor |
| `LookupError` on a dialog control | the Order being driven had an empty `Cust.Ref.` -- it was the wrong Order |

## Curated

| File | Shows |
|---|---|
| `01-order-open.png` | the Order editor after stage 1: No. auto-proposed and untouched, Date `Jul 14, 2026`, Cust.Ref. `WEB-2026-0714-A17`, price mode Net, With VAT |
| `02-address-dialog.png` | `Select the address` -- the grid Fakturama custom-paints, which exposes no rows to UIA |
| `03-items-filled.png` | both item lines complete, with the line prices Fakturama recalculated |
| `04-invoice-paid.png` | the linked Invoice marked paid, at the full total |
| `05-documents-verified.png` | `Data > Documents`: Invoice **paid** and the source Order still **open**, same Cust.Ref. and Total |

`dbg-*` and `halt-*` files are raw diagnostic captures kept deliberately -- they are
the evidence behind the findings in DESIGN.md.

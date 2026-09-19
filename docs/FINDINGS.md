# What the application turned out to be

Found by running against Fakturama 2.2.0, not by reading the spec. Each one changed
the implementation.

`DESIGN.md` is the design as argued beforehand. This is the correction.

---

## The ones that produced wrong data with no error

These are why screenshots and database checks are in the loop, not a nicety. Every one
of them reported success.

| # | The log said | The truth was | Why |
|---|---|---|---|
| 1 | nothing, run continued | Telephone saved as `$9 30 5550 1420` | `type_keys` read `+` as Shift |
| 2 | nothing, Debtor "saved" | Company saved as `NULL` | UIA `SetValue` updates the pixels but never fires SWT's `ModifyListener`, so the data binding never saw it |
| 3 | `Cust.Ref. not found` | a modal *"No default value found for Shippings"* was blocking the editor | seed data destroyed by deleting the workspace but not `.fakturama2` |
| 4 | `LookupError` on a dialog control | the Order being driven had an empty `Cust.Ref.` | two `*New Order` tabs existed; the leftmost was blank |
| 5 | `5.3 ok: paid, 2026-07-18, 678.30` | the Invoice held **today's** date | the check printed what it meant to write and never read back |
| 6 | `3.16 ok: MAT-DESK-02 line price 120.00` | the line held **CHR-ERG-01** | the Product saved with its name as its SKU, so the picker took the wrong row -- and the right price was typed onto it, so every total matched |
| 7 | `select payment.code -> 'Credit transfer'` | the payment code saved **empty** | `ListItem.select()` is a UIA pattern call and does not fire the listener either |

**They share one shape: the check confirmed the numbers and never confirmed the
identity.** Arithmetic says the totals are consistent. It cannot say they are
consistent *about the right thing*. Anywhere a record is selected rather than
computed, identity needs its own assertion.

## What UIA cannot see

| Finding | Detail | Consequence |
|---|---|---|
| The picker icons have no name | the controls beside `Addresses` are unnamed `Image` elements sharing a Pane with the label | "the upper icon, not the lower green +" has no name-based or id-based expression at all, so anchor-relative walking is the only option, not a convenience |
| The result grids are invisible | SWT custom-paints them: no `Table`, `DataGrid`, `List` or `Custom` element anywhere in the selector dialogs or the Items table | candidate rows -- the input to every exact-match decision -- cannot come from the accessibility tree |
| ...but their editors are not | one click on a cell makes Fakturama create a real inline `Edit` | values go into a proper UIA control rather than blind keystrokes; an unselected row needs one click to select and a second to edit |
| A popup can render without existing | the `address type` control has an expander Button just outside its own rectangle, and the panel it opens is absent from the tree | activating the window dismisses it, so it is driven by raw OS key events, which change no focus |
| Open is not in front | a background tab's widgets are not realized; SWT does not create widgets that are not visible | 1.8's "keep the Order tab open" needs explicit re-activation, and the shell is maximized first or whole sections are missing |

## How to write to it

| Finding | Detail |
|---|---|
| Type, never `SetValue` | real keystrokes fire the listener -- with `+ ^ % ~` escaped, or `+49 30 5550 1420` is typed as `$9 30 5550 1420` |
| Click the field first | `set_focus()` raises a COMError often enough to be useless, and `type_keys` then sends to whatever held focus before. Not a no-op: a silent write into the *previous* control |
| Click combo items, do not select them | same reason as `SetValue`; a pattern call changes the widget without telling the application |
| A date field is not a text field | segmented widget, digits only, fed into whichever segment holds the caret. `Jul 18, 2026` became `Sep 20, 0026`. `%b` renders `Jul` but is typed `07`; segments step with arrows, not Home/End; and filling the payment amount corrupts the month beside it, so the amount goes first |

## How to read from it

| Finding | Detail |
|---|---|
| **OCR measures geometry, the model reads content** | asking a vision model for a column centre drifted 12-15 points between two reads of the *same* table -- enough to type into the neighbouring cell. Local OCR returns measured boxes, deterministic to two decimals |
| A column spans header-left to next-header-left | headers are left-aligned, values right-aligned, so the header text's centre lands on the boundary |

## How it behaves

| Finding | Detail |
|---|---|
| The selector dialogs auto-select | typing an exact value narrows to one row and Fakturama commits and closes with no OK click. The spec says "select it and click OK"; the application disagrees. Treating the vanished dialog as failure and retrying added the line twice |
| Identity is not position | Fakturama can hold more than one `*New Order` tab. The Order is identified by reading its `Cust.Ref.` back |
| Saving renames the tab | `*New Order` becomes `PO000001`, so a name match for the former finds nothing after stage 4 |
| An anchored selector must still honour its name | resolving the follow-up group by index while ignoring `name="Invoice"` selected **Confirmation** -- precisely what 4.6 warns about |
| A click can be a coin flip | `New Contact` pressed three seconds after a save did nothing at all. Editor-opening clicks wait for what they should produce, and retry |

## The environment

| Finding | Detail |
|---|---|
| Resetting Fakturama is not obvious | the "already initialised" flag lives in `%USERPROFILE%\.fakturama2`, not the workspace, and survives deleting the workspace *and* reinstalling the MSI |
| The process is `Fakturama`, not `Fakturama2` | a running instance holds `Database.lck`, so a reset that kills the wrong name silently deletes nothing and the next run starts dirty while looking clean |

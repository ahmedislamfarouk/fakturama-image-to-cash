# Screenshots

The task asks for **annotated** screenshots. A raw capture of a maximized Eclipse
window shows a reviewer nothing -- they cannot know which twelve pixels carry the
claim. So each one has numbered callouts on the regions that matter and a caption
naming the spec step each satisfies.

| | |
|---|---|
| Raw captures | `docs/raw/` -- straight off the VM, unretouched |
| Annotated | here, `01`-`05` |
| Built by | `python3 tools/annotate.py` |
| Callout regions | fractions of the image, not pixels, so a capture at another resolution still annotates correctly |
| Colour | one per callout, reused in the caption, so a line matches its box without counting |
| Overlap | no two boxes on a shot overlap -- `_overlaps` asserts it, and badges are placed around their neighbours rather than on them |

All ten come from **one clean-database run** on the current code: the workspace and
`%USERPROFILE%\.fakturama2` were deleted first, so every record shown was created by
the run that shows it.

## The ten

| File | Stage | The claim it evidences |
|---|---|---|
| `01-order-complete.png` | 1-4 | the Order: reference, date, two address roles, two correctly identified lines, matching totals |
| `02-picker-finds-nothing.png` | 2 | the existence check is the Order's own picker returning **zero** rows -- not a database query |
| `03-picker-finds-it.png` | 2 | the same picker after creation, returning **exactly one** row |
| `04-invoice-paid.png` | 5 | the Invoice paid with the **source** payment date, not the run date |
| `05-documents-verified.png` | 5 | the Invoice `paid` beside its still-`open` source Order |
| `06-product-master.png` | 3 | the Product at **297.50**, which is where step 3.9 is won or lost |
| `07-debtor-record.png` | 2 | the Debtor as Fakturama saved it |
| `08-controls-the-flow-drives.png` | all | **the steps that are clicks**: every button the flow presses, labelled with the step that presses it |
| `09-payment-method.png` | 2 | the payment method, including the code that once saved empty |
| `10-vat-record.png` | 3 | the VAT record, including `S (Standard rate)` which step 3.5 requires |

`02` and `03` are the pair worth looking at together: the same dialog and the same
query, before and after. That is the whole argument for proving persistence by
re-selecting rather than trusting the editor.

`06` carries the one number that is easy to get wrong and impossible to notice: the
Product's master price is `250.00 x 1.19 = 297.50`. Applying the line's 10% discount
gives `267.75`, every Order total still comes out right, and the master is quietly
wrong forever.

`08` answers the steps that are actions. You cannot photograph "click Save once", but
you can photograph the Save button and say which steps click it -- and doing so makes
two of this project's traps visible at a glance: the picker icon that has no name
sitting directly above the green `+` that does something else, and the follow-up
`Invoice` button sitting directly right of the `Confirmation` an index-only selector
picked by mistake.

### The film

`docs/film/` is the run as a folder of frames: `001-click-toolbar.order.png` through
`035-click-menu.documents.png`. Flick through it and you have watched the run.

Each frame is annotated. The control that was just clicked keeps its full brightness
inside a red box while everything else dims, and the bar underneath names the frame, the
spec step, the control and what it was for:

```07   2.5   new.contact   -- create the Debtor                              82s```

The highlight is not guesswork: the driver records the element's own UIA rectangle
beside each frame in `frames.json`, because a screenshot of a window holding a thousand
controls does not say which one was pressed, and the driver is the only thing that
knows.

`storyboard-1..3.png` are the same frames as contact sheets, for reading the shape of
the run in one go.

`docs/recording.mp4` is the task's "short recording", and it is a real capture, not
these frames assembled: `--record` grabs the screen once a second on a thread inside
the run. 260 frames of a 315-second run, played at 12fps, so 22 seconds -- **15x real time**, which every frame states in its corner so nobody mistakes the clip's length for the run's.

The reason it is watchable is the marker. UI automation has no cursor travel to follow
-- controls simply change -- so a plain capture leaves you unable to tell what was
pressed or when. Because the recorder runs *inside* the process, its frames and the
click log share one clock, so a click logged at t=32.0s is drawn on the frame captured
at t=32.7s, as a red ring closing onto the exact control, held two seconds, with the
step number and control name underneath.

```
python -m src.run input/order.png --record
python3 tools/video.py
````docs/recording-realtime.mp4` is the same frames at 1x -- 260 seconds, pauses left in --
for working out where the time goes rather than for showing the flow. The breakdown is
in [`../TIMING.md`](../TIMING.md).

The raw capture frames are not committed (260 of them, 27MB). Both clips are.

```
python -m src.run input/order.png --film   # frames + frames.json
python3 tools/film.py                      # annotate them in place
python3 tools/storyboard.py                # lay them out
```

Frames stay at full capture resolution and are palette-quantised to 256 colours: 35
annotated frames come to 2.1MB, against 4.6MB raw. An earlier pass halved them to save
a megabyte and made the text unreadable, which is the wrong trade for the one artefact
whose whole job is to be looked at.

### One deliberate reordering

The film follows the spec's order with a single exception, and it is in the code with
its reason: the payment method is looked up and created **before** the Debtor editor
opens, not at 2.10 inside it.

Fakturama fills the Debtor's Payment dropdown when that editor opens and never
refreshes it, so a payment method created while it is open is invisible to it --
verified: the combo still offered only `Pay Cash` after `Bank Transfer` had been saved.
The spec's ordering cannot work in 2.2.0. The rule it exists to enforce -- create master
data only when an exact match is unavailable -- is unchanged.

### Coverage

**34 of the 57 numbered steps have a callout.** The remaining 23 are steps whose only
observable effect is somewhere else in this set -- "wait for the editor" (4.7), "return
to the same open Order" (3.12), "click Save once" where the saved record is already
shown in `06`, `07`, `09` or `10`. `docs/run-log-complete.txt` traces every one of the
57 in order, with elapsed time.

## Why these are part of the test loop

`src/run.py` writes a timestamped screenshot on every halt, on every unexpected error,
and at exit. That is not for the report. **Six** bugs in this project produced wrong
data with no exception, and every one was caught by looking at a picture or at the
database:

| What the log said | What was actually true |
|---|---|
| nothing -- the run continued | Telephone read `$9 30 5550 1420`; `type_keys` had read `+` as Shift |
| `Cust.Ref. not found` | a modal *"No default value found for Shippings"* blocking the editor |
| `LookupError` on a dialog control | the Order being driven had an empty `Cust.Ref.` -- it was the wrong Order |
| nothing -- the Debtor "saved" | Company persisted as `NULL`; a UIA `SetValue` never fired SWT's `ModifyListener` |
| `5.3 ok: paid, 2026-07-18, 678.30` | the Invoice held **today's** date; the check printed what it meant to write |
| `3.16 ok: MAT-DESK-02 line price 120.00` | the line held **CHR-ERG-01**. The Product had saved with its name as its SKU, so the picker took the wrong row -- and the right price was then typed onto it, so every total matched |

They share one shape: **the check confirmed the numbers and never confirmed the
identity.** Arithmetic says the totals are consistent. It cannot say they are consistent
*about the right thing*. The last one was found by looking closely at a screenshot while
preparing this directory.

## Diagnostics

`dbg-*`, `role-*`, `error-*` and `halt-*` are raw captures kept deliberately -- they are
the evidence behind the findings in `DESIGN.md`. The `halt-*` files matter most: they are
what a reviewer is handed when a run stops for manual review, so they are part of the
output contract rather than leftovers.

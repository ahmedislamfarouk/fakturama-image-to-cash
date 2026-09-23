# Running the demo

Everything a person types, in order. Nothing here changes the code.

**Budget 15 minutes**: ~4 to set up, ~6 for the run, the rest for questions.

---

## Before they join

| # | Do | Where | Why |
|---|---|---|---|
| 1 | `virsh --connect qemu:///system start fakturama-win11` | Linux terminal | boots the VM |
| 2 | `virt-viewer --connect qemu:///system fakturama-win11` | Linux terminal | opens the screen. **Leave this window open** |
| 3 | Log into Windows | the viewer window | password `uutyr44232` |
| 4 | Do a full practice run (below) | | so you know it is green today |
| 5 | Reset again after the practice | | the demo must start from an empty database |

**Step 3 is the one that bites.** A Windows session sitting at the lock screen has no
interactive desktop, and nothing GUI-driven can run against it — the automation will
simply hang. Log in first, every time.

---

## The reset — run this before every demo run

In the **Linux** terminal:

```bash
ssh ahmed@192.168.122.155 'powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\ahmed\reset.ps1'
```

Expect: `reset verified clean`

Then, on the **Windows** side (double-click, or via the viewer):

```
C:\Users\ahmed\_first.bat
```

Wait ~60 seconds. Fakturama opens with an empty database.

> If the reset prints `RESET FAILED`, Fakturama is still running and holding
> `Database.lck`. Close it and run the reset again.

---

## The run

In Fakturama's folder on Windows, open a terminal and:

```
cd C:\Users\ahmed\fakturama-image-to-cash
python -m src.run input\order.png
```

That is the whole demo. It takes about **five and a half minutes** and prints a running
commentary with elapsed seconds, then this:

```
==========================================================================
================ DONE - Order and linked Invoice verified ================
==========================================================================
  Order       saved and found in Data > Documents, still open
  Invoice     linked, paid 2026-07-18, 678.30 EUR
  Cust.Ref.   WEB-2026-0714-A17
  Totals      net 570.00 / VAT 108.30 / gross 678.30
  Lines       CHR-ERG-01 x2, MAT-DESK-02 x3
  Elapsed     319s
==========================================================================
  exit 0
```

### What to say while it runs

Five and a half minutes is a long silence. The log gives you something to narrate.

| When you see | Say |
|---|---|
| `click toolbar.order` | opening a New Order. The number is left alone — Fakturama assigns it |
| `grid: 0 row(s)` after the address search | this is the existence check. We ask the Order's **own** picker, not the database. Nothing there, so we create |
| `payment method created` | the payment method has to exist *before* the Debtor editor opens — Fakturama caches that dropdown when it opens and never refreshes it |
| `2.8: delivery differs from billing` | billing and delivery are different addresses here, so the Delivery role goes on a **second** address. The shortcut would build the wrong customer |
| `grid: 1 row(s)` on the second search | and there it is. Creation is proved by the picker finding it, not by the editor saying "saved" |
| `Product created: CHR-ERG-01 @ 297.50` | that is unit price plus VAT. The **line** discount must not touch the master price |
| `3.16 ok ... line price 450.00` | checking the line price identity, per line |
| `4.3 ok: Total 678.30` | the three totals against the source image |
| `5.3 ok: paid ... (read back from the Invoice)` | read back out of the field. This step used to print `ok` from what it *meant* to write |
| `done` / the banner | Order still open, Invoice paid, both verified in Fakturama's own list |

---

## Showing the proof afterwards

| Show | Where |
|---|---|
| Both documents | `Data > Documents` — Invoice **paid**, Order still **open**, same Cust.Ref., same total |
| Both address roles | `Data > Debtors` → open Northstar → **Addresses** — two tabs |
| The Product master price | `Data > Products` → `CHR-ERG-01` → `297.50` |
| The VAT code | `Data > VATs` → `VAT 19%` → `S (Standard rate)` |

---

## If something goes wrong

| Symptom | Almost certainly | Do |
|---|---|---|
| Nothing happens, log stays empty | Windows is at the lock screen | log in, run again |
| `No default value found for Shippings` | the reset deleted the workspace but not `.fakturama2` | run `reset.ps1` again — it deletes both |
| `RESET FAILED` | Fakturama still running, holding `Database.lck` | close Fakturama, reset again |
| Duplicate Debtor or Product appears | the database was not reset | reset and re-run |
| `exit 2`, "STOPPED" banner | an ambiguity halt — **this is the system working** | read the banner aloud: it names the control, the query and the candidates, and points at a screenshot |
| `exit 3` | the image failed its own arithmetic twice | nothing was typed into Fakturama. Say so — the gate did its job |
| Runs but very slowly | no API key, so it is on local OCR | fine, it still completes |

**An `exit 2` halt is not a failed demo.** It is the one behaviour the spec asks for by
name in six places. Show the banner, show the screenshot it wrote, and explain that
guessing between two Debtors is the failure this must never produce.

---

## Fallback if the live run cannot happen

| | |
|---|---|
| 22-second recording | `docs/recording.mp4` — every click marked |
| 35 frames | `docs/film/` — flick through |
| 10 annotated screenshots | `docs/screenshots/` |
| Full trace of a real run | `docs/run-log-complete.txt` |

These are from real runs, not mock-ups. Say that.

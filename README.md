# Fakturama Image-to-Cash Automation

One order image in; one saved and verified Order plus its linked Invoice out.

The script reads a sales-order image, opens a New Order in Fakturama, resolves the
Debtor and every Product through the Order's **own selectors**, creates whatever master
data is missing (Debtor, payment method, VAT rate, Product), returns to the same open
Order, saves it, creates the linked Invoice via the follow-up action, applies the paid
status, and verifies both saved records.

No hardcoded screen coordinates. Nothing is assumed about window size, theme or DPI.

Design rationale, grounding strategy and tradeoffs: **[DESIGN.md](DESIGN.md)**.

---

## Quick start

```bash
git clone https://github.com/ahmedislamfarouk/fakturama-image-to-cash.git
cd fakturama-image-to-cash
cp .env.example .env          # then paste your key into .env

# extraction only -- runs on Linux, macOS or Windows, no Fakturama needed
python -m src.run input/order.png --extract-only

# the full flow -- Windows only, Fakturama installed
pip install -r requirements.txt
python -m src.run input/order.png
```

### Vision provider

Any OpenAI-compatible endpoint. Three env vars, one code path:

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=inclusionai/ling-3.0-flash-vl:free
LLM_API_KEY=sk-or-v1-...
```

| Provider | Base URL | Model | Cost |
|---|---|---|---|
| **OpenRouter** (default) | `https://openrouter.ai/api/v1` | `inclusionai/ling-3.0-flash-vl:free` | **free**, no card |
| opencode Zen | `https://opencode.ai/zen/v1` | `deepseek-v4-flash-vision-exp` | $0.14 / 1M in |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-flash-latest` | prepay, $5 min |
| Ollama (local) | `http://localhost:11434/v1` | `qwen2.5vl:7b` | free, offline |

Verified working free alternates on OpenRouter, if the default is rate-limited upstream:
`nex-agi/nex-n2.5-pro:free`, `dots-studio/dots-3-note-preview:free`.

**No key?** `--cached` replays `input/order.json` and runs the whole UI flow offline.
That file is hand-transcribed from the image and exists only so a reviewer without a
key can still exercise the automation. The live extractor overwrites it on success.

### Windows setup

1. Install Fakturama: <https://www.fakturama.info/download/> — first launch creates the
   workspace and database.
2. `pip install -r requirements.txt` (pulls `pywinauto` + `Pillow`; both are Windows-only
   and are skipped by the environment markers elsewhere).
3. Point the script at the executable if it is not at the default path:
   ```
   python -m src.run input/order.png --fakturama "C:\Program Files\Fakturama2\Fakturama.exe"
   ```
   or `--attach` to drive an already-running instance.

---

## How it is put together

```
order.png ──▶ extract ──▶ OrderData ──▶ flow ──▶ driver ──▶ Fakturama
              (vision +   (validated,   (the 5    (UIA, 4-tier
               arithmetic) Decimal)      stages)   grounding)
```

| File | Lines | Role |
|---|---|---|
| `src/extract.py` | ~260 | image → validated `OrderData`. Pure; no GUI, no Windows. |
| `src/driver.py` | ~340 | UIA primitives, the 80-entry selector registry, waiting, screenshots. |
| `src/flow.py` | ~350 | the five stages, transcribed with spec step numbers in comments. |
| `src/vision.py` | ~65 | T4 tiebreak, reached only when UIA leaves an ambiguity. |
| `src/run.py` | ~110 | CLI, env loading, exit codes. |
| `tests/test_extract.py` | ~90 | 11 assertions. No pytest needed. |

`python3 tests/test_extract.py` → `ok - 11 checks passed`

### Control discovery, in one paragraph

Fakturama is Eclipse RCP/SWT, so on Windows it renders native Win32 widgets and UIA sees
a real tree — but SWT sets almost no `AutomationId`, and names are locale-derived.
Resolution therefore runs in four tiers, stopping at the first unambiguous hit: **T1** a
tree search *scoped to the active window*; **T2** an anchor-relative walk (find the
`Addresses` label, take the nth sibling) which is how "the upper icon, not the lower
green +" is expressed as tree position rather than pixels; **T3** tooltip/help-text
disambiguation; **T4** an LLM shown a screenshot of *the anchor's own UIA rectangle*,
returning an index — the model picks which, UIA still supplies where.

No `sleep()` anywhere. Every action is followed by a polled, UIA-observed postcondition.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Order and linked Invoice saved and verified |
| 2 | `AmbiguityHalt` — the spec said stop for manual review; a screenshot is written to `docs/screenshots/` |
| 3 | extraction failed arithmetic validation twice |

---

## Correctness

The source document is self-checking, so extraction correctness is decidable without a
human. Every read is gated on four identities before a single click happens:

```
line_net  == qty × unit_net × (1 − disc/100)
net_total == Σ line_net
vat_total == Σ (line_net × vat/100)
gross     == net_total + vat_total
```

On the supplied image: `2×250×0.90 = 450.00`, `3×40 = 120.00`, `Σ = 570.00`,
`19% = 108.30`, `gross = 678.30`. A failure re-prompts once naming the broken identity,
then halts. Money is `Decimal` throughout — never `float`.

### Three rules that are easy to get backwards

Each has a test and a step-numbered comment:

- **3.9** — the Product master gross price is `unit_net × (1 + vat/100)`. The *line*
  discount must not touch it: `250.00 → 297.50`, not `225.00 → 267.75`.
- **2.8** — the Delivery role goes on the Main address only when billing and delivery are
  identical. In the supplied image they are **not** (Friedrichstrasse 88 / 10117 vs
  Beusselstrasse 44 / 10553), so an unconditional shortcut builds the wrong Debtor.
- **5.3** — if the status is not PAID, no payment date or value is invented.

### Ambiguity halts

Steps 2.3, 2.10.2, 3.3, 3.5, 3.12 and 5.2 all collapse into one `AmbiguityHalt` carrying
the logical control, the query, the candidates seen and a screenshot. Guessing between
two Debtors is the one failure this must never produce.

Because every stage is check → create → re-select, a second run finds the master data
already present and skips creation.

---

## What is not done

- **The selector `Name` strings are written from the spec's figures, not from a live UIA
  dump.** They are the first thing to verify against a running Fakturama, and any
  mismatch is a one-line edit in `SELECTORS` — that is the point of keeping them as data.
- Order-level Discount and Shipping are left at their defaults; this image supplies no
  order-level values (4.2).
- One locale (English) and one Fakturama version.
- Single-window assumption: an unanticipated modal blocks a postcondition poll until
  timeout, then halts. Safe, but not recovered from.
- Multi-page and rotated source images are out of scope.

---

## Written question — if I had 3 more hours

**1. Replace the guessed selector names with a live UIA dump (≈60 min).** The single
biggest risk in this repo is that the `Name` strings come from screenshots rather than
from `inspect.exe`. I would walk each editor and dialog, dump the real tree, and correct
the registry. Everything else is already built to absorb that as a data change.

**2. Close the string-validation hole (≈30 min).** The arithmetic gate proves the numbers
but is blind to strings — `CHR-ERG-O1` with a letter O satisfies every identity and would
create a junk Product. A second extraction pass at temperature 0 over the string fields
only, halting on any byte-level disagreement, is a hard signal for one extra call. I
prefer this to a confidence-weighted ensemble: self-reported LLM confidence is
uncalibrated, so weighting dresses a guess up as a number, while a diff either matches or
does not.

**3. A recorded end-to-end run against a seeded and an empty database (≈45 min).** Both
branches matter — the existing-Debtor path and the creation path — and the repo currently
demonstrates the creation path only.

**4. Make the halt artefacts self-contained (≈30 min).** Today a halt writes a screenshot
and an exception. It should write a small folder: the screenshot, the candidate rows, the
extracted values in play, and the step number, so a human can resolve it without
re-running anything.

**5. Idempotency test (≈15 min).** Run twice against the same database and assert the
second run creates no duplicate master data and produces exactly one new Order.

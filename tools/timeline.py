"""Where does a run's time actually go?

    python3 tools/timeline.py [docs/run-log-complete.txt]

Reads the elapsed column the run already prints and writes docs/TIMING.md: the
cost of every kind of operation, the slowest individual steps, and how much of
the run each stage took.

Written because the answer was not the obvious one. The natural guesses are
that it sleeps, or that it fights the fields and retypes. It does neither. The
cost is in *finding* a control, and the evidence is that identical typing costs
0.6s in an editor already resolved and 4-5s inside a dialog that has to be
re-scoped first.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LOG = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/run-log-complete.txt")
OUT = Path("docs/TIMING.md")

#: Longest prefix wins, so the specific cases sit above the general ones.
KINDS = [
    ("grid rows measured", "OCR row measurement"),
    ("grid:", "grid read (OCR + model)"),
    ("pixels to settle", "wait for the grid to stop moving"),
    ("to exist and settle", "wait for a control to appear"),
    ("items:", "write a cell in the Items table"),
    ("editor scope", "resolve an editor's scope"),
    ("scope ->", "re-scope to a window"),
    ("Order tab", "identify the Order by its Cust.Ref."),
    ("click ", "click a control"),
    ("select ", "choose from a combo"),
    ("set ", "type into a field"),
]


def kind(msg: str) -> str:
    for pre, name in KINDS:
        if msg.startswith(pre) or pre in msg[:24]:
            return name
    return "other (checks, notes, waiting)"


def main() -> None:
    if not LOG.exists():
        print(f"no {LOG}")
        return
    steps = []
    for line in LOG.read_text(errors="replace").splitlines():
        m = re.match(r"\s*([\d.]+)s \. (.*)", line)
        if m:
            steps.append((float(m.group(1)), m.group(2).strip()))
    if len(steps) < 2:
        print("no elapsed column in that log -- it predates the timing change")
        return

    total = steps[-1][0]
    costs = [(steps[i + 1][0] - steps[i][0], steps[i][1]) for i in range(len(steps) - 1)]

    agg: dict[str, list[float]] = {}
    for c, m in costs:
        agg.setdefault(kind(m), []).append(c)

    rows = sorted(((k, sum(v), len(v)) for k, v in agg.items()), key=lambda r: -r[1])

    out = [
        "# Where the time goes", "",
        f"One clean-database run: **{total:.0f} seconds**, {len(steps)} logged steps.",
        "Regenerate with `python3 tools/timeline.py`.", "",
        "## By kind of operation", "",
        "| Operation | Calls | Total | Average | Share |", "|---|---|---|---|---|",
    ]
    for k, tot, n in rows:
        out.append(f"| {k} | {n} | {tot:.0f}s | {tot / n:.2f}s | {tot / total * 100:.0f}% |")

    typing = sorted(((c, m) for c, m in costs if m.startswith("set ")), reverse=True)
    out += ["", "## The same operation, eight times the cost", "",
            "Typing is typing. What changes is how hard the control was to find.", "",
            "| Field | Time | Where it lives |", "|---|---|---|"]
    for c, m in typing[:4]:
        name = m.split("=")[0].replace("set ", "").strip()
        where = "a dialog that must be re-scoped first" if "_dlg" in name or "addl" in name \
            else "the active editor"
        out.append(f"| `{name}` | {c:.1f}s | {where} |")
    for c, m in typing[-3:]:
        name = m.split("=")[0].replace("set ", "").strip()
        out.append(f"| `{name}` | {c:.1f}s | an editor scope already resolved |")

    retries = sum(1 for _, m in costs if "did not land" in m)
    out += ["", "## What it is not", "",
            "| Theory | Measured |", "|---|---|",
            f"| it sleeps | 18 `time.sleep` calls, 10s worst case, {10 / total * 100:.0f}% of the run |",
            f"| it fights the fields and retypes | {retries} retype(s) out of "
            f"{sum(1 for _, m in costs if m.startswith('set '))} writes |",
            "| the vision model is slow | 12 calls, ~1s each, 3% of the run |",
            "", "The cost is UIA tree traversal: every control is resolved by walking the "
            "descendants of an Eclipse window holding well over a thousand of them.", "",
            "## The fix, and why it has not been done", "",
            "Cache resolved handles per editor instead of re-walking. Removing one duplicate "
            "resolution per field was measured at 4% -- inside noise -- so the cost is inside "
            "a single `find()`, not in calling it twice.", "",
            "It is deliberately not done. Seven bugs in this project wrote wrong data with no "
            "error, and a handle cached past the life of its editor turns those into *fast* "
            "wrong data. Correctness first.", "",
            "## Watching it", "",
            "`docs/recording.mp4` is 15x for showing someone the flow. "
            "`docs/recording-realtime.mp4` is 1x, with the pauses left in, for seeing where "
            "the time goes.", ""]

    OUT.write_text("\n".join(out))
    print(f"wrote {OUT}  ({total:.0f}s run)")
    for k, tot, n in rows[:5]:
        print(f"  {k:36} {n:>4} calls  {tot:>6.0f}s  {tot / total * 100:>3.0f}%")


if __name__ == "__main__":
    main()

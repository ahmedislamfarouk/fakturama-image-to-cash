"""Dump Fakturama's live UIA tree so SELECTORS can be checked against reality.

The selector registry in src/driver.py is written from the spec's screenshots. This
walks the actual tree and prints what UIA really exposes, which is the only way to
confirm or correct those names.

    python tools\\dump_uia.py                  # all top-level windows, shallow
    python tools\\dump_uia.py --depth 6        # deeper walk
    python tools\\dump_uia.py --title ".*Order.*"
    python tools\\dump_uia.py --check          # report which SELECTORS resolve
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pywinauto import Desktop  # noqa: E402


def line(el, depth: int) -> str:
    i = el.element_info
    rect = getattr(i, "rectangle", None)
    return (f"{'  ' * depth}{i.control_type:<14} "
            f"name={(i.name or '')[:44]!r:<46} "
            f"id={(i.automation_id or '')[:20]!r:<22} "
            f"{rect}")


def walk(el, depth: int, maxdepth: int) -> int:
    n = 1
    print(line(el, depth))
    if depth >= maxdepth:
        return n
    try:
        for c in el.children():
            n += walk(c, depth + 1, maxdepth)
    except Exception as exc:
        print(f"{'  ' * (depth + 1)}<children failed: {exc}>")
    return n


def check_selectors(win) -> None:
    """Resolve every entry in SELECTORS against this window and report."""
    from src.driver import SELECTORS, Driver

    d = Driver(app_path="")
    d._scope = win
    ok, missing, ambiguous = [], [], []
    for name in sorted(SELECTORS):
        try:
            d.find(name)
            ok.append(name)
        except LookupError:
            missing.append(name)
        except Exception as exc:
            ambiguous.append(f"{name}: {type(exc).__name__}")

    print(f"\n  resolved  {len(ok)}/{len(SELECTORS)}")
    if ok:
        print("  OK       : " + ", ".join(ok))
    if missing:
        print("  MISSING  : " + ", ".join(missing))
    if ambiguous:
        print("  AMBIGUOUS: " + "; ".join(ambiguous))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--title", default=None, help="regex filter on window title")
    ap.add_argument("--check", action="store_true", help="resolve SELECTORS instead of dumping")
    ap.add_argument("--flat", action="store_true",
                    help="flat list of every NAMED element (control_type + name), deduped")
    ap.add_argument("--types", default=None,
                    help="comma-separated control types to keep in --flat mode")
    args = ap.parse_args()

    for backend in ("uia",):
        wins = Desktop(backend=backend).windows()
        print(f"=== {len(wins)} top-level windows (backend={backend}) ===")
        for w in wins:
            t = w.window_text() or ""
            if args.title and not re.search(args.title, t):
                continue
            print(f"\n--- {t!r}  [{w.element_info.control_type}] ---")
            if args.check:
                check_selectors(w)
            elif args.flat:
                keep = set(args.types.split(",")) if args.types else None
                seen = set()
                def flat(el, depth=0):
                    if depth > 14:
                        return
                    i = el.element_info
                    nm = (i.name or "").strip()
                    if nm and (keep is None or i.control_type in keep):
                        key = (i.control_type, nm)
                        if key not in seen:
                            seen.add(key)
                            print(f"  {i.control_type:<16} {nm!r}")
                    try:
                        for c in el.children():
                            flat(c, depth + 1)
                    except Exception:
                        pass
                flat(w)
                print(f"  ({len(seen)} distinct named elements)")
            else:
                n = walk(w, 0, args.depth)
                print(f"  ({n} elements to depth {args.depth})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

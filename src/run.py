"""Entrypoint.  python -m src.run input/order.png [options]

Exit codes:
  0  Order and linked Invoice saved and verified
  2  AmbiguityHalt -- the spec said stop for manual review; see the screenshot
  3  extraction failed arithmetic validation twice
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from src.driver import AmbiguityHalt, Driver
from src.extract import extract, validate

DEFAULT_FAKTURAMA = r"C:\Program Files\Fakturama2\Fakturama.exe"


def load_env(path: Path = Path(".env")) -> None:
    """Three lines instead of python-dotenv. Existing env always wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _shot(d, name: str):
    """Best-effort screenshot; never let capture failure mask the real error."""
    try:
        from datetime import datetime
        return d.screenshot(f"{name}-{datetime.now():%H%M%S}")
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fakturama image-to-cash automation")
    ap.add_argument("image", nargs="?", default="input/order.png", type=Path)
    ap.add_argument("--cached", action="store_true",
                    help="reuse input/order.json instead of calling the vision model")
    ap.add_argument("--extract-only", action="store_true",
                    help="extract and validate, then stop -- runs on any platform")
    ap.add_argument("--fakturama", default=os.environ.get("FAKTURAMA_EXE", DEFAULT_FAKTURAMA))
    ap.add_argument("--attach", action="store_true",
                    help="attach to a running Fakturama instead of launching one")
    ap.add_argument("--no-vision-tiebreak", action="store_true",
                    help="halt instead of asking an LLM when UIA leaves >1 candidate")
    args = ap.parse_args(argv)

    load_env()

    print(f"[1/6] extracting {args.image}" + (" (cached)" if args.cached else ""))
    order = extract(args.image, cache=Path("input/order.json"), use_cache=args.cached)
    errs = validate(order)
    if errs:
        print("extraction failed validation:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 3

    print(f"      {order.external_ref} | {order.company} | {len(order.lines)} items "
          f"| {order.net_total}/{order.vat_total}/{order.gross_total} "
          f"| paid={order.paid}")
    for ln in order.lines:
        print(f"      - {ln.sku:12} x{ln.qty} @ {ln.unit_net} -{ln.discount_pct}% "
              f"-> {ln.line_net}  (master gross {ln.product_gross_price}, {ln.vat_name})")
    if not order.delivery_same_as_billing:
        print("      note: delivery address differs from billing -- 2.8 shortcut does not apply")

    if args.extract_only:
        print("extract-only: stopping before the UI flow")
        return 0

    from src import flow  # imported late so --extract-only works without pywinauto

    d = Driver(app_path=args.fakturama, vision_tiebreak=not args.no_vision_tiebreak)
    try:
        print("[2/6] " + ("attaching to" if args.attach else "launching") + " Fakturama")
        d.connect() if args.attach else d.start()

        print("[3/6] stage 1-2: Order open, Debtor resolved")
        flow.stage1_open_order(d, order)
        flow.stage2_debtor(d, order)

        print("[4/6] stage 3: products")
        flow.stage3_products(d, order)

        print("[5/6] stage 4: saving and verifying the Order")
        flow.stage4_save_order(d, order)

        print("[6/6] stage 5: linked Invoice")
        flow.stage5_invoice(d, order)

    except AmbiguityHalt as halt:
        shot = _shot(d, "halt")
        print(f"\nSTOPPED FOR MANUAL REVIEW\n  {halt}", file=sys.stderr)
        if shot:
            print(f"  screenshot: {shot}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Always leave a picture behind. A traceback says which call failed; only a
        # screenshot says what the application was actually showing -- that is how the
        # '+49' -> '$9' corruption was found, and it would never have raised.
        shot = _shot(d, "error")
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        if shot:
            print(f"  screenshot: {shot}", file=sys.stderr)
        raise
    finally:
        _shot(d, "final-state")

    print(f"\ndone -- Order and linked Invoice saved and verified "
          f"({order.external_ref}, {order.gross_total} {order.currency})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

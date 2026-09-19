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
import time
from pathlib import Path

from src.driver import AmbiguityHalt, Driver
from src.extract import extract, validate

DEFAULT_FAKTURAMA = r"C:\Program Files\Fakturama2\Fakturama.exe"


# Line-buffer stdout even when it is redirected to a file. Python block-buffers a
# redirected stream, so a run that is working normally writes nothing for minutes
# and looks hung -- which is exactly how it looked while it was busy.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass



def verdict(ok: bool, rows: list[tuple[str, str]]) -> None:
    """The last thing on screen, and unmistakable.

    A run ends with a wall of step lines, and "did it work" should not require
    reading them. Nor should it require remembering that an empty tail means
    finished rather than hung -- which is exactly what it looked like.
    """
    width = 74
    head = " DONE - Order and linked Invoice verified " if ok else " STOPPED - see above "
    bar = "=" * width
    print(f"\n{bar}\n{head.center(width, '=')}\n{bar}")
    for k, v in rows:
        print(f"  {k:<11} {v}")
    print(f"{bar}\n  exit {0 if ok else 2}\n")


def load_env(path: Path = Path(".env")) -> None:
    """Three lines instead of python-dotenv. Existing env always wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def write_extraction_report(order, path: Path = Path("input/order.extracted.json")) -> Path:
    """Dump what was read from the image, plus everything derived from it.

    input/order.json is a cache -- it exists so a run can replay without calling a
    vision model. It holds the raw fields and nothing else, so it cannot answer the
    question a human actually has when checking an extraction: not "what did it
    read" but "what is it about to DO with that".

    The derived values are where the expensive mistakes live. The Product master
    price excludes the line discount (3.9), and whether the delivery address matches
    the billing one decides how the Debtor is built (2.8). Both are invisible in the
    raw fields and both are easy to get backwards, so they are written out
    explicitly, next to the arithmetic that has to hold.
    """
    import json

    o = order
    report = {
        "source_image": "input/order.png",
        "checks": {
            "arithmetic": "pass" if not validate(o) else "FAIL",
            "failures": validate(o),
            "identities": {
                "net_total == sum(line_net)": f"{sum(l.line_net for l in o.lines)} == {o.net_total}",
                "vat_total == sum(line_net * vat%)": f"{o.vat_total}",
                "gross == net + vat": f"{o.net_total} + {o.vat_total} == {o.gross_total}",
            },
        },
        "order": {
            "external_ref": o.external_ref,
            "order_date": o.order_date.isoformat(),
            "currency": o.currency,
            "payment_method": o.payment_method,
            "paid": o.paid,
            "payment_date": o.payment_date.isoformat() if o.payment_date else None,
        },
        "debtor": {
            "company": o.company,
            "contact": f"{o.contact_first_name} {o.contact_last_name}",
            "alias": o.alias,
            "email": o.email,
            "phone": o.phone,
            "billing": vars(o.billing),
            "delivery": vars(o.delivery),
            "delivery_same_as_billing": o.delivery_same_as_billing,
            "step_2_8": ("Delivery role goes on the Main address"
                         if o.delivery_same_as_billing else
                         "billing and delivery differ -- a SECOND address carries the Delivery role"),
        },
        "lines": [
            {
                "sku": ln.sku,
                "description": ln.description,
                "qty": ln.qty,
                "unit_net": str(ln.unit_net),
                "discount_pct": str(ln.discount_pct),
                "vat_pct": str(ln.vat_pct),
                "line_net_from_image": str(ln.line_net),
                "line_net_recomputed": str(ln.expected_line_net),
                "line_net_agrees": ln.line_net == ln.expected_line_net,
                "product_master_gross": str(ln.product_gross_price),
                "product_master_gross_note":
                    f"{ln.unit_net} x (1 + {ln.vat_pct}/100) -- the LINE discount is NOT applied (3.9)",
                "vat_record_name": ln.vat_name,
            }
            for ln in o.lines
        ],
        "totals": {
            "net": str(o.net_total),
            "vat": str(o.vat_total),
            "gross": str(o.gross_total),
        },
    }
    path.write_text(json.dumps(report, indent=2) + "\n")
    return path


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
    ap.add_argument("--film", action="store_true",
                    help="save a numbered frame after every click, for tools/storyboard.py")
    ap.add_argument("--attach", action="store_true",
                    help="attach to a running Fakturama instead of launching one")
    ap.add_argument("--no-vision-tiebreak", action="store_true",
                    help="halt instead of asking an LLM when UIA leaves >1 candidate")
    args = ap.parse_args(argv)

    started = time.monotonic()
    load_env()

    print(f"[1/6] extracting {args.image}" + (" (cached)" if args.cached else ""))
    order = extract(args.image, cache=Path("input/order.json"), use_cache=args.cached)
    errs = validate(order)
    if errs:
        verdict(False, [
            ("Failed", "the image did not satisfy its own arithmetic, twice"),
            *[("", e) for e in errs],
            ("Next", "the extraction is wrong; nothing was typed into Fakturama"),
        ])
        return 3

    print(f"      {order.external_ref} | {order.company} | {len(order.lines)} items "
          f"| {order.net_total}/{order.vat_total}/{order.gross_total} "
          f"| paid={order.paid}")
    for ln in order.lines:
        print(f"      - {ln.sku:12} x{ln.qty} @ {ln.unit_net} -{ln.discount_pct}% "
              f"-> {ln.line_net}  (master gross {ln.product_gross_price}, {ln.vat_name})")
    if not order.delivery_same_as_billing:
        print("      note: delivery address differs from billing -- 2.8 shortcut does not apply")

    report = write_extraction_report(order)
    print(f"      wrote {report} -- raw fields, derived values and the arithmetic check")

    if args.extract_only:
        print("extract-only: stopping before the UI flow")
        return 0

    from src import flow  # imported late so --extract-only works without pywinauto

    d = Driver(app_path=args.fakturama, vision_tiebreak=not args.no_vision_tiebreak,
               film=args.film)
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
        verdict(False, [
            ("Stopped at", halt.what if hasattr(halt, "what") else "an ambiguity"),
            ("Because", str(halt)),
            ("Screenshot", str(shot) if shot else "not captured"),
            ("Next", "resolve it in Fakturama, then re-run -- master data is reused"),
            ("Elapsed", f"{time.monotonic() - started:.0f}s"),
        ])
        return 2
    except Exception as exc:
        # Always leave a picture behind. A traceback says which call failed; only a
        # screenshot says what the application was actually showing -- that is how the
        # '+49' -> '$9' corruption was found, and it would never have raised.
        shot = _shot(d, "error")
        verdict(False, [
            ("Failed", f"{type(exc).__name__}: {exc}"),
            ("Screenshot", str(shot) if shot else "not captured"),
            ("Elapsed", f"{time.monotonic() - started:.0f}s"),
        ])
        raise
    finally:
        _shot(d, "final-state")

    verdict(True, [
        ("Order",      "saved and found in Data > Documents, still open"),
        ("Invoice",    f"linked, paid {order.payment_date}, {order.gross_total} {order.currency}"),
        ("Cust.Ref.",  order.external_ref),
        ("Totals",     f"net {order.net_total} / VAT {order.vat_total} / gross {order.gross_total}"),
        ("Lines",      ", ".join(f"{l.sku} x{l.qty}" for l in order.lines)),
        ("Elapsed",    f"{time.monotonic() - started:.0f}s"),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

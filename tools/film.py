"""Annotate the frames from a --film run, then lay them out.

    python -m src.run input/order.png --film   # writes docs/film/ + frames.json
    python3 tools/film.py                      # annotates in place, builds sheets

A raw frame of a window holding a thousand controls does not say which one was
clicked. The driver is the only thing that knows, so it records the element's
rectangle next to each frame; this draws it, adds the step number and the control
name, and dims everything else so the eye goes to the right place.

Quality is deliberate: full width, 256 colours. A screenshot is flat colour and
palette-compresses well, so there is no reason to throw away resolution -- the
earlier pass halved them to save a megabyte and made the text unreadable, which
is the wrong trade for the one artefact whose whole job is to be looked at.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FILM = Path("docs/film")
OUT = Path("docs/screenshots")
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

ACCENT = (220, 38, 38)
INK = (17, 24, 39)
MUTED = (110, 120, 135)
PAPER = (255, 255, 255)

BAR_H = 64
COLORS = 256

#: Which spec step each logical control belongs to; src/flow.py is the source of
#: truth, repeated here so a frame can be labelled without parsing Python.
STEP = {
    "toolbar.order": "1.3", "order.editor_tab": "1.8", "address_picker.open": "2.2",
    "address_dlg.cancel": "2.3", "address_dlg.ok": "2.10.2", "menu.payments": "2.10.1",
    "payment.add": "2.10.2", "new.contact": "2.5", "debtor.tab_addresses": "2.7",
    "debtor.add_address": "2.8", "debtor.tab_misc": "2.9", "order.save": "2.10.6",
    "product_picker.open": "3.2", "product_dlg.cancel": "3.3", "product_dlg.ok": "3.12",
    "menu.vats": "3.4", "vat.add": "3.5", "new.product": "3.7",
    "menu.documents": "4.5", "order.followup_invoice": "4.6",
    "invoice.editor_tab": "4.7", "invoice.paid": "5.3",
}

WHAT = {
    "toolbar.order": "open a New Order",
    "address_picker.open": "ask the Order's own picker for the Debtor",
    "address_dlg.cancel": "no exact match -- leave without selecting",
    "address_dlg.ok": "exactly one match -- take it",
    "menu.payments": "look the payment method up in its own list",
    "payment.add": "not there, so create it",
    "new.contact": "create the Debtor",
    "debtor.tab_addresses": "the billing address",
    "debtor.add_address": "a SECOND address, for the Delivery role",
    "debtor.tab_misc": "alias, payment method, Net",
    "order.save": "save, once",
    "product_picker.open": "ask the picker for this SKU",
    "product_dlg.cancel": "no exact match -- leave without selecting",
    "product_dlg.ok": "exactly one match -- take it",
    "menu.vats": "look the VAT rate up",
    "vat.add": "not there, so create it",
    "new.product": "create the Product",
    "menu.documents": "verify from Fakturama's own list",
    "order.followup_invoice": "the follow-up action, NOT the toolbar button",
    "invoice.editor_tab": "bring the linked Invoice forward",
    "invoice.paid": "mark it paid",
    "order.date": "the extracted Order date",
}


def font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


def annotate(frame: dict) -> Path | None:
    src = FILM / frame["file"]
    if not src.exists():
        return None
    im = Image.open(src).convert("RGB")
    W, H = im.size

    logical = frame["label"].replace("click-", "")
    step = STEP.get(logical, "")
    what = WHAT.get(logical, "")
    rect = frame.get("rect")

    if rect and rect[2] > rect[0] and rect[3] > rect[1]:
        # Dim everything, then punch the clicked control back through at full
        # brightness. Cheaper to read than an arrow, and it cannot point off-screen.
        dim = Image.blend(im, Image.new("RGB", (W, H), (255, 255, 255)), 0.55)
        box = (max(0, rect[0]), max(0, rect[1]), min(W, rect[2]), min(H, rect[3]))
        if box[2] > box[0] and box[3] > box[1]:
            dim.paste(im.crop(box), (box[0], box[1]))
            im = dim
            d = ImageDraw.Draw(im)
            pad = 6
            d.rectangle((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
                        outline=ACCENT, width=4)

    canvas = Image.new("RGB", (W, H + BAR_H), PAPER)
    canvas.paste(im, (0, 0))
    d = ImageDraw.Draw(canvas)
    d.line((0, H, W, H), fill=(210, 214, 220), width=2)

    y = H + 16
    x = 22
    n = f"{frame['n']:02d}"
    d.text((x, y), n, font=font(30, bold=True), fill=MUTED)
    x += d.textlength(n, font=font(30, bold=True)) + 18
    if step:
        d.text((x, y), step, font=font(30, bold=True), fill=ACCENT)
        x += d.textlength(step, font=font(30, bold=True)) + 16
    d.text((x, y), logical, font=font(28), fill=INK)
    x += d.textlength(logical, font=font(28)) + 18
    if what:
        d.text((x, y + 3), f"-- {what}", font=font(26), fill=MUTED)

    t = f"{frame['t']:.0f}s"
    d.text((W - 22 - d.textlength(t, font=font(26)), y + 3), t, font=font(26), fill=MUTED)

    canvas.quantize(colors=COLORS, method=Image.MEDIANCUT).save(src, optimize=True)
    return src


def main() -> None:
    man = FILM / "frames.json"
    if not man.exists():
        print("no docs/film/frames.json -- run:  python -m src.run input/order.png --film")
        return
    frames = json.loads(man.read_text())
    done = [f for f in frames if annotate(f)]
    total = sum((FILM / f["file"]).stat().st_size for f in done)
    print(f"annotated {len(done)} frames  ({total / 1048576:.1f}MB)")


if __name__ == "__main__":
    main()

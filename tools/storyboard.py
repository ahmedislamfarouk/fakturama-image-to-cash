"""Lay the frames from a --film run out in order, as a contact sheet.

    python -m src.run input/order.png --film      # writes docs/film/NNN-*.png
    python3 tools/storyboard.py                   # writes docs/screenshots/storyboard-N.png

The annotated screenshots argue that each step is correct. This argues something
else: that the steps happen in the order the spec sets out, one continuous run,
without the reviewer having to sit through six minutes of it. Each tile is a
real frame taken straight after the click that produced it, captioned with the
control that was clicked and the spec step that clicked it.

Frames are downscaled hard on purpose. Nobody reads a contact sheet for detail --
they read it for shape. Detail is what 01-10 are for.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FILM = Path("docs/film")
OUT = Path("docs/screenshots")
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

COLS = 4
TILE_W = 620
GAP = 18
CAPTION_H = 62
MARGIN = 26
PER_SHEET = 16

INK = (17, 24, 39)
MUTED = (100, 110, 125)
ACCENT = (198, 40, 40)
PAPER = (255, 255, 255)
FRAME = (203, 208, 215)

#: Which spec step each logical control belongs to. The flow is the source of
#: truth for this; it is repeated here only so a tile can be labelled without
#: parsing Python.
STEP = {
    "toolbar.order": "1.3", "order.editor_tab": "1.8", "address_picker.open": "2.2",
    "address_dlg.cancel": "2.3", "address_dlg.ok": "2.10.2", "menu.payments": "2.10.1",
    "payment.add": "2.10.2", "new.contact": "2.5", "debtor.tab_addresses": "2.7",
    "debtor.add_address": "2.8", "debtor.tab_misc": "2.9",
    "product_picker.open": "3.2", "product_dlg.cancel": "3.3", "product_dlg.ok": "3.12",
    "menu.vats": "3.4", "vat.add": "3.5", "new.product": "3.7",
    "menu.documents": "4.5", "order.followup_invoice": "4.6",
    "invoice.editor_tab": "4.7", "invoice.paid": "5.3",
}



#: The toolbar Save is the same control at 2.10.6, 3.11, 4.4 and 5.4, so the step
#: it belongs to has to come from its neighbours. What follows it settles the two
#: that matter: a Save followed by Data > Documents is the stage-4 or stage-5
#: save, and what precedes it settles the rest.
SAVE_AFTER = [("5.", "5.4"), ("4.", "4.4"), ("3.", "3.11"), ("2.", "2.10.6")]


def steps_for(controls: list[str]) -> list[str]:
    """Label a whole run at once, so Save can see both neighbours."""
    out, prev, seen_stage4 = [], "", False
    for i, logical in enumerate(controls):
        if logical == "order.save":
            nxt = controls[i + 1] if i + 1 < len(controls) else ""
            if nxt == "menu.documents":
                step = "5.4" if seen_stage4 else "4.4"
                seen_stage4 = True
            else:
                step = next((s for p, s in SAVE_AFTER if prev.startswith(p)), "2.10.6")
        else:
            step = STEP.get(logical, "")
        out.append(step)
        prev = step or prev
    return out


def step_for(logical: str, previous: str) -> str:
    if logical != "order.save":
        return STEP.get(logical, "")
    return next((s for p, s in SAVE_AFTER if previous.startswith(p)), "2.10.6")

def font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


def label_of(path: Path, previous: str = "") -> tuple[str, str]:
    """('2.2', 'address_picker.open') from '007-click-address_picker.open.png'."""
    m = re.match(r"(\d+)-click-(.+)\.png$", path.name)
    if not m:
        return "", path.stem
    logical = m.group(2)
    return step_for(logical, previous), logical


def sheet(frames: list[Path], index: int, total_sheets: int) -> Path:
    rows = (len(frames) + COLS - 1) // COLS
    with Image.open(frames[0]) as probe:
        ratio = probe.size[1] / probe.size[0]
    tile_h = int(TILE_W * ratio)
    cell_h = tile_h + CAPTION_H

    head_h = 92
    W = MARGIN * 2 + COLS * TILE_W + (COLS - 1) * GAP
    H = head_h + MARGIN + rows * cell_h + (rows - 1) * GAP + MARGIN

    canvas = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(canvas)

    d.text((MARGIN, MARGIN), "One run, in order", font=font(36, bold=True), fill=INK)
    sub = ("every frame taken straight after the click that produced it"
           if index == 1 else "continued")
    d.text((MARGIN, MARGIN + 44), sub, font=font(22), fill=MUTED)
    if total_sheets > 1:
        note = f"sheet {index} of {total_sheets}"
        d.text((W - MARGIN - d.textlength(note, font=font(22)), MARGIN + 44),
               note, font=font(22), fill=MUTED)

    prev = ""
    for i, f in enumerate(frames):
        r, c = divmod(i, COLS)
        x = MARGIN + c * (TILE_W + GAP)
        y = head_h + MARGIN + r * (cell_h + GAP)

        with Image.open(f) as im:
            im = im.convert("RGB").resize((TILE_W, tile_h), Image.LANCZOS)
            canvas.paste(im, (x, y))
        d.rectangle((x, y, x + TILE_W, y + tile_h), outline=FRAME, width=2)

        step, logical = label_of(f, prev)
        prev = step or prev
        n = f.name.split("-")[0].lstrip("0") or "0"
        cy = y + tile_h + 10
        d.text((x, cy), n, font=font(20, bold=True), fill=MUTED)
        off = x + d.textlength(n, font=font(20, bold=True)) + 10
        if step:
            d.text((off, cy), step, font=font(20, bold=True), fill=ACCENT)
            off += d.textlength(step, font=font(20, bold=True)) + 8
        d.text((off, cy), logical, font=font(20), fill=INK)

    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / (f"storyboard-{index}.png" if total_sheets > 1 else "storyboard.png")
    canvas.save(dst)
    print(f"{dst.name}  ({canvas.size[0]}x{canvas.size[1]})  {len(frames)} frames")
    return dst


def main() -> None:
    frames = sorted(FILM.glob("*.png"))
    if not frames:
        print("no frames in docs/film -- run:  python -m src.run input/order.png --film")
        return
    sheets = (len(frames) + PER_SHEET - 1) // PER_SHEET
    for i in range(sheets):
        sheet(frames[i * PER_SHEET:(i + 1) * PER_SHEET], i + 1, sheets)


if __name__ == "__main__":
    main()

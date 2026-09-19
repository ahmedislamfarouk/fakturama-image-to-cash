"""Turn raw captures into the annotated screenshots the task asks for.

The deliverable is "annotated screenshots", not screenshots. A raw capture of a
1400-control Eclipse window shows a reviewer nothing -- they cannot know which
six pixels carry the claim. So each shot gets numbered callouts on the regions
that matter, and a caption strip naming the spec step each one satisfies.

Regions are fractions of the image, not pixels, so a capture at a different
resolution annotates correctly without edits.

    python3 tools/annotate.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAW = Path("docs/raw")
OUT = Path("docs/screenshots")

INK = (17, 24, 39)            # caption text
PAPER = (255, 255, 255)
RULE = (209, 213, 219)

#: One colour per callout, reused in the caption so a reader can match a line to
#: its box without counting. Eight identical red rectangles on a grey Windows form
#: are technically correct and genuinely hard to follow.
#:
#: Muted and similar in weight on purpose: these sit on a pale application UI, so
#: they need to separate from each other without turning the screenshot into a
#: parade. All are dark enough to read against white and against the yellow
#: Fakturama uses for computed fields.
PALETTE = [
    (198, 40, 40),    # red
    (21, 101, 154),   # blue
    (176, 106, 0),    # amber
    (27, 122, 90),    # green
    (113, 68, 150),   # purple
    (166, 62, 108),   # rose
    (63, 81, 112),    # slate
    (140, 90, 40),    # brown
]


def colour(i: int) -> tuple[int, int, int]:
    return PALETTE[i % len(PALETTE)]


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def badge_spot(box, others, rad, w, h):
    """Somewhere to put the number that is not on top of another callout.

    The badge used to sit at every box's top-left corner unconditionally, which
    on adjacent boxes -- the four fields of the payment row, say -- stacked the
    numbers on each other's borders.
    """
    l, t, r, b = box
    mx = (l + r) / 2
    gap = rad + rad // 3
    my = (t + b) / 2
    # Above and beside before below. Collision is only checked against other
    # callouts, not against the application's own pixels, and a badge dropped
    # under a box tends to land on the very text the box is pointing at --
    # badge 4 sat squarely on the address it was labelling.
    candidates = [
        (l - rad // 2, t - rad // 2),     # outside top-left, the default
        (mx, t - gap),                    # straight above -- what a row of
        (r + rad // 2, t - rad // 2),     # adjacent fields needs
        (r + gap, my),                    # beside, right
        (l - gap, my),                    # beside, left
        (mx, t - gap * 2),                # one row further above
        (mx, b + gap),                    # only now, below
        (l - rad // 2, b + rad // 2),
        (r + rad // 2, b + rad // 2),
        (mx, b + gap * 2),
    ]
    for cx, cy in candidates:
        cx = min(max(cx, rad), w - rad)
        cy = min(max(cy, rad), h - rad)
        spot = (cx - rad, cy - rad, cx + rad, cy + rad)
        if not any(_overlaps(spot, o) for o in others):
            return cx, cy
    # Everything collided. Sit above the box rather than on top of the content it
    # points at -- an unreadable label is worse than one slightly out of place.
    cx, cy = mx, t - gap
    return min(max(cx, rad), w - rad), min(max(cy, rad), h - rad)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default()


def wrap(d, text: str, f, width: float) -> list[str]:
    """Greedy wrap against the real rendered width.

    A caption tuned on a 2560px screenshot silently ran off the edge of an
    800px dialog crop. Measuring beats guessing a character count.
    """
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if cur and d.textlength(trial, font=f) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def annotate(src: Path, dst: Path, title: str, subtitle: str, marks: list[dict],
             crop: tuple[int, int, int, int] | None = None) -> None:
    """marks: {'box': (l, t, r, b) as 0-1 fractions, 'text': str, 'step': str}

    `crop` is in pixels of the raw capture. A full-screen shot of a maximized
    Eclipse window renders the row that matters about twelve pixels tall, which
    is not evidence a reviewer can read. Cropping to the region first, then
    annotating, keeps the fractions simple and the result legible.
    """
    im = Image.open(src).convert("RGB")
    if crop:
        im = im.crop(crop)
    w, h = im.size
    # Overlay geometry scales with the image so boxes stay proportionate. The
    # caption does NOT: a 800px-wide dialog crop would otherwise get 7px text,
    # which defeats the point of annotating it at all.
    scale = w / 2560                      # every overlay size is tuned at 2560 wide
    tscale = max(scale, 0.78)             # caption text never shrinks below legible
    pad = int(28 * tscale)
    line_h = int(34 * tscale)

    # measured below, once the fonts exist
    cap_h = 0
    f_badge = font(int(26 * max(scale, 0.7)), bold=True)
    f_title = font(int(34 * tscale), bold=True)
    f_sub = font(int(24 * tscale))
    f_item = font(int(23 * tscale))
    f_step = font(int(23 * tscale), bold=True)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text_w = w - pad * 2
    indent = int(13 * tscale) * 2 + int(14 * tscale)
    title_lines = wrap(probe, title, f_title, text_w)
    sub_lines = wrap(probe, subtitle, f_sub, text_w)
    mark_lines = [wrap(probe, m["text"], f_item, text_w - indent - int(60 * tscale))
                  for m in marks]

    cap_h = (pad * 2
             + int(44 * tscale) * len(title_lines)
             + int(32 * tscale) * len(sub_lines) + int(14 * tscale)
             + sum(line_h * len(ls) for ls in mark_lines))

    canvas = Image.new("RGB", (w, h + cap_h), PAPER)
    canvas.paste(im, (0, 0))
    d = ImageDraw.Draw(canvas)

    boxes = [(m["box"][0] * w, m["box"][1] * h, m["box"][2] * w, m["box"][3] * h)
             for m in marks]
    lw = max(2, int(3 * scale))
    rad = int(20 * max(scale, 0.7))

    # A hairline of white just outside each box, so the outline stays visible
    # against Fakturama's grey chrome and its yellow computed fields alike.
    for box in boxes:
        d.rectangle((box[0] - lw, box[1] - lw, box[2] + lw, box[3] + lw),
                    outline=PAPER, width=lw)
    for i, box in enumerate(boxes):
        d.rectangle(box, outline=colour(i), width=lw)

    placed: list[tuple] = []
    for i, box in enumerate(boxes):
        cx, cy = badge_spot(box, [b for j, b in enumerate(boxes) if j != i] + placed,
                            rad, w, h)
        spot = (cx - rad, cy - rad, cx + rad, cy + rad)
        placed.append(spot)
        d.ellipse(spot, fill=colour(i), outline=PAPER, width=max(1, lw // 2))
        label = str(i + 1)
        bb = d.textbbox((0, 0), label, font=f_badge)
        d.text((cx - (bb[2] - bb[0]) / 2, cy - (bb[3] - bb[1]) / 2 - int(2 * scale)),
               label, font=f_badge, fill=PAPER)

    d.line((0, h, w, h), fill=RULE, width=max(1, int(2 * scale)))

    y = h + pad
    for ln in title_lines:
        d.text((pad, y), ln, font=f_title, fill=INK)
        y += int(44 * tscale)
    for ln in sub_lines:
        d.text((pad, y), ln, font=f_sub, fill=(75, 85, 99))
        y += int(32 * tscale)
    y += int(14 * tscale)

    for i, m in enumerate(marks, 1):
        crad = int(13 * tscale)
        d.ellipse((pad + 2, y + 5, pad + 2 + crad * 2, y + 5 + crad * 2),
                  fill=colour(i - 1))
        bb = d.textbbox((0, 0), str(i), font=font(int(18 * tscale), bold=True))
        d.text((pad + 2 + crad - (bb[2] - bb[0]) / 2, y + 5 + crad - (bb[3] - bb[1]) / 2 - 1),
               str(i), font=font(int(18 * tscale), bold=True), fill=PAPER)

        x = pad + crad * 2 + int(14 * tscale)
        step = m.get("step", "")
        if step:
            d.text((x, y + int(3 * tscale)), step, font=f_step, fill=colour(i - 1))
            x += d.textlength(step, font=f_step) + int(12 * tscale)
        for k, ln in enumerate(mark_lines[i - 1]):
            d.text((x if k == 0 else pad + indent, y + int(3 * tscale)),
                   ln, font=f_item, fill=INK)
            y += line_h

    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst)
    print(f"{dst.name}  ({canvas.size[0]}x{canvas.size[1]})")


SHOTS = [
    dict(
        src="A-order-complete.png", dst="01-order-complete.png",
        title="Stage 1-4  |  the Order, complete and still open",
        subtitle="Every record referenced here was created by this run against a database wiped beforehand.",
        marks=[
            dict(box=(.217, .097, .293, .110), step="1.4",
                 text="No. PO000001 -- assigned by Fakturama, never typed by us"),
            dict(box=(.449, .097, .510, .110), step="1.5",
                 text="Date Jul 14, 2026 -- from the image, typed digit-by-digit into a segmented widget"),
            dict(box=(.228, .137, .498, .150), step="1.6",
                 text="Cust.Ref. WEB-2026-0714-A17 -- read back before the run continues"),
            dict(box=(.228, .153, .304, .165), step="2.8",
                 text="TWO address tabs. Billing and delivery differ here, so a second address carries the Delivery role"),
            dict(box=(.216, .221, .584, .246), step="3.16",
                 text="CHR-ERG-01 at 250.00 -10% = 450.00, MAT-DESK-02 at 40.00 x3 = 120.00. The Item No. of each row is verified -- a row not holding its SKU halts the run"),
            dict(box=(.792, .421, .997, .499), step="4.3",
                 text="Net 570.00 / VAT 108.30 / Total 678.30 -- compared against the source image"),
            dict(box=(.420, .075, .546, .087), step="3.10",
                 text="Both Products exist as their own records -- the tabs Fakturama opened when creating them"),
        ],
    ),
    dict(
        src="02-no-match.png", dst="02-picker-finds-nothing.png",
        title="Stage 2  |  the existence check, before the Debtor exists",
        subtitle="The spec says selection IS the existence check. Never query the database -- ask the Order's own picker.",
        marks=[
            dict(box=(.715, .055, .965, .105), step="2.2",
                 text="Search -- the company name is typed here, then the list is left to settle before it is read"),
            dict(box=(.02, .118, .99, .165), step="",
                 text="Column headers ARE visible to UIA. The rows underneath are not -- SWT paints them"),
            dict(box=(.02, .17, .99, .84), step="2.3",
                 text="Zero rows. No exact match, so the Debtor gets created -- and only then"),
            dict(box=(.72, .90, .99, .975), step="",
                 text="Cancel returns to the SAME open Order, rather than starting the flow over"),
        ],
    ),
    dict(
        src="B-address-picker-match.png", dst="03-picker-finds-it.png",
        crop=(880, 400, 1680, 960),
        title="Stage 2  |  the same picker after creation -- proof it persisted",
        subtitle="Creation is proved by the consumer finding it, not by the editor claiming it saved.",
        marks=[
            dict(box=(.768, .082, .950, .122), step="2.10",
                 text="The same company name searched again, after the Debtor was created"),
            dict(box=(.020, .134, .980, .171), step="",
                 text="Headers are visible to UIA. The row below is not -- it is read from pixels"),
            dict(box=(.020, .176, .790, .212), step="2.10.2",
                 text="Exactly one row: CUST000001, Marta, Klein, Northstar Office..., 10117, Berlin"),
            dict(box=(.798, .176, .912, .212), step="2.8",
                 text="address type reads BILLING -- the second address carries DELIVERY"),
            dict(box=(.020, .215, .980, .900), step="2.10.2",
                 text="Nothing else matched. A second row here would be an AmbiguityHalt, never a guess"),
        ],
    ),
    dict(
        src="C-invoice-paid.png", dst="04-invoice-paid.png",
        crop=(500, 330, 2560, 820),
        title="Stage 5  |  the linked Invoice, paid from the source document",
        subtitle="The payment row silently saved with today's date until the check was made to read the field back.",
        marks=[
            dict(box=(.029, .138, .483, .212), step="5.1",
                 text="Both lines carried over from the Order, with the right SKU on each"),
            dict(box=(.742, .700, .997, .955), step="4.3",
                 text="Net 570.00 / VAT 108.30 / Total 678.30, unchanged from the Order"),
            dict(box=(.006, .878, .032, .925), step="5.3",
                 text="paid ticked -- only because the source document says PAID"),
            dict(box=(.032, .878, .088, .925), step="5.2",
                 text="Bank Transfer -- the payment method extracted from the image"),
            dict(box=(.092, .878, .163, .925), step="5.3",
                 text="Jul 18, 2026 -- the SOURCE payment date, not the date of the run"),
            dict(box=(.163, .878, .208, .925), step="5.3",
                 text="678.30 -- the full Invoice total. Both are read back before the run reports ok"),
        ],
    ),
    dict(
        src="D-documents.png", dst="05-documents-verified.png",
        title="Stage 5  |  Data > Documents -- the final verification",
        subtitle="The Invoice paid beside its still-open source Order, read back out of Fakturama's own list.",
        marks=[
            dict(box=(.348, .639, .625, .653), step="5.5",
                 text="INV000001, dated today -- the Invoice this run created"),
            dict(box=(.348, .654, .625, .668), step="4.5",
                 text="PO000001, dated Jul 14 2026 from the image -- the source Order"),
            dict(box=(.632, .636, .718, .671), step="5.1",
                 text="Both rows carry Cust.Ref. WEB-2026-0714-A17 -- the link is real, not implied"),
            dict(box=(.724, .636, .800, .671), step="5.5",
                 text="'paid' and 'open' -- the two states the spec asks to see side by side"),
            dict(box=(.806, .636, .918, .671), step="4.3",
                 text="678.30 on both. The Invoice did not consume or alter the Order"),
        ],
    ),
    dict(
        src="E-product-editor.png", dst="06-product-master.png",
        crop=(500, 140, 2000, 560),
        title="Stage 3  |  the Product record, and the trap in step 3.9",
        subtitle="The master price is the unit net plus VAT. The LINE discount must not touch it.",
        marks=[
            dict(box=(.093, .078, .752, .119), step="3.7",
                 text="Item Number CHR-ERG-01 -- the SKU the Order's picker will search for"),
            dict(box=(.093, .140, .752, .181), step="3.8",
                 text="Name, taken from the source document (Description below holds the same text)"),
            dict(box=(.090, .598, .190, .648), step="3.9",
                 text="297.50 = 250.00 x 1.19. NOT 267.75 -- the 10% line discount belongs to the Order line, not to the Product"),
            dict(box=(.088, .722, .150, .776), step="3.10",
                 text="VAT 19% -- the rate this run created, selected rather than typed"),
            dict(box=(.093, .790, .752, .836), step="3.10",
                 text="Stock 0.00; cost price and the user fields left as Fakturama proposes them"),
        ],
    ),
    dict(
        src="F-debtor-editor.png", dst="07-debtor-record.png",
        crop=(480, 130, 2100, 620),
        title="Stage 2  |  the Debtor record Fakturama saved",
        subtitle="Read back from the saved record, not from the editor that claimed to save it.",
        marks=[
            dict(box=(.100, .055, .240, .107), step="2.5",
                 text="Customer ID CUST000001 -- proposed by Fakturama and left alone"),
            dict(box=(.100, .115, .365, .225), step="2.6",
                 text="Company from the source document. First and Last Name are the row below, and Marta Klein is visible there"),
            dict(box=(.100, .232, .190, .285), step="2.6",
                 text="Salutation stays '---' because the source supplies none"),
            dict(box=(.027, .350, .160, .405), step="2.7",
                 text="Addresses tab holds both addresses -- see 01 and 03 for the roles"),
            dict(box=(.100, .570, .650, .620), step="2.9",
                 text="Alias name NORTHSTAR-BERLIN"),
            dict(box=(.712, .620, .930, .672), step="2.10",
                 text="Payment: Bank Transfer -- the method this run created first"),
            dict(box=(.100, .740, .650, .795), step="2.12",
                 text="Net or Gross: Net, matching the Order's price mode"),
        ],
    ),
    dict(
        src="A-order-complete.png", dst="08-controls-the-flow-drives.png",
        title="The controls the flow drives, and the step that drives each",
        subtitle="The steps that are clicks rather than states. None of these is found by position -- "
                 "the boxes show where they happen to be in this window, not where the code looks.",
        marks=[
            dict(box=(.066, .029, .084, .066), step="1.3",
                 text="toolbar Order -- opens the New Order editor. NOT the Invoice button beside it"),
            dict(box=(.2170, .1660, .2275, .1815), step="2.2",
                 text="the UPPER icon beside Addresses opens the Debtor picker. It has no name in the "
                      "accessibility tree; it is reached as the first icon after the 'Addresses' label"),
            dict(box=(.2170, .1820, .2275, .1965), step="2.8",
                 text="the lower green + adds an address. Confusing the two is exactly what the spec warns about"),
            dict(box=(.152, .029, .171, .066), step="2.5",
                 text="toolbar Contact -- creates the Debtor once the picker has proved it is absent"),
            dict(box=(.130, .029, .149, .066), step="3.7",
                 text="toolbar Product -- same, for each Product the picker cannot find"),
            dict(box=(.004, .215, .062, .227), step="2.10.1",
                 text="Data > terms of payment -- where the payment method is looked up, then created"),
            dict(box=(.004, .249, .062, .261), step="3.4",
                 text="Data > VATs -- where the VAT rate is looked up, then created"),
            dict(box=(.004, .148, .062, .160), step="4.5",
                 text="Data > Documents -- the list both verifications read back from"),
            dict(box=(.046, .029, .064, .066), step="4.4",
                 text="toolbar Save, clicked exactly once per record (also 2.10.6, 3.11, 5.4)"),
            dict(box=(.782, .161, .802, .194), step="4.6",
                 text="Invoice inside 'Create a follow-up document'. The toolbar Invoice button would "
                      "make an unlinked document -- this one preserves the Order relationship"),
            dict(box=(.751, .161, .781, .194), step="4.6",
                 text="Confirmation sits immediately left of it, which is what an index-only selector picked"),
        ],
    ),
    dict(
        src="G-payment-method.png", dst="09-payment-method.png",
        crop=(480, 130, 2000, 400),
        title="Stage 2  |  the payment method record (2.10.3 - 2.10.6)",
        subtitle="Created because the Order's own list had no exact match, then selected on the Debtor.",
        marks=[
            dict(box=(.130, .140, .980, .215), step="2.10.3",
                 text="Name: Bank Transfer, exactly as the source document writes it"),
            dict(box=(.130, .238, .980, .312), step="2.10.3",
                 text="Account is left blank -- the source supplies none, so none is invented"),
            dict(box=(.130, .334, .980, .409), step="2.10.3",
                 text="Description, same value"),
            dict(box=(.130, .430, .980, .505), step="2.10.4",
                 text="the payment code. This field saved EMPTY while the log said 'Credit transfer', "
                      "because a UIA select() does not fire SWT's listener -- it is now clicked, and "
                      "FKT_PAYMENT stores 30, the UNTDID 4461 code for a credit transfer"),
            dict(box=(.130, .530, .980, .605), step="2.10.5",
                 text="Cash discount 0%"),
            dict(box=(.130, .626, .980, .701), step="2.10.5",
                 text="Discount Days 0"),
            dict(box=(.130, .722, .980, .797), step="2.10.5",
                 text="Net Days 0. Saved once, with 2.10.6's single Save click"),
        ],
    ),
    dict(
        src="H-vat-record.png", dst="10-vat-record.png",
        crop=(480, 130, 2000, 400),
        title="Stage 3  |  the VAT record (3.4 - 3.6)",
        subtitle="Looked up in Data > VATs first; created only because no exact match existed.",
        marks=[
            dict(box=(.098, .140, .980, .215), step="3.6",
                 text="Name VAT 19% -- the name the Order's VAT lookup searches for"),
            dict(box=(.098, .334, .980, .409), step="3.6",
                 text="Description, same value"),
            dict(box=(.098, .430, .980, .505), step="3.5",
                 text="VAT code (E-Invoice) = S (Standard rate). The spec requires S; a pre-existing "
                      "rate cannot be checked from the list, which is why reuse halts instead of guessing"),
            dict(box=(.098, .530, .980, .605), step="3.6",
                 text="Value 19%, from the VAT percentage on both item lines"),
            dict(box=(.240, .638, .315, .722), step="3.6",
                 text="'Set as standard' is deliberately NOT clicked -- this run must not change the "
                      "workspace default"),
        ],
    ),
]


def main() -> None:
    missing = []
    for s in SHOTS:
        src = RAW / s["src"]
        if not src.exists():
            missing.append(s["src"])
            continue
        annotate(src, OUT / s["dst"], s["title"], s["subtitle"], s["marks"],
                 crop=s.get("crop"))
    if missing:
        print("\nmissing raw captures (run tools/capture_docs.py on the VM):")
        for m in missing:
            print("  -", m)


if __name__ == "__main__":
    main()

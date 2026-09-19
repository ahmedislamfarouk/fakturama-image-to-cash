"""Build the screen recording, with a red marker wherever the automation clicked.

    python -m src.run input/order.png --record
    python3 tools/video.py               # -> docs/recording.mp4

`--record` grabs the screen once a second on a thread inside the run, so these
frames and the click log share one clock: a click logged at t=82.4s can be drawn
on the frame captured at t=82.4s, on the control it actually hit.

That marker is the point. A plain screen capture of UI automation is close to
useless -- there is no cursor travel to follow, controls just change, and you
cannot tell what was pressed or when. Here the click is marked where it landed
and held for a beat so the eye catches it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REC = Path("docs/record")
FILM = Path("docs/film")
OUT = Path("docs/recording.mp4")
OUT_REALTIME = Path("docs/recording-realtime.mp4")

FPS = 12                 # playback rate; source is 1 fps, so this is 12x speed
HOLD = 2.0               # seconds a click marker stays visible, in source time
ACCENT = (220, 38, 38)
BAR_H = 46
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

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


def clicks() -> list[dict]:
    man = FILM / "frames.json"
    if not man.exists():
        return []
    return [c for c in json.loads(man.read_text()) if c.get("rect")]


def draw(frame: Path, t: float, cs: list[dict], scale: float,
         total: float, speed: float, prev_step: str = "") -> Image.Image:
    im = Image.open(frame).convert("RGB")
    W, H = im.size
    canvas = Image.new("RGB", (W, H + BAR_H), (255, 255, 255))
    canvas.paste(im, (0, 0))
    d = ImageDraw.Draw(canvas, "RGBA")

    live = [c for c in cs if 0 <= t - c["t"] <= HOLD]
    for c in live:
        l, tp, r, b = [v * scale for v in c["rect"]]
        cx, cy = (l + r) / 2, (tp + b) / 2
        age = (t - c["t"]) / HOLD                      # 0 fresh -> 1 fading
        # A ring that shrinks onto the control, then the box it landed in.
        rad = 34 * (1 - age) + 12
        alpha = int(235 * (1 - age * 0.7))
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad),
                  outline=(*ACCENT, alpha), width=4)
        d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(*ACCENT, alpha))
        d.rectangle((l - 3, tp - 3, r + 3, b + 3), outline=(*ACCENT, alpha), width=3)

    label = ""
    if live:
        c = live[-1]
        logical = c["label"].replace("click-", "")
        label = f"{step_for(logical, prev_step)}  {logical}".strip()
    d.line((0, H, W, H), fill=(210, 214, 220), width=2)
    d.text((18, H + 12), label or "...", font=font(22, bold=bool(label)),
           fill=ACCENT if label else (150, 156, 165))
    # Real elapsed time, and the speed-up, on every frame. The clip is 22
    # seconds and the run is five and a half minutes; without this on screen
    # the obvious reading is that the automation is fast.
    ts = f"{t:6.0f}s of {total:.0f}s real time   |   {speed:.0f}x"
    d.text((W - 18 - d.textlength(ts, font=font(20)), H + 14), ts,
           font=font(20), fill=(120, 128, 140))
    return canvas


def main() -> None:
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found")
        return
    frames = sorted(REC.glob("*.png"))
    if not frames:
        print("no frames in docs/record -- run:  python -m src.run input/order.png --record")
        return

    cs = clicks()
    # The recorder downscales; the click rects are full-resolution screen coords.
    with Image.open(frames[0]) as f0:
        scale = f0.size[0] / 2560.0

    last = max(float(m.group(1)) for f in frames
               if (m := re.match(r"\d+-([\d.]+)\.png$", f.name)))
    total = last
    speed = total / (len(frames) / FPS)

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        for i, f in enumerate(frames):
            m = re.match(r"\d+-([\d.]+)\.png$", f.name)
            t = float(m.group(1)) if m else float(i)
            draw(f, t, cs, scale, total, speed).save(tmpd / f"{i:05d}.png")

        # Two cuts of the same frames. The fast one is for showing someone what
        # the automation does; the real-time one is for working out where the
        # five minutes actually go, which is a different job and needs the
        # pauses left in.
        for dst, fps in ((OUT, FPS), (OUT_REALTIME, 1)):
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
                   "-i", str(tmpd / "%05d.png"),
                   "-vf", "scale=1600:-2:flags=lanczos,format=yuv420p",
                   "-c:v", "libx264", "-preset", "slow", "-crf", "27",
                   "-movflags", "+faststart", str(dst)]
            subprocess.run(cmd, check=True)
            secs = len(frames) / fps
            print(f"{dst}  {len(frames)} frames at {fps}fps -> {secs:.0f}s "
                  f"({total:.0f}s real, {total/secs:.0f}x), "
                  f"{dst.stat().st_size/1048576:.1f}MB")
    print(f"{len(cs)} clicks marked")


if __name__ == "__main__":
    main()

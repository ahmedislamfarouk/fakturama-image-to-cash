"""Turn the annotated film frames into a short recording.

    python -m src.run input/order.png --film
    python3 tools/film.py
    python3 tools/record.py            # -> docs/recording.mp4

The task asks for "annotated screenshots or a short recording". This is the
recording, assembled from the frames rather than screen-captured, which has one
real advantage: an actual screen capture of UI automation shows a cursor
teleporting around and you cannot tell what was clicked. Here every frame
already highlights the control the driver pressed, because the driver recorded
its rectangle.

Each frame holds for a beat, so it reads as a slideshow of the run rather than a
video anyone has to scrub.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

FILM = Path("docs/film")
OUT = Path("docs/recording.mp4")
SECONDS_PER_FRAME = 1.6
WIDTH = 1600


def main() -> None:
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found -- install it, or use docs/screenshots/storyboard-*.png")
        return
    man = FILM / "frames.json"
    if not man.exists():
        print("no docs/film/frames.json -- run:  python -m src.run input/order.png --film")
        return

    frames = [f for f in json.loads(man.read_text()) if (FILM / f["file"]).exists()]
    if not frames:
        print("no frames on disk")
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        # ffmpeg's concat demuxer wants a playlist; numbering them ourselves keeps
        # the order explicit rather than trusting a glob.
        lines = []
        for i, f in enumerate(frames):
            dst = tmpd / f"{i:03d}.png"
            shutil.copy(FILM / f["file"], dst)
            lines.append(f"file '{dst}'")
            lines.append(f"duration {SECONDS_PER_FRAME}")
        lines.append(f"file '{tmpd / f'{len(frames) - 1:03d}.png'}'")   # hold the last
        playlist = tmpd / "list.txt"
        playlist.write_text("\n".join(lines) + "\n")

        OUT.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(playlist),
            "-vf", f"scale={WIDTH}:-2:flags=lanczos,format=yuv420p",
            "-r", "25", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
            "-movflags", "+faststart", str(OUT),
        ]
        subprocess.run(cmd, check=True)

    size = OUT.stat().st_size / 1048576
    print(f"{OUT}  {len(frames)} frames, "
          f"{len(frames) * SECONDS_PER_FRAME:.0f}s, {size:.1f}MB")


if __name__ == "__main__":
    main()

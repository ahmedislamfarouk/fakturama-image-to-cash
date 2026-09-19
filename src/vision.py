"""T4 vision tiebreak -- the last resort in driver.find().

Reached only when the scoped UIA search, the anchor-relative walk and the property
discriminator all leave more than one candidate. The model decides WHICH control;
UIA still supplies WHERE it is, so no coordinate is ever authored or persisted.

Shares the extractor's endpoint config, so the whole project speaks to exactly one
OpenAI-compatible URL.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from pathlib import Path

PROMPT = """You are helping a UI automation script disambiguate a control.

The screenshot shows one region of a Fakturama window. The script is looking for:
  {want}

It has narrowed the UI-Automation tree to these candidates, in tree order:
{options}

Reply with ONLY the index of the correct candidate as a bare integer (0-based).
If you cannot tell with confidence, reply exactly: UNSURE
"""


def pick_control(shot: Path, want: str, labels: list[str]) -> int | None:
    """Return the index of the candidate the model picks, or None if unsure."""
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    if not key:
        return None

    options = "\n".join(f"  [{i}] {lab}" for i, lab in enumerate(labels))
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(want=want, options=options)},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(shot.read_bytes()).decode()}},
        ]}],
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": os.environ.get("LLM_USER_AGENT", "Mozilla/5.0"),
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        text = json.load(r)["choices"][0]["message"]["content"].strip()

    if "UNSURE" in text.upper():
        return None
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None

"""T4 vision tiebreak -- the last resort in driver.find().

Reached only when the scoped UIA search, the anchor-relative walk and the property
discriminator all leave more than one candidate. The model decides WHICH control;
UIA still supplies WHERE it is, so no coordinate is ever authored or persisted.

Shares the extractor's endpoint config, so the whole project speaks to exactly one
OpenAI-compatible URL.
"""

from __future__ import annotations

import base64
import hashlib
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


TABLE_PROMPT = """This image is a results table from a desktop application dialog.

Return ONLY a JSON object, no prose and no markdown fence:
{"columns": ["..."], "rows": [["cell", "cell", ...], ...], "row_y_pct": [12.5, 18.0, ...]}

- "columns" is the header row, left to right.
- "rows" is every DATA row actually visible, each as a list of cell strings in the
  same order as the columns. Use "" for an empty cell.
- If the table has no data rows, return {"columns": [...], "rows": [], "row_y_pct": []}.
- Do not invent rows. Do not include the header in "rows".
- "row_y_pct" has ONE entry per data row, in the same order: the vertical centre of
  that row as a percentage (0-100) of the image height. Be precise; it is used to
  click the row.
"""


_TABLE_CACHE: dict[str, dict] = {}


def _split(val: str) -> list[str]:
    return [x.strip() for x in (val or "").split(",") if x.strip()]


def providers() -> list[dict]:
    """Endpoints to try in order: (base_url, key, [models]).

    Configured as numbered env groups so several providers -- or several keys -- can
    be chained without code changes:

        LLM_BASE_URL   / LLM_API_KEY   / LLM_MODEL + LLM_FALLBACK_MODELS
        LLM_BASE_URL_2 / LLM_API_KEY_2 / LLM_MODELS_2
        LLM_BASE_URL_3 / LLM_API_KEY_3 / LLM_MODELS_3   ...

    Rate limits apply per model AND per account, so failing over across both axes is
    what keeps a run alive. Local OCR (src/ocr.py) is the final fallback and needs no
    account at all.
    """
    out = []
    first_models = [os.environ.get("LLM_MODEL", "gpt-4o-mini"), *_split(os.environ.get("LLM_FALLBACK_MODELS", ""))]
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        out.append({
            "base": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            "key": key,
            "models": list(dict.fromkeys([m for m in first_models if m])),
        })
    n = 2
    while True:
        base = os.environ.get(f"LLM_BASE_URL_{n}")
        k = os.environ.get(f"LLM_API_KEY_{n}")
        if not (base and k):
            break
        if "REPLACE" not in k.upper():   # skip unfilled placeholder slots
            out.append({
                "base": base.rstrip("/"),
                "key": k,
                "models": _split(os.environ.get(f"LLM_MODELS_{n}", "")) or ["gpt-4o-mini"],
            })
        n += 1
    return out


def models() -> list[str]:
    """Models of the primary provider (kept for callers that only need the list)."""
    provs = providers()
    return provs[0]["models"] if provs else []


def _post(base: str, key: str, model: str, prompt: str, image_b64: str, timeout: int = 120) -> str:
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def ask_vision(prompt: str, image: bytes, timeout: int = 120) -> str:
    """One vision call, trying every configured provider and model until one answers."""
    provs = providers()
    if not provs:
        raise RuntimeError("no vision provider configured (set LLM_API_KEY)")
    b64 = base64.b64encode(image).decode()
    errors = []
    for i, prov in enumerate(provs, 1):
        for model in prov["models"]:
            try:
                return _post(prov["base"], prov["key"], model, prompt, b64, timeout)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode()[:100].replace("\n", " ")
                except Exception:
                    pass
                errors.append(f"p{i}/{model}: HTTP {exc.code} {detail}")
                if exc.code in (401, 403):
                    break            # bad key for this provider; skip its other models
            except Exception as exc:
                errors.append(f"p{i}/{model}: {type(exc).__name__}")
    raise RuntimeError("all vision providers failed -- " + " | ".join(errors[:6]))


def read_table(shot: Path) -> dict:
    """Read a custom-painted table that UIA does not expose.

    SWT paints Fakturama's selector grids itself, so the 'Select the address' and
    'Select a product' result lists contain no Table/DataGrid/List element -- only
    empty Panes (verified against 2.2.0). The rectangle still comes from UIA at
    runtime; only the pixels inside it are read by the model. No coordinate is
    authored or persisted, so this stays inside the no-hardcoded-coordinates rule.
    """
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        raise RuntimeError("no LLM_API_KEY set; cannot read the UIA-invisible grid")

    # Identical pixels cannot yield different rows, so memoize on the image hash.
    # wait_stable() already proves the grid has stopped changing before we read it,
    # and the same grid is read repeatedly across a run (search, judge, re-select).
    # Without this the free tier is exhausted by re-reading pictures we have seen.
    raw = shot.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest in _TABLE_CACHE:
        return _TABLE_CACHE[digest]

    try:
        text = ask_vision(TABLE_PROMPT, raw)
        table = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception as exc:
        # Fall back to the local engine. The existence checks the whole spec is built
        # on must not depend on somebody else's rate limit; these grids are crisp
        # black-on-white text, which local OCR handles well.
        from src import ocr
        if not ocr.available():
            raise RuntimeError(
                f"vision failed ({exc}) and no local OCR installed "
                f"(pip install rapidocr-onnxruntime)"
            ) from exc
        table = ocr.read_table(shot)
        table["_engine"] = "local-ocr"

    _TABLE_CACHE[digest] = table
    return table

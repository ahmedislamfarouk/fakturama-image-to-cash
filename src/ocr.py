"""Local OCR reader for Fakturama's UIA-invisible grids.

Same contract as vision.read_table(): given a PNG of a grid, return

    {"columns": [...], "rows": [[cell, ...], ...], "row_y_pct": [...]}

Why this exists: SWT custom-paints the selector grids, so their rows are absent from
the accessibility tree and can only be read from pixels. Doing that through a hosted
vision model makes every existence check -- the question the whole spec is built on --
depend on an API quota. These grids are crisp black text on white at a known DPI,
which is the easiest possible OCR input, so a local engine removes that dependency
entirely. The hosted model stays as the primary because it handles the layout
reasoning (which cell belongs to which column) without being told.

Engine: rapidocr-onnxruntime (pip-installable ONNX; no system binary to install).
"""

from __future__ import annotations

from pathlib import Path

_ENGINE = None


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def _boxes(shot: Path) -> list[tuple[float, float, float, str]]:
    """(y_centre, x_left, x_right, text) for every detected fragment."""
    result, _ = _engine()(str(shot))
    out = []
    for box, text, _conf in (result or []):
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        out.append((sum(ys) / len(ys), min(xs), max(xs), str(text).strip()))
    return out


def read_table(shot: Path, row_tol: int = 8) -> dict:
    """Group OCR fragments into header + data rows by vertical position.

    Fragments whose vertical centres agree within `row_tol` pixels belong to the same
    visual row; within a row they are ordered left to right, which reproduces the
    column order. The first row is the header.
    """
    from PIL import Image

    with Image.open(shot) as im:
        height = im.height

    frags = _boxes(shot)
    if not frags:
        return {"columns": [], "rows": [], "row_y_pct": []}

    frags.sort(key=lambda f: f[0])
    lines: list[list[tuple[float, float, float, str]]] = []
    for f in frags:
        if lines and abs(f[0] - lines[-1][0][0]) <= row_tol:
            lines[-1].append(f)
        else:
            lines.append([f])

    grouped = []
    for line in lines:
        line.sort(key=lambda f: f[1])
        cells = [f[3] for f in line if f[3]]
        if cells:
            grouped.append((sum(f[0] for f in line) / len(line), cells))

    if not grouped:
        return {"columns": [], "rows": [], "row_y_pct": []}

    columns = grouped[0][1]
    data = grouped[1:]
    return {
        "columns": columns,
        "rows": [cells for _, cells in data],
        "row_y_pct": [round(100.0 * y / height, 2) for y, _ in data],
    }


def read_layout(shot: Path, row_tol: int = 8) -> dict:
    """Column and row geometry from real OCR bounding boxes.

    Same shape as vision.read_layout(), but the coordinates are MEASURED rather than
    estimated. A vision model asked for "the horizontal centre of this column as a
    percentage" guesses, and the guesses move between reads -- observed drifting by
    12-15 points on the same table, which is enough to type into the wrong cell.
    OCR returns the actual box of every header word, so the centre is exact.
    """
    from PIL import Image

    with Image.open(shot) as im:
        width, height = im.size

    frags = _boxes(shot)
    if not frags:
        return {"columns": [], "rows": []}

    frags.sort(key=lambda f: f[0])
    lines: list[list[tuple[float, float, float, str]]] = []
    for f in frags:
        if lines and abs(f[0] - lines[-1][0][0]) <= row_tol:
            lines[-1].append(f)
        else:
            lines.append([f])

    # A column spans from its header's LEFT edge to the next header's left edge.
    # Using the centre of the header TEXT instead puts the click near the column
    # boundary, because headers are left-aligned while values are right-aligned --
    # that landed on the row without opening a cell editor, so Qty. stayed 1.00.
    header = [h for h in sorted(lines[0], key=lambda f: f[1]) if h[3]]
    # The last column has no following header to bound it. Using the image edge puts
    # its centre far out in the empty area to the right of the table, so fall back to
    # the typical width of the columns before it.
    gaps = [header[i + 1][1] - header[i][1] for i in range(len(header) - 1)]
    typical = sorted(gaps)[len(gaps) // 2] if gaps else 0

    columns = []
    for i, (_y, x0, _x1, text) in enumerate(header):
        right = header[i + 1][1] if i + 1 < len(header) else min(width, x0 + typical)
        centre = (x0 + right) / 2
        columns.append({
            "name": text,
            "x_pct": round(100.0 * centre / width, 2),
            "x0_pct": round(100.0 * x0 / width, 2),
            "x1_pct": round(100.0 * right / width, 2),
        })

    rows = []
    for line in lines[1:]:
        line.sort(key=lambda f: f[1])
        cells = [f[3] for f in line if f[3]]
        if not cells:
            continue
        y = sum(f[0] for f in line) / len(line)
        rows.append({"y_pct": round(100.0 * y / height, 2), "cells": cells})

    return {"columns": columns, "rows": rows}

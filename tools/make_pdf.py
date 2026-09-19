"""Render DESIGN.md to DESIGN.pdf.

    python3 tools/make_pdf.py

Part 1 asks for "1-4 pages, no code", which is a document rather than a file in
a repository. The Markdown stays the source of truth -- it is what gets edited
and what the repo shows -- and this produces the artefact a reviewer prints.

Typography is set for reading on paper: a serif face at a comfortable measure,
tables that break across pages without losing their header, and links printed
in full so a printed copy is still usable.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import markdown
    from weasyprint import CSS, HTML
except ImportError:
    sys.exit("needs: pip install markdown weasyprint")

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "DESIGN.md")
DST = SRC.with_suffix(".pdf")

CSS_TEXT = """
@page {
  size: A4;
  margin: 15mm 15mm 14mm 15mm;
  @bottom-center {
    content: counter(page) " / " counter(pages);
    font: 8.5pt/1 "DejaVu Sans", sans-serif;
    color: #8a8f98;
  }
}
html { font-size: 9.4pt; }
body {
  font-family: "DejaVu Serif", Georgia, serif;
  line-height: 1.38;
  color: #16191d;
  hyphens: auto;
}
h1 {
  font-family: "DejaVu Sans", sans-serif;
  font-size: 17pt; line-height: 1.15; margin: 0 0 1.5mm;
  letter-spacing: -0.2pt;
}
h1 + p { color: #55606d; margin: 0 0 5mm; font-size: 9.2pt; }
h2 {
  font-family: "DejaVu Sans", sans-serif;
  font-size: 11.5pt; margin: 5.5mm 0 2mm;
  padding-bottom: 1.2mm; border-bottom: 0.5pt solid #d7dbe0;
  break-after: avoid;
}
h3 {
  font-family: "DejaVu Sans", sans-serif;
  font-size: 9.8pt; margin: 3.5mm 0 1.2mm; color: #2b3138;
  break-after: avoid;
}
p { margin: 0 0 2.1mm; }
strong { color: #000; }
a { color: #1a4d80; text-decoration: none; }
code, pre {
  font-family: "DejaVu Sans Mono", monospace;
  font-size: 8.4pt;
}
code { background: #f3f4f6; padding: 0.3mm 0.8mm; border-radius: 1pt; }
pre {
  background: #f7f8fa; border: 0.4pt solid #e2e5e9; border-left: 2pt solid #c8ced6;
  padding: 2.4mm 3mm; margin: 2.5mm 0; line-height: 1.35;
  break-inside: avoid; white-space: pre-wrap;
}
pre code { background: none; padding: 0; }
table {
  width: 100%; border-collapse: collapse; margin: 2mm 0 3mm;
  font-size: 8.2pt; font-family: "DejaVu Sans", sans-serif;
}
thead { display: table-header-group; }
th {
  text-align: left; font-weight: bold; font-size: 8.4pt;
  text-transform: uppercase; letter-spacing: 0.3pt; color: #4a525c;
  border-bottom: 0.9pt solid #b9c0c8; padding: 1.1mm 2.2mm 1.1mm 0;
}
td {
  vertical-align: top; padding: 1.1mm 2.2mm 1.1mm 0;
  border-bottom: 0.4pt solid #e6e9ed;
}
tr { break-inside: avoid; }
td:first-child, th:first-child { padding-left: 0; }
blockquote {
  margin: 3mm 0; padding-left: 3.5mm; border-left: 1.5pt solid #c8ced6;
  color: #3c434b; font-style: italic;
}
hr { border: none; border-top: 0.4pt solid #dfe3e8; margin: 4mm 0; }
"""


def main() -> None:
    if not SRC.exists():
        sys.exit(f"no {SRC}")

    html_body = markdown.markdown(
        SRC.read_text(),
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
    )
    html = f"<!doctype html><meta charset='utf-8'><body>{html_body}</body>"

    css = CSS_TEXT
    if "BRIEF" in SRC.stem.upper():
        # A one-pager is a different document, not a shrunken one: it loses the
        # rules and the generous section spacing that help a four-page read.
        css += """
        @page { margin: 13mm 14mm 11mm 14mm; }
        html { font-size: 9.0pt; }
        h2 { font-size: 10.6pt; margin: 3.6mm 0 1.4mm; border-bottom: none; }
        p { margin: 0 0 1.7mm; }
        table { margin: 1.6mm 0 2.4mm; }
        td, th { padding: 0.85mm 2.2mm 0.85mm 0; }
        ul { margin: 1.4mm 0; padding-left: 4mm; }
        li { margin: 0 0 1mm; }
        """
    HTML(string=html, base_url=str(SRC.parent.resolve())).write_pdf(
        DST, stylesheets=[CSS(string=css)]
    )

    size = DST.stat().st_size / 1024
    try:
        from pypdf import PdfReader
        pages = len(PdfReader(str(DST)).pages)
        print(f"{DST}  {pages} pages, {size:.0f}KB")
    except ImportError:
        print(f"{DST}  {size:.0f}KB")


if __name__ == "__main__":
    main()

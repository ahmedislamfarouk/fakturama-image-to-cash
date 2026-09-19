"""Order image -> validated OrderData.

Pure: no GUI, no Windows, no Fakturama. Runs and is tested on any platform.

Reads the image with an LLM vision model over an OpenAI-compatible endpoint, then
gates the result on the document's own arithmetic. The source document is
self-checking, so extraction correctness is decidable without a human: if the
four identities in `validate()` hold, the numbers were read correctly.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

CENT = Decimal("0.01")


def money(v) -> Decimal:
    """Money is Decimal, never float. 108.30 has no binary representation."""
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Address:
    name: str
    street: str
    zip: str
    city: str
    country: str
    extra: str = ""  # 'Address specification' / additional name, when supplied

    def same_as(self, other: "Address") -> bool:
        """Step 2.8 only assigns the Delivery role when the addresses are identical.

        Compared on normalized fields -- never assumed. In the supplied image they
        differ (Friedrichstrasse 88 / 10117 vs Beusselstrasse 44 / 10553), so an
        implementation that takes the shortcut unconditionally builds the wrong Debtor.
        """
        norm = lambda a: [  # noqa: E731
            " ".join(getattr(a, f).split()).casefold()
            for f in ("name", "street", "zip", "city", "country", "extra")
        ]
        return norm(self) == norm(other)


@dataclass
class OrderLine:
    sku: str
    description: str
    qty: int
    unit_net: Decimal
    discount_pct: Decimal
    vat_pct: Decimal
    line_net: Decimal

    @property
    def expected_line_net(self) -> Decimal:
        return money(self.qty * self.unit_net * (1 - self.discount_pct / 100))

    @property
    def product_gross_price(self) -> Decimal:
        """Step 3.9: Product master price. The line discount is deliberately NOT applied."""
        return money(self.unit_net * (1 + self.vat_pct / 100))

    @property
    def vat_name(self) -> str:
        """Step 3.5/3.6: 'VAT ' followed by the percentage, e.g. 'VAT 19%'."""
        pct = self.vat_pct.normalize()
        return f"VAT {pct:f}%"


@dataclass
class OrderData:
    external_ref: str
    order_date: date
    customer_id: str
    currency: str
    company: str
    contact_first_name: str
    contact_last_name: str
    alias: str
    email: str
    phone: str
    billing: Address
    delivery: Address
    payment_method: str
    paid: bool
    payment_date: date | None
    lines: list[OrderLine]
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal
    warnings: list[str] = field(default_factory=list)

    @property
    def delivery_same_as_billing(self) -> bool:
        return self.billing.same_as(self.delivery)


def validate(o: OrderData) -> list[str]:
    """The four identities. Empty list means the read is arithmetically sound.

    Blind to errors that preserve the identities -- a misread SKU or city passes here
    and is caught later by Fakturama's selector returning no exact match.
    """
    errs = []
    for i, ln in enumerate(o.lines, 1):
        if ln.line_net != ln.expected_line_net:
            errs.append(
                f"line {i} ({ln.sku}): stated net {ln.line_net} != "
                f"{ln.qty} x {ln.unit_net} x (1 - {ln.discount_pct}/100) = {ln.expected_line_net}"
            )
    net = money(sum(ln.line_net for ln in o.lines))
    if net != o.net_total:
        errs.append(f"net total: stated {o.net_total} != sum of lines {net}")
    vat = money(sum(ln.line_net * ln.vat_pct / 100 for ln in o.lines))
    if vat != o.vat_total:
        errs.append(f"VAT total: stated {o.vat_total} != computed {vat}")
    if money(o.net_total + o.vat_total) != o.gross_total:
        errs.append(
            f"gross total: stated {o.gross_total} != {o.net_total} + {o.vat_total}"
        )
    if o.paid and o.payment_date is None:
        errs.append("paid status is PAID but no payment date was read")
    if not o.paid and o.payment_date is not None:
        errs.append("payment date present but paid status is not PAID (step 5.3)")
    return errs


SCHEMA_PROMPT = """You are reading a sales order document. Return ONLY a JSON object,
no prose and no markdown fence, with exactly these keys:

{
  "external_ref": str, "order_date": "YYYY-MM-DD", "customer_id": str, "currency": str,
  "company": str, "contact_first_name": str, "contact_last_name": str,
  "alias": str, "email": str, "phone": str,
  "billing":  {"name": str, "street": str, "zip": str, "city": str, "country": str, "extra": str},
  "delivery": {"name": str, "street": str, "zip": str, "city": str, "country": str, "extra": str},
  "payment_method": str, "paid": bool, "payment_date": "YYYY-MM-DD" or null,
  "lines": [{"sku": str, "description": str, "qty": int, "unit_net": str,
             "discount_pct": str, "vat_pct": str, "line_net": str}],
  "net_total": str, "vat_total": str, "gross_total": str
}

Rules:
- All monetary and percentage values as decimal STRINGS ("250.00", "19", "10"). Never numbers.
- Percentages without the % sign.
- Split the contact name into first and last name.
- "extra" is any additional address line (warehouse name, c/o, department); "" if absent.
- The address block's first line is the recipient name -> "name".
- "paid" is true only if the document's paid status reads PAID.
- Read every item row. Do not invent rows for the blank filler rows in the table.
"""


def _parse(d: dict) -> OrderData:
    addr = lambda k: Address(**{  # noqa: E731
        f: str(d[k].get(f, "")).strip() for f in ("name", "street", "zip", "city", "country", "extra")
    })
    return OrderData(
        external_ref=d["external_ref"].strip(),
        order_date=date.fromisoformat(d["order_date"]),
        customer_id=d["customer_id"].strip(),
        currency=d["currency"].strip(),
        company=d["company"].strip(),
        contact_first_name=d["contact_first_name"].strip(),
        contact_last_name=d["contact_last_name"].strip(),
        alias=d["alias"].strip(),
        email=d["email"].strip(),
        phone=d["phone"].strip(),
        billing=addr("billing"),
        delivery=addr("delivery"),
        payment_method=d["payment_method"].strip(),
        paid=bool(d["paid"]),
        payment_date=date.fromisoformat(d["payment_date"]) if d.get("payment_date") else None,
        lines=[
            OrderLine(
                sku=l["sku"].strip(),
                description=l["description"].strip(),
                qty=int(l["qty"]),
                unit_net=money(l["unit_net"]),
                discount_pct=Decimal(str(l["discount_pct"])),
                vat_pct=Decimal(str(l["vat_pct"])),
                line_net=money(l["line_net"]),
            )
            for l in d["lines"]
        ],
        net_total=money(d["net_total"]),
        vat_total=money(d["vat_total"]),
        gross_total=money(d["gross_total"]),
    )


def _vision(image: Path, extra_instruction: str = "") -> dict:
    """One POST to any OpenAI-compatible /chat/completions endpoint.

    Configured by env so the same code path serves OpenAI, OpenRouter, Gemini's
    compat endpoint, or a local model -- see README.
    """
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    if not key:
        raise RuntimeError(
            "No LLM_API_KEY / OPENAI_API_KEY set. Set one, or pass --cached to reuse "
            "a previous extraction from input/order.json."
        )
    mime = mimetypes.guess_type(image.name)[0] or "image/png"
    data_uri = f"data:{mime};base64," + base64.b64encode(image.read_bytes()).decode()
    body = json.dumps({
        "model": model,
        "temperature": 0,
        # No response_format: not every OpenAI-compatible gateway supports it (some
        # 400 on it), and the brace slice below handles fenced or chatty output anyway.
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": SCHEMA_PROMPT + extra_instruction},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]}],
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # opencode Zen sits behind Cloudflare, which rejects urllib's default
            # User-Agent with 403 / error 1010. Any browser-shaped UA passes.
            "User-Agent": os.environ.get("LLM_USER_AGENT", "Mozilla/5.0"),
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def extract(image: Path, cache: Path | None = None, use_cache: bool = False) -> OrderData:
    """Read the image, then gate on arithmetic. One re-prompt on failure, then halt.

    `cache` lets a reviewer without an API key still exercise the UI flow.
    """
    if use_cache and cache and cache.exists():
        return _parse(json.loads(cache.read_text()))

    raw = _vision(image)
    order = _parse(raw)
    errs = validate(order)
    if errs:
        order = _parse(_vision(image, "\n\nYour previous read failed these checks. "
                                      "Re-read the affected values carefully:\n- " + "\n- ".join(errs)))
        errs = validate(order)
        if errs:
            raise ValueError("Extraction failed arithmetic validation twice:\n  " + "\n  ".join(errs))

    if cache:
        cache.write_text(json.dumps(raw, indent=2))
    return order


def to_dict(o: OrderData) -> dict:
    return json.loads(json.dumps(asdict(o), default=str))

"""Checks on the arithmetic gate and the derived values the UI flow types in.

Run: python -m tests.test_extract   (no pytest needed)
"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.extract import Address, OrderData, OrderLine, money, validate  # noqa: E402


def sample() -> OrderData:
    """The supplied order image, transcribed by hand. The fixture is the ground truth."""
    return OrderData(
        external_ref="WEB-2026-0714-A17",
        order_date=date(2026, 7, 14),
        customer_id="CUST-1007",
        currency="EUR",
        company="Northstar Office GmbH",
        contact_first_name="Marta",
        contact_last_name="Klein",
        alias="NORTHSTAR-BERLIN",
        email="marta.klein@example.test",
        phone="+49 30 5550 1420",
        billing=Address("Northstar Office GmbH", "Friedrichstrasse 88", "10117", "Berlin", "Germany"),
        delivery=Address("Northstar Office Warehouse", "Beusselstrasse 44", "10553", "Berlin", "Germany"),
        payment_method="Bank Transfer",
        paid=True,
        payment_date=date(2026, 7, 18),
        lines=[
            OrderLine("CHR-ERG-01", "Ergonomic Desk Chair", 2, money(250), Decimal(10), Decimal(19), money("450.00")),
            OrderLine("MAT-DESK-02", "Anti-Fatigue Desk Mat", 3, money(40), Decimal(0), Decimal(19), money("120.00")),
        ],
        net_total=money("570.00"),
        vat_total=money("108.30"),
        gross_total=money("678.30"),
    )


def main() -> None:
    o = sample()

    assert validate(o) == [], validate(o)

    # Step 3.16: line price identity, per line.
    assert o.lines[0].expected_line_net == money("450.00")
    assert o.lines[1].expected_line_net == money("120.00")

    # Step 3.9: Product master gross price. The line discount must NOT be applied --
    # 250 x 1.19 = 297.50, not 225 x 1.19 = 267.75.
    assert o.lines[0].product_gross_price == money("297.50")
    assert o.lines[1].product_gross_price == money("47.60")

    # Step 3.4/3.6: the VAT record name the Data > VATs lookup searches for.
    assert o.lines[0].vat_name == "VAT 19%"

    # Step 2.8: billing and delivery differ here, so the Delivery role must NOT be
    # assigned to the Main address. Getting this backwards builds the wrong Debtor.
    assert o.delivery_same_as_billing is False
    identical = sample()
    identical.delivery = identical.billing
    assert identical.delivery_same_as_billing is True

    # A misread digit has to break an identity, or the gate is worthless.
    bad = sample()
    bad.lines[0].line_net = money("460.00")
    assert len(validate(bad)) == 3  # line, net total, VAT total

    bad = sample()
    bad.vat_total = money("108.00")
    assert any("VAT total" in e for e in validate(bad))

    # Step 5.3: never invent a payment date, never drop one.
    bad = sample()
    bad.payment_date = None
    assert any("no payment date" in e for e in validate(bad))

    bad = sample()
    bad.paid = False
    assert any("not PAID" in e for e in validate(bad))

    print("ok - 11 checks passed")


if __name__ == "__main__":
    main()

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

    # Grid cells come from pixels and Fakturama truncates them to column width, so
    # the exact-match rule has to cope with an elided tail without going fuzzy.
    from src.flow import _exact, _cell_matches
    want = ["Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin"]
    shown = [["1", "CUST000001", "Marta", "Klein", "Northstar Office ...", "10117", "Berlin", "", ""]]
    assert _exact(shown, want) == [0]
    assert _exact([["2", "CUST000002", "Jan", "Weber", "Southstar ...", "20095", "Hamburg"]], want) == []
    assert _cell_matches("Northstar Office ...", "Northstar Office GmbH")
    assert not _cell_matches("Northstar Office AG", "Northstar Office GmbH")
    assert not _cell_matches("", "Northstar Office GmbH")

    # The paid date is written into a segmented widget that ignores non-digits
    # and fills segments in display order. Typing the rendered string put
    # 'Sep 20, 0026' in the field while the log still claimed 5.3 ok, so the
    # digit order has to follow the format the widget is currently rendering.
    from src.driver import Driver
    assert Driver._date_format("Jul 18, 2026") == "%b %d, %Y"
    assert Driver._date_format("18.07.2026") == "%d.%m.%Y"
    assert Driver._parse_date("Jul 18, 2026") == date(2026, 7, 18)
    assert Driver._parse_date("18.07.2026") == date(2026, 7, 18)
    assert Driver._parse_date("not a date") is None

    # US rendering wants month first, German rendering wants day first --
    # same date, different keystrokes. And 'Jul' is typed as '07', so the
    # digits must not be scraped out of the rendered string.
    assert Driver._date_digits(date(2026, 7, 18), "Sep 19, 2026") == "07182026"
    assert Driver._date_digits(date(2026, 7, 18), "19.09.2026") == "18072026"
    assert Driver._date_digits(date(2026, 7, 18), "2026-09-19") == "20260718"

    # A field that reads back its own label means nothing was typed into it.
    # An entire Product was created empty because this only produced a warning.
    from src.driver import _landed, _same_value, _digits

    class FakeEl:
        def __init__(self, name):
            self.element_info = type("I", (), {"name": name})()

    itemno = FakeEl("Item Number")
    assert not _landed("Item Number", "MAT-DESK-02", itemno)   # the label: nothing landed
    assert not _landed("", "MAT-DESK-02", itemno)              # empty: nothing landed
    assert _landed("MAT-DESK-02", "MAT-DESK-02", itemno)

    # ...but Fakturama's own rendering is not a failure.
    price = FakeEl("Price")
    assert _landed("$678.30", "678.30", price)
    assert _landed("0%", "0", price)
    assert _digits("$1.234,50") == "123450"
    assert _same_value("678.30", "678.30")
    assert not _same_value("$678.30", "678.30")

    print("ok - 31 checks passed")


if __name__ == "__main__":
    main()

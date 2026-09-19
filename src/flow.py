"""The five stages, transcribed from the spec so they read next to the PDF.

One continuous Order-first flow. The Order tab opens first (1.3) and stays open
through every master-data detour (1.8) -- creation branches are side trips, and each
one ends by returning to the same Order and re-selecting through the Order's own
picker. Selection IS the existence check; re-selection IS the save confirmation.

Step numbers in comments map to the spec. Where the spec says "stop for manual
review", this raises AmbiguityHalt and the run exits non-zero with a screenshot.
"""

from __future__ import annotations

from decimal import Decimal

from src.driver import AmbiguityHalt, Driver
from src.extract import OrderData, OrderLine, money

# 2.10.4 -- the documented payment-code mapping. Nothing else is guessed.
PAYMENT_CODE = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def _rows(d: Driver, grid_logical: str) -> list[list[str]]:
    """Read a selector dialog's result grid.

    grid_logical is kept for call-site readability, but Fakturama's selector grids are
    custom-painted and expose nothing to UIA (no Table/DataGrid/List/DataItem), so the
    rows are read from the grid rectangle's pixels. See driver.grid_rows().
    """
    return d.grid_rows()


def _exact(rows: list[list[str]], expected: list[str]) -> list[int]:
    """Indices of rows whose cells contain every expected value, normalized.

    2.3 / 3.3 / 3.5: exactness is the whole point. One hit continues, zero branches
    to creation, more than one halts.
    """
    want = [_norm(v) for v in expected if str(v).strip()]
    hits = []
    for i, cells in enumerate(rows):
        have = {_norm(c) for c in cells}
        if all(any(w == h for h in have) for w in want):
            hits.append(i)
    return hits


def _select_exact(d: Driver, *, what: str, search: str, expected: list[str],
                  search_logical: str, grid_logical: str,
                  ok_logical: str, cancel_logical: str) -> bool:
    """Shared shape of 2.2-2.3 and 3.3: search, stabilize, judge, act.

    Returns True when an exact row was selected, False when none existed (caller
    takes the creation branch). Raises AmbiguityHalt when more than one matched.
    """
    d.set_text(search_logical, search)
    d.wait_stable(grid_logical)          # 2.2 "wait for the list to stabilize"
    rows = _rows(d, grid_logical)
    hits = _exact(rows, expected)

    if len(hits) > 1:
        shot = d.screenshot(f"halt-{what}")
        raise AmbiguityHalt(what, search, [" | ".join(rows[i]) for i in hits], shot)
    if not hits:
        d.click(cancel_logical)           # 2.3 / 3.3 "click Cancel and continue"
        d.note(f"{what}: no exact match for {search!r} -> creation branch")
        return False

    grid = d.find(grid_logical)
    grid.descendants(control_type="DataItem")[hits[0]].click_input()
    d.click(ok_logical)
    d.note(f"{what}: selected exact match for {search!r}")
    return True


# --------------------------------------------------------------------------
# 1. Extract the image and open a New Order
# --------------------------------------------------------------------------

def stage1_open_order(d: Driver, o: OrderData) -> None:
    d.click("toolbar.order")                       # 1.3
    d.wait_exists("order.custref")                 # wait for the New Order editor
    # 1.4 -- the proposed No. is left untouched on purpose.
    d.set_text("order.date", o.order_date.isoformat())      # 1.5
    d.set_text("order.custref", o.external_ref)             # 1.6
    d.select("order.pricemode", "Net")                      # 1.7 (defaults to 'Gross')
    d.select("order.vat_with", "With VAT")                  # 1.7 (already the default)
    d.wait_value("order.custref", o.external_ref)
    d.note(f"stage 1 ok -- Order open, Cust.Ref. {o.external_ref}, date {o.order_date}")


# --------------------------------------------------------------------------
# 2. Select or create the Debtor
# --------------------------------------------------------------------------

def stage2_debtor(d: Driver, o: OrderData) -> None:
    if _open_address_picker_and_select(d, o):
        _confirm_addresses(d, o)                   # 2.4
        return

    _create_debtor(d, o)                           # 2.5 - 2.11

    # 2.12 -- back to the still-open Order; successful re-selection proves the save.
    if not _open_address_picker_and_select(d, o):
        raise AmbiguityHalt("debtor.reselect", o.company,
                            ["saved Debtor not selectable from the Order"],
                            d.screenshot("halt-debtor-reselect"))
    _confirm_addresses(d, o)                       # 2.13


def _open_address_picker_and_select(d: Driver, o: OrderData) -> bool:
    d.click("address_picker.open")                 # 2.1 upper icon, never the green +
    d.scope_window(".*Select the address.*")
    try:
        return _select_exact(
            d, what="debtor", search=o.company,
            # 2.3 -- exact means Company, First Name, Name, ZIP and City all match.
            expected=[o.company, o.contact_first_name, o.contact_last_name,
                      o.billing.zip, o.billing.city],
            search_logical="address_dlg.search", grid_logical="address_dlg.list",
            ok_logical="address_dlg.ok", cancel_logical="address_dlg.cancel",
        )
    finally:
        d.scope_main()


def _confirm_addresses(d: Driver, o: OrderData) -> None:
    """2.4 / 2.13 -- the populated addresses must match the source image."""
    inv = d.read_text("order.invoice_addr")
    dlv = d.read_text("order.delivery_addr")
    for field, blob, label in ((o.billing, inv, "invoice"), (o.delivery, dlv, "delivery")):
        for part in (field.street, field.zip, field.city):
            if _norm(part) not in _norm(blob):
                raise AmbiguityHalt(f"address.{label}", part, [blob],
                                    d.screenshot(f"halt-address-{label}"))
    d.note("stage 2 ok -- Debtor selected, both addresses match the source")


def _create_debtor(d: Driver, o: OrderData) -> None:
    d.click("new.contact")                         # 2.5 -- Order tab stays open
    d.wait_exists("debtor.company")
    # 2.6 -- proposed Customer ID untouched; Salutation left as '---' when unsupplied.
    d.set_text("debtor.company", o.company)
    d.set_text("debtor.firstname", o.contact_first_name)
    d.set_text("debtor.lastname", o.contact_last_name)

    d.open_tab("debtor.tab_addresses", "debtor.street")   # 2.7 Addresses > Main address
    d.set_text("debtor.street", o.billing.street)
    d.set_text("debtor.zip", o.billing.zip)
    d.set_text("debtor.city", o.billing.city)
    d.set_text("debtor.country", o.billing.country)
    d.set_text("debtor.email", o.email)
    d.set_text("debtor.phone", o.phone)

    # 2.8 -- the roles live in the 'address type' field, not in checkboxes: Fakturama
    # 2.2.0 has no CheckBox anywhere in its shell, and the field's own dropdown popup
    # is custom-painted and invisible to UIA. It does accept the role name as text.
    #
    # The Delivery role goes on the Main address ONLY when the two addresses are
    # identical. On the supplied image they are not (Friedrichstrasse 88 / 10117 vs
    # Beusselstrasse 44 / 10553), so taking the shortcut would build the wrong Debtor.
    roles = ["Invoice address"]
    if o.delivery_same_as_billing:
        roles.append("Delivery address")
    else:
        d.note("2.8: delivery differs from billing -- Delivery role NOT assigned to Main address")
    d.set_text("debtor.addrtype", ", ".join(roles))

    d.open_tab("debtor.tab_misc", "debtor.alias")  # 2.9
    d.set_text("debtor.alias", o.alias)
    d.set_text("debtor.discount", "0")
    d.select("debtor.netgross", "Net")

    # 2.10 -- there is no separate Payment tab in Fakturama 2.2.0: Alias name,
    # Discount, Net or Gross and Payment all live on Miscellaneous, so 2.9 and 2.10
    # happen without leaving this tab.
    if not _try_select(d, "debtor.payment", o.payment_method):
        _create_payment_method(d, o.payment_method)
        # 2.10.6 -- the payment editor is now in front; come back to the Debtor
        # editor before its inner tabs exist again.
        d.click("debtor.editor_tab")
        d.open_tab("debtor.tab_misc", "debtor.alias")
        if not _try_select(d, "debtor.payment", o.payment_method):
            raise AmbiguityHalt("payment.reselect", o.payment_method, ["not selectable after save"],
                                d.screenshot("halt-payment-reselect"))

    d.click("order.save")                          # 2.11 -- once
    d.note(f"Debtor created: {o.company}")


def _try_select(d: Driver, logical: str, value: str) -> bool:
    try:
        d.select(logical, value)
        return True
    except Exception:
        return False


def _create_payment_method(d: Driver, method: str) -> None:
    """2.10.1 - 2.10.6. The Debtor editor stays open throughout."""
    code = PAYMENT_CODE.get(_norm(method))
    if code is None:
        raise AmbiguityHalt("payment.code", method, sorted(PAYMENT_CODE),
                            d.screenshot("halt-payment-code"))

    d.click("menu.payments")                       # 2.10.1
    d.set_text("list.search", method)
    d.wait_stable("address_dlg.list")
    rows = _rows(d, "address_dlg.list")
    hits = _exact(rows, [method])
    if len(hits) > 1:                              # 2.10.2 conflicting definitions
        raise AmbiguityHalt("payment.lookup", method, [" | ".join(rows[i]) for i in hits],
                            d.screenshot("halt-payment-lookup"))
    if hits:
        d.note(f"payment method {method!r} already exists -- reusing")
        return

    d.click("payment.add")                         # 2.10.2 green + upper-right
    d.set_text("payment.name", method)             # 2.10.3
    d.set_text("payment.description", method)      # Account deliberately left blank
    d.select("payment.code", code)                 # 2.10.4
    for f in ("payment.cash_discount", "payment.discount_days", "payment.net_days"):
        d.set_text(f, "0")                         # 2.10.5
    # Text 'unpaid'/'deposit'/'paid' stay blank; 'Set as standard' is never clicked.
    d.click("order.save")                          # 2.10.6 -- once
    d.note(f"payment method created: {method} -> {code}")


# --------------------------------------------------------------------------
# 3. Select or create each Product, then complete its line
# --------------------------------------------------------------------------

def stage3_products(d: Driver, o: OrderData) -> None:
    for n, line in enumerate(o.lines, 1):          # 3.1 -- in source order
        d.note(f"-- item {n}/{len(o.lines)}: {line.sku}")
        if not _open_product_picker_and_select(d, line):
            _ensure_vat(d, line)                   # 3.4 - 3.6, BEFORE New product
            _create_product(d, line)               # 3.7 - 3.11
            if not _open_product_picker_and_select(d, line):
                raise AmbiguityHalt("product.reselect", line.sku,
                                    ["saved Product not selectable from the Order"],
                                    d.screenshot(f"halt-product-{line.sku}"))
        _complete_line(d, line)                    # 3.13 - 3.16


def _open_product_picker_and_select(d: Driver, line: OrderLine) -> bool:
    d.click("product_picker.open")                 # 3.2 upper icon, never the green +
    d.scope_window(".*Select a product.*")
    try:
        return _select_exact(
            d, what=f"product:{line.sku}", search=line.sku, expected=[line.sku],
            search_logical="product_dlg.search", grid_logical="address_dlg.list",
            ok_logical="product_dlg.ok", cancel_logical="product_dlg.cancel",
        )
    finally:
        d.scope_main()


def _ensure_vat(d: Driver, line: OrderLine) -> None:
    """3.4 - 3.6. Reuse only on a full three-way match; otherwise create, else halt."""
    pct = f"{line.vat_pct.normalize():f}"
    d.click("menu.vats")                           # 3.4
    d.set_text("list.search", line.vat_name)
    d.wait_stable("address_dlg.list")
    rows = _rows(d, "address_dlg.list")

    # 3.5 -- Name == 'VAT <pct>%', Value == pct, and VAT code == S (Standard rate).
    hits = _exact(rows, [line.vat_name])
    if len(hits) > 1:
        raise AmbiguityHalt("vat.lookup", line.vat_name, [" | ".join(rows[i]) for i in hits],
                            d.screenshot("halt-vat-lookup"))
    if hits:
        cells = {_norm(c) for c in rows[hits[0]]}
        if not any(pct in c for c in cells) or not any("standard" in c or c == "s" for c in cells):
            raise AmbiguityHalt("vat.conflict", line.vat_name, [" | ".join(rows[hits[0]])],
                                d.screenshot("halt-vat-conflict"))
        d.note(f"VAT {line.vat_name!r} exists and matches -- reusing")
        return

    d.click("vat.add")                             # 3.6
    d.set_text("vat.name", line.vat_name)
    d.set_text("vat.description", line.vat_name)
    d.select("vat.code", "S (Standard rate)")
    d.set_text("vat.value", pct)
    # 'Standard VAT' display is left unchanged.
    d.click("order.save")                          # once
    d.note(f"VAT created: {line.vat_name}")


def _create_product(d: Driver, line: OrderLine) -> None:
    d.click("new.product")                         # 3.7 -- only after the VAT exists
    d.wait_exists("product.itemno")
    d.set_text("product.itemno", line.sku)         # 3.8
    d.set_text("product.name", line.description)
    d.set_text("product.description", line.description)
    # 3.9 -- master price is unit_net x (1 + vat/100). The LINE discount is NOT applied:
    # 250.00 -> 297.50, not 225.00 -> 267.75.
    d.set_text("product.price_gross", f"{line.product_gross_price:.2f}")
    d.set_text("product.cost_price", "0.00")       # 3.10
    d.select("product.vat", line.vat_name)
    d.set_text("product.stock", "0.00")
    # Category, GTIN, supplier code, allowance, picture and udf1 are left untouched.
    d.click("order.save")                          # 3.11 -- once
    d.note(f"Product created: {line.sku} @ {line.product_gross_price} gross")


def _complete_line(d: Driver, line: OrderLine) -> None:
    row = d.find("order.items").parent()
    d.set_text("order.qty", line.qty, scope=row)                        # 3.13
    d.set_text("order.uprice", f"{line.unit_net:.2f}", scope=row)       # 3.14
    d.select("order.vat", line.vat_name, scope=row)
    d.set_text("order.discount", f"{line.discount_pct:f}", scope=row)   # 3.15

    # 3.16 -- qty x unit net x (1 - discount/100), checked not assumed.
    shown = money(d.read_text("order.line_price", scope=row).replace(",", "."))
    if shown != line.expected_line_net:
        raise AmbiguityHalt(f"line.{line.sku}", "line price",
                            [f"shown {shown} != expected {line.expected_line_net}"],
                            d.screenshot(f"halt-line-{line.sku}"))
    d.note(f"line ok: {line.sku} -> {shown}")


# --------------------------------------------------------------------------
# 4. Complete and save the Order
# --------------------------------------------------------------------------

def stage4_save_order(d: Driver, o: OrderData) -> None:
    # 4.2 -- order-level Discount and Shipping stay at their defaults; this image
    # supplies no order-level values.
    _check_total(d, "order.total_net", o.net_total, "Total Net")        # 4.3
    _check_total(d, "order.total_vat", o.vat_total, "VAT")
    _check_total(d, "order.total", o.gross_total, "Total")

    d.click("order.save")                                                # 4.4
    d.click("menu.documents")                                            # 4.5
    d.set_text("list.search", o.external_ref)
    d.wait_stable("address_dlg.list")
    rows = _rows(d, "address_dlg.list")
    if not _exact(rows, [o.external_ref]):
        raise AmbiguityHalt("order.saved", o.external_ref, [str(r) for r in rows[:5]],
                            d.screenshot("halt-order-saved"))
    d.screenshot("04-order-saved")
    d.note(f"stage 4 ok -- Order saved, totals {o.net_total}/{o.vat_total}/{o.gross_total}")


def _check_total(d: Driver, logical: str, expected: Decimal, label: str) -> None:
    shown = money(d.read_text(logical).replace("EUR", "").replace(",", ".").strip())
    if shown != expected:
        raise AmbiguityHalt(f"total.{label}", label, [f"shown {shown} != source {expected}"],
                            d.screenshot(f"halt-total-{label.replace(' ', '-')}"))


# --------------------------------------------------------------------------
# 5. Complete and verify the linked Invoice
# --------------------------------------------------------------------------

def stage5_invoice(d: Driver, o: OrderData) -> None:
    # 4.6 -- the follow-up action, NOT the top toolbar Invoice button. Only the
    # follow-up preserves the Order relationship.
    d.click("order.followup_invoice")
    d.wait_exists("invoice.payment")                                     # 4.7

    # 5.1 -- proposed Invoice No., Invoice Date and Service date left unchanged;
    # confirm what was copied down from the Order.
    if _norm(d.read_text("order.custref")) != _norm(o.external_ref):
        raise AmbiguityHalt("invoice.custref", o.external_ref, [d.read_text("order.custref")],
                            d.screenshot("halt-invoice-custref"))
    _check_total(d, "order.total", o.gross_total, "Total")

    if not _try_select(d, "invoice.payment", o.payment_method):          # 5.2
        raise AmbiguityHalt("invoice.payment", o.payment_method, ["not available on the Invoice"],
                            d.screenshot("halt-invoice-payment"))

    if o.paid:                                                           # 5.3
        d.click("invoice.paid")
        d.set_text("invoice.paid_date", o.payment_date.isoformat())
        d.set_text("invoice.paid_value", f"{o.gross_total:.2f}")
    else:
        d.note("5.3: not PAID -- leaving paid clear, inventing no date or value")

    d.click("order.save")                                                # 5.4
    d.click("menu.documents")                                            # 5.5
    d.set_text("list.search", o.external_ref)
    d.wait_stable("address_dlg.list")
    rows = _rows(d, "address_dlg.list")
    kinds = {_norm(c) for r in rows for c in r}
    if not ({"invoice"} & kinds) or not ({"order"} & kinds):
        raise AmbiguityHalt("invoice.saved", o.external_ref, [str(r) for r in rows[:5]],
                            d.screenshot("halt-invoice-saved"))
    d.screenshot("05-invoice-verified")
    # 5.7 -- the flow ends here. No Delivery, Correction or Dunning document.
    d.note("stage 5 ok -- Invoice saved and verified; source Order still open")


def run(d: Driver, o: OrderData) -> None:
    stage1_open_order(d, o)
    stage2_debtor(d, o)
    stage3_products(d, o)
    stage4_save_order(d, o)
    stage5_invoice(d, o)

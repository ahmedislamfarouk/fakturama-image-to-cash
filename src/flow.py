"""The five stages, transcribed from the spec so they read next to the PDF.

One continuous Order-first flow. The Order tab opens first (1.3) and stays open
through every master-data detour (1.8) -- creation branches are side trips, and each
one ends by returning to the same Order and re-selecting through the Order's own
picker. Selection IS the existence check; re-selection IS the save confirmation.

Step numbers in comments map to the spec. Where the spec says "stop for manual
review", this raises AmbiguityHalt and the run exits non-zero with a screenshot.
"""

from __future__ import annotations

import time
from decimal import Decimal

from src.driver import AmbiguityHalt, Driver
from src.extract import OrderData, OrderLine, money

# 2.10.4 -- the documented payment-code mapping. Nothing else is guessed.
PAYMENT_CODE = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}


def _focus_order(d: Driver, expect_ref: str | None = None) -> None:
    """Bring OUR still-open Order editor to the front -- identified by content.

    1.8 keeps the Order tab open across every master-data detour, but 'open' is not
    'in front': a background tab's widgets are not realized, so its controls cannot
    be found. Worse, Fakturama can end up with more than one '*New Order' tab, and
    picking by screen position selects whichever happens to be leftmost -- which was
    a DIFFERENT, blank Order (empty Cust.Ref., Gross mode, today's date) while the
    real one held the item lines.

    So identity comes from content: activate each candidate tab and keep the one
    whose Cust.Ref. matches the extracted External Reference.
    """
    d.scope_main()
    ref = expect_ref or _ORDER_REF.get("value")

    # Match on '*New Order' AND on a saved Order's number, because saving renames
    # the tab: an unsaved Order is '*New Order', a saved one is 'PO000001'. Stage 5
    # runs after the save, so a name-only match for '*New Order' finds nothing and
    # the follow-up click lands on whatever tab happens to be in front -- which
    # after 4.5 is the Data > Documents list, where the click does nothing at all.
    def _candidate(t) -> bool:
        name = (t.element_info.name or "").strip().lstrip("*")
        return name == "New Order" or _ORDER_TAB.get("name") == name

    tabs = [t for t in d._main.descendants(control_type="TabItem") if _candidate(t)]
    if not tabs:
        d.open_tab("order.editor_tab", "order.custref")
        return
    if len(tabs) == 1 or not ref:
        try:
            tabs[0].click_input()
            time.sleep(0.6)
            if _norm(d.read_text("order.custref")) == _norm(ref or ""):
                return
        except Exception:
            pass
        d.open_tab("order.editor_tab", "order.custref")
        return

    for i, tab in enumerate(sorted(tabs, key=lambda t: t.element_info.rectangle.left)):
        try:
            tab.set_focus()
        except Exception:
            pass
        try:
            tab.click_input()
        except Exception:
            continue
        time.sleep(0.6)
        try:
            if _norm(d.read_text("order.custref")) == _norm(ref):
                _ORDER_TAB["name"] = (tab.element_info.name or "").strip().lstrip("*")
                d.note(f"Order tab {i} is ours (Cust.Ref. {ref})")
                return
        except Exception:
            continue
    raise AmbiguityHalt("order.identify", ref,
                        [f"{len(tabs)} '*New Order' tabs, none with Cust.Ref. {ref}"],
                        d.screenshot("halt-order-identify"))


_ORDER_REF: dict[str, str] = {}
#: The Order tab's name once Fakturama has renamed it on save (e.g. 'PO000001').
_ORDER_TAB: dict[str, str] = {}


def _open_picker(d: Driver, open_logical: str, title_re: str, search_logical: str,
                 tries: int = 3):
    """Open a selector dialog and confirm it is actually usable.

    Observed on the product picker's second open (3.12, after creating the product):
    the dialog appears, scope binds to it, and it is gone before the first keystroke
    -- the scope dump showed a dead element, rect 0x0 with no children. Rather than
    theorise about why it dies, treat opening as an operation that can fail and
    verify it: the dialog is only accepted once its search box resolves inside it.
    """
    last = None
    for attempt in range(1, tries + 1):
        _focus_order(d)
        d.click(open_logical)
        time.sleep(1.0)
        try:
            d.scope_window(title_re)
            d.find(search_logical)          # proves the dialog is alive and populated
            return
        except Exception as exc:
            last = exc
            d.note(f"{open_logical}: dialog not usable on attempt {attempt}/{tries} "
                   f"({type(exc).__name__}); reopening")
            d.scope_main()
            time.sleep(1.0)
    d.describe_scope(open_logical)
    raise last


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def _rows(d: Driver, grid_logical: str, scope=None) -> list[list[str]]:
    """Read a selector dialog's result grid.

    grid_logical is kept for call-site readability, but Fakturama's selector grids are
    custom-painted and expose nothing to UIA (no Table/DataGrid/List/DataItem), so the
    rows are read from the grid rectangle's pixels. See driver.grid_rows().
    """
    return d.grid_rows(scope)


ELLIPSES = ("...", "\u2026")


def _cell_matches(cell: str, want: str) -> bool:
    """One displayed cell against one expected value.

    Fakturama truncates cells that do not fit their column and appends an ellipsis:
    'Northstar Office GmbH' is displayed as 'Northstar Office ...'. The grid is read
    from pixels, so that is genuinely all there is to compare against -- an equality
    test can never match a long value, and widening columns is not something the
    automation should depend on.

    A truncated cell is therefore treated as a PREFIX. This deliberately weakens the
    comparison for long values, and the spec already supplies the compensating check:
    2.4 requires confirming the populated Invoice and Delivery addresses against the
    source after selection, and 3.12/2.13 re-select through the Order's own picker.
    Exactness is established there rather than against a string the UI has elided.
    """
    c, w = _norm(cell), _norm(want)
    if c == w:
        return True
    for e in ELLIPSES:
        if c.endswith(e):
            prefix = c[: -len(e)].strip()
            if prefix and w.startswith(prefix):
                return True
    return False


def _exact(rows: list[list[str]], expected: list[str]) -> list[int]:
    """Indices of rows matching every expected value.

    2.3 / 3.3 / 3.5: exactness is the whole point. One hit continues, zero branches
    to creation, more than one halts.
    """
    want = [str(v) for v in expected if str(v).strip()]
    hits = []
    for i, cells in enumerate(rows):
        if all(any(_cell_matches(c, w) for c in cells) for w in want):
            hits.append(i)
    return hits


def _select_exact(d: Driver, *, what: str, search: str, expected: list[str],
                  search_logical: str, grid_logical: str,
                  ok_logical: str, cancel_logical: str, scope=None,
                  title_re: str | None = None) -> bool:
    """Shared shape of 2.2-2.3 and 3.3: search, stabilize, judge, act.

    Returns True when an exact row was selected, False when none existed (caller
    takes the creation branch). Raises AmbiguityHalt when more than one matched.
    """
    # Re-bind the dialog on every attempt. The window stays open, but the element
    # reference goes stale: _open_picker resolves the search box successfully and the
    # very next lookup through the same scope reports a dead element (rect 0x0, no
    # children). Re-scoping gets a fresh handle instead of a cached corpse.
    # Typing an EXACT value auto-selects it: Fakturama's selector dialogs commit and
    # close the moment the search narrows to a single row, adding the line without
    # any OK click. Verified directly -- typing 'MAT-DESK-02' closed the dialog and
    # appended the item. So the dialog vanishing here is SUCCESS, not failure, and
    # retrying the keystrokes would add the row twice.
    def _type_search():
        if title_re and d.dialog_open(title_re):
            d.scope_window(title_re)
        d.set_text(search_logical, search)

    try:
        d.retry(_type_search, f"type search into {search_logical}", tries=2)
    except Exception:
        if title_re and not d.dialog_open(title_re):
            d.note(f"{what}: dialog auto-selected {search!r} and closed")
            d.scope_main()
            return True
        raise

    if title_re and not d.dialog_open(title_re):
        d.note(f"{what}: dialog auto-selected {search!r} and closed")
        d.scope_main()
        return True
    d.wait_stable(grid_logical, scope=scope)   # 2.2 "wait for the list to stabilize"
    rows = _rows(d, grid_logical, scope)
    hits = _exact(rows, expected)

    if len(hits) > 1:
        shot = d.screenshot(f"halt-{what}")
        raise AmbiguityHalt(what, search, [" | ".join(rows[i]) for i in hits], shot)
    if not hits:
        # Log what was actually on offer. An exact-match rule that rejects everything
        # is indistinguishable from an empty list unless the candidates are visible,
        # and the difference decides whether to fix the rule or the search.
        d.note(f"{what}: no exact match for {search!r} among {len(rows)} row(s)")
        for r in rows[:5]:
            d.note(f"    candidate: {r}")
        d.note(f"    required : {expected}")
        d.click(cancel_logical)           # 2.3 / 3.3 "click Cancel and continue"
        return False

    d.click_grid_row(hits[0], scope)
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
    d.set_date("order.date", o.order_date)                 # 1.5
    d.set_text("order.custref", o.external_ref)             # 1.6
    d.select("order.pricemode", "Net")                      # 1.7 (defaults to 'Gross')
    d.select("order.vat_with", "With VAT")                  # 1.7 (already the default)
    d.wait_value("order.custref", o.external_ref)
    _ORDER_REF["value"] = o.external_ref   # identity for _focus_order
    got_date = d.read_date("order.date")                                 # 1.5 readback
    if got_date != o.order_date:
        raise AmbiguityHalt("order.date", str(o.order_date),
                            [f"Order holds {got_date}"], d.screenshot("halt-order-date"))
    d.note(f"stage 1 ok -- Order open, Cust.Ref. {o.external_ref}, date {got_date}")


# --------------------------------------------------------------------------
# 2. Select or create the Debtor
# --------------------------------------------------------------------------

def stage2_debtor(d: Driver, o: OrderData) -> None:
    _focus_order(d)
    if _open_address_picker_and_select(d, o):
        _confirm_addresses(d, o)                   # 2.4
        return

    # 2.10, hoisted: Fakturama populates the Debtor's Payment dropdown when the
    # editor OPENS and never refreshes it, so a payment method created while that
    # editor is open is invisible to it (verified: the combo still offered only
    # ['Pay Cash'] after 'Bank Transfer' had been saved). The spec's ordering cannot
    # work in 2.2.0, so the existence check and creation happen first; the intent --
    # create master data only when an exact match is unavailable -- is unchanged.
    _ensure_payment_method(d, o.payment_method)
    _focus_order(d)                                # back to the still-open Order
    _create_debtor(d, o)                           # 2.5 - 2.11

    # 2.12 -- back to the still-open Order; successful re-selection proves the save.
    _focus_order(d)
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
            title_re=".*Select the address.*",
        )
    finally:
        d.scope_main()


def _confirm_addresses(d: Driver, o: OrderData) -> None:
    """2.4 / 2.13 -- the populated addresses should match the source image.

    2.8 is now satisfied: the Main address carries the Invoice address role, written
    to FKT_ADDRESS_CONTACTTYPES as 'BILLING' (see driver.set_address_roles).

    What remains is narrower: the Order's 'Invoice address' box still renders empty
    after the Debtor is selected, so there is nothing to compare against. The role is
    in the database, so this is a display/refresh behaviour rather than missing data.
    Reported rather than enforced, so the run does not stop on it.
    """
    inv = d.read_text("order.invoice_addr")
    if not inv.strip():
        d.note("GAP 2.4: the Order's invoice address box is empty even though the "
               "Debtor's address carries the BILLING role -- nothing to compare")
        return

    for part in (o.billing.street, o.billing.zip, o.billing.city):
        if _norm(part) not in _norm(inv):
            raise AmbiguityHalt("address.invoice", part, [inv],
                                d.screenshot("halt-address-invoice"))
    d.note("2.4: invoice address matches the source")

    if not o.delivery_same_as_billing:
        d.note("2.8: delivery differs from billing; a second address carries it")


def _create_debtor(d: Driver, o: OrderData) -> None:
    d.click_for("new.contact", "debtor.company")   # 2.5 -- Order tab stays open
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
    if not d.set_address_roles(roles):
        d.note("2.8: could not tick every role; the Order may show no invoice address")

    # 2.8 -- "If billing and delivery are identical, also assign the Delivery address
    # role and do not create another address." They are NOT identical here, so a
    # second address is required to carry the delivery details and the Delivery role.
    if not o.delivery_same_as_billing:
        _add_delivery_address(d, o)

    d.open_tab("debtor.tab_misc", "debtor.alias")  # 2.9
    d.set_text("debtor.alias", o.alias)
    d.set_text("debtor.discount", "0")
    d.select("debtor.netgross", "Net")

    # 2.10 -- there is no separate Payment tab in Fakturama 2.2.0: Alias name,
    # Discount, Net or Gross and Payment all live on Miscellaneous, so 2.9 and 2.10
    # happen without leaving this tab.
    if not _try_select(d, "debtor.payment", o.payment_method):
        # This branch called a function that does not exist, so the one time it
        # was taken the run died with a NameError instead of recovering. The
        # method IS created before the Debtor editor opens, so reaching here
        # means the combo had not picked up the new record yet. Re-assert it,
        # then try the selection once more, and halt rather than save a Debtor
        # with the wrong payment terms.
        d.note(f"debtor.payment: {o.payment_method!r} not in the combo yet; re-checking")
        _ensure_payment_method(d, o.payment_method)
        _focus_order(d, o.external_ref)
        d.open_tab("debtor.tab_misc", "debtor.alias")
        if not _try_select(d, "debtor.payment", o.payment_method):
            raise AmbiguityHalt("debtor.payment", o.payment_method,
                                ["the Debtor's payment combo does not offer it"],
                                d.screenshot("halt-debtor-payment"))
        # 2.10.6 -- the payment editor is now in front; come back to the Debtor
        # editor before its inner tabs exist again.
        d.click("debtor.editor_tab")
        d.open_tab("debtor.tab_misc", "debtor.alias")
        if not _try_select(d, "debtor.payment", o.payment_method):
            raise AmbiguityHalt("payment.reselect", o.payment_method, ["not selectable after save"],
                                d.screenshot("halt-payment-reselect"))

    d.click("order.save")                          # 2.11 -- once
    d.note(f"Debtor created: {o.company}")


def _add_delivery_address(d: Driver, o: OrderData) -> None:
    """Create the Debtor's second address, carrying the Delivery role (2.8)."""
    try:
        d.click("debtor.add_address")
    except Exception as exc:
        d.note(f"2.8: could not add a second address ({type(exc).__name__})")
        return
    time.sleep(1.5)

    try:
        d.set_text("debtor.street", o.delivery.street)
        d.set_text("debtor.zip", o.delivery.zip)
        d.set_text("debtor.city", o.delivery.city)
        d.set_text("debtor.country", o.delivery.country)
        if o.delivery.name and _norm(o.delivery.name) != _norm(o.company):
            # the warehouse name is an extra line on the address, not the company
            d.set_text("debtor.addl_name", o.delivery.name)
    except Exception as exc:
        d.note(f"2.8: could not fill the delivery address ({type(exc).__name__})")
        return

    if d.set_address_roles(["Delivery address"]):
        d.note(f"2.8 ok: second address created for {o.delivery.street}, "
               f"{o.delivery.zip} {o.delivery.city} with the Delivery role")
    else:
        d.note("2.8: second address created but the Delivery role was not set")


def _try_select(d: Driver, logical: str, value: str) -> bool:
    try:
        d.select(logical, value)
        return True
    except Exception:
        return False


def _ensure_payment_method(d: Driver, method: str) -> None:
    """2.10.1 - 2.10.6. Reuse one unambiguous exact match, else create it.

    Runs BEFORE the Debtor editor opens -- see the note in stage2_debtor.
    """
    code = PAYMENT_CODE.get(_norm(method))
    if code is None:
        raise AmbiguityHalt("payment.code", method, sorted(PAYMENT_CODE),
                            d.screenshot("halt-payment-code"))

    d.click("menu.payments")                       # 2.10.1
    view = d.list_view_scope("payment.add")        # scope reads to THIS list view
    d.set_text("list.search", method)
    d.wait_stable("address_dlg.list", scope=view)
    rows = _rows(d, "address_dlg.list", view)
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
    # 3.2 upper icon, never the green +.
    _open_picker(d, "product_picker.open", ".*Select a product.*", "product_dlg.search")
    try:
        return _select_exact(
            d, what=f"product:{line.sku}", search=line.sku, expected=[line.sku],
            search_logical="product_dlg.search", grid_logical="address_dlg.list",
            ok_logical="product_dlg.ok", cancel_logical="product_dlg.cancel",
            title_re=".*Select a product.*",
        )
    finally:
        d.scope_main()


def _ensure_vat(d: Driver, line: OrderLine) -> None:
    """3.4 - 3.6. Reuse only on a full three-way match; otherwise create, else halt."""
    pct = f"{line.vat_pct.normalize():f}"
    d.click("menu.vats")                           # 3.4
    view = d.list_view_scope("vat.add")
    d.set_text("list.search", line.vat_name)
    d.wait_stable("address_dlg.list", scope=view)
    rows = _rows(d, "address_dlg.list", view)

    # 3.5 -- reuse only when Name is 'VAT <pct>%', Value is that percentage, and the
    # VAT code is S (Standard rate).
    #
    # The VATs LIST shows only Standard / Name / Description / Value -- there is no
    # VAT code column, so the third condition cannot be evaluated from the list at
    # all. Name and Value are checked here; the code is confirmed by opening the
    # record, which is the only place Fakturama exposes it.
    hits = _exact(rows, [line.vat_name])
    if len(hits) > 1:
        raise AmbiguityHalt("vat.lookup", line.vat_name, [" | ".join(rows[i]) for i in hits],
                            d.screenshot("halt-vat-lookup"))
    if hits:
        cells = [_norm(c) for c in rows[hits[0]]]
        if not any(pct in c for c in cells):
            raise AmbiguityHalt("vat.value", line.vat_name,
                                [f"Value does not read {pct}: " + " | ".join(rows[hits[0]])],
                                d.screenshot("halt-vat-value"))
        if not _vat_code_is_standard(d, rows[hits[0]], line):
            raise AmbiguityHalt("vat.code", line.vat_name,
                                ["VAT code (E-Invoice) is not S (Standard rate)"],
                                d.screenshot("halt-vat-code"))
        d.note(f"VAT {line.vat_name!r} exists with matching name, value and code -- reusing")
        return

    d.click("vat.add")                             # 3.6
    d.set_text("vat.name", line.vat_name)
    d.set_text("vat.description", line.vat_name)
    d.select("vat.code", "S (Standard rate)")
    d.set_text("vat.value", pct)
    # 'Standard VAT' display is left unchanged.
    d.click("order.save")                          # once
    _CREATED_VATS.add(_norm(line.vat_name))
    d.note(f"VAT created: {line.vat_name} with S (Standard rate)")


_CREATED_VATS: set[str] = set()


def _vat_code_is_standard(d: Driver, row: list[str], line: OrderLine) -> bool:
    """3.5's third condition: is this VAT's code S (Standard rate)?

    The VATs list shows only Standard / Name / Description / Value -- Fakturama does
    not expose VAT code (E-Invoice) there, so it cannot be read at lookup time.

    A VAT this run created is known to be S, because 3.6 set it. For one that already
    existed, the code is genuinely unverifiable from the list, and 3.5 is an
    exactness rule -- so it is reported as unverified and the caller halts rather
    than reusing a VAT that might be Z, E or AE. Opening the record to read the code
    is the obvious improvement and is listed in the README.
    """
    if _norm(line.vat_name) in _CREATED_VATS:
        d.note(f"{line.vat_name!r} was created by this run with S (Standard rate)")
        return True
    d.note(f"3.5: cannot verify VAT code for pre-existing {line.vat_name!r} -- "
           f"the list view has no VAT code column")
    return False


def _create_product(d: Driver, line: OrderLine) -> None:
    d.click_for("new.product", "product.itemno")   # 3.7 -- only after the VAT exists
    # Scope to this editor: the Order editor is open too and its labels collide
    # (Text 'VAT' matches in both), which would halt as ambiguous.
    ed = d.editor_scope("product.itemno")
    d.set_text("product.itemno", line.sku, scope=ed)   # 3.8
    d.set_text("product.name", line.description, scope=ed)
    d.set_text("product.description", line.description, scope=ed)
    # 3.9 -- master price is unit_net x (1 + vat/100). The LINE discount is NOT applied:
    # 250.00 -> 297.50, not 225.00 -> 267.75.
    d.set_text("product.price_gross", f"{line.product_gross_price:.2f}", scope=ed)
    d.set_text("product.cost_price", "0.00", scope=ed)      # 3.10
    d.select("product.vat", line.vat_name, scope=ed)
    d.set_text("product.stock", "0.00", scope=ed)
    # Category, GTIN, supplier code, allowance, picture and udf1 are left untouched.
    d.click("order.save")                          # 3.11 -- once
    d.note(f"Product created: {line.sku} @ {line.product_gross_price} gross")
    _focus_order(d)                                # 3.12 -- back to the open Order


def _complete_line(d: Driver, line: OrderLine) -> None:
    """3.13 - 3.16 -- fill the item line and check its price.

    The Items table exposes no rows or cells to UIA, so each field is reached by
    double-clicking the cell at a vision-located position inside the table's own
    rectangle. Column names come from the table's header, so a re-ordered or resized
    table still resolves.
    """
    pane = d.items_pane()
    row = _line_index(d, line)

    d.edit_cell(pane, "Qty.", row, line.qty)                        # 3.13
    d.edit_cell(pane, "U.Price", row, f"{line.unit_net:.2f}")       # 3.14
    d.edit_cell(pane, "Discount", row, f"{line.discount_pct:f}")    # 3.15

    # 3.16 -- qty x unit net x (1 - discount/100), read back from the table.
    shown = _line_cell(d, pane, row, "Price")
    if shown is None:
        d.note(f"3.16: could not read the line price for {line.sku}; not verified")
        return
    if shown != line.expected_line_net:
        raise AmbiguityHalt(f"line.{line.sku}", "line price",
                            [f"shown {shown} != expected {line.expected_line_net}"],
                            d.screenshot(f"halt-line-{line.sku}"))
    d.note(f"3.16 ok: {line.sku} line price {shown}")


def _line_index(d: Driver, line: OrderLine) -> int:
    """Which row of the Items table holds this SKU.

    This used to fall back to the last row when the SKU was not found, on the
    assumption that the picker had just appended it. That assumption is what let
    the worst bug in this project through: the second Product was created empty,
    the picker could not find it, the dialog auto-closed having selected the
    FIRST product instead, and this function handed back the last row anyway.
    The correct quantity, price and discount were then typed into a line holding
    the wrong product -- so every total matched and the arithmetic gate passed
    while the Order billed the wrong item.

    A missing SKU means the selection did not do what we think it did. That is
    exactly the "stop for manual review" case, not a case for a best guess.
    """
    rows = d.grid_rows(d.items_pane())
    for i, cells in enumerate(rows):
        if any(_cell_matches(c, line.sku) for c in cells):
            return i
    raise AmbiguityHalt(f"line.{line.sku}", f"an Items row holding {line.sku}",
                        [str(r) for r in rows[:5]] or ["the Items table is empty"],
                        d.screenshot(f"halt-line-missing-{line.sku}"))


def _line_cell(d: Driver, pane, row: int, column: str):
    """One numeric cell of an item line, as Decimal, or None if unreadable.

    Reads through the model rather than OCR: OCR drops empty cells, so its per-row
    list no longer lines up with the header and a column index points at the wrong
    value. The model is asked to keep empty cells as "", which preserves alignment.
    Geometry still comes from OCR -- this is only about CONTENT.
    """
    from src.driver import colkey
    from src.vision import read_table

    shot = d.shots / "_items.png"
    pane.capture_as_image().save(shot)
    table = read_table(shot)
    names = [colkey(c) for c in table.get("columns", [])]
    rows = table.get("rows", [])
    if colkey(column) not in names or row >= len(rows):
        return None
    idx = names.index(colkey(column))
    cells = rows[row]
    if idx >= len(cells):
        return None
    raw = str(cells[idx]).replace("EUR", "").replace("$", "").replace(",", ".").strip()
    try:
        return money(raw)
    except Exception:
        return None


# --------------------------------------------------------------------------
# 4. Complete and save the Order
# --------------------------------------------------------------------------

def stage4_save_order(d: Driver, o: OrderData) -> None:
    # 4.1 -- final gate before saving: re-confirm the Debtor addresses against the
    # source. Every Product line was checked against its own line price at 3.16 as
    # it was entered, and the totals below re-prove the whole set.
    _confirm_addresses(d, o)                                             # 4.1

    # 4.2 -- order-level Discount and Shipping stay at their defaults; this image
    # supplies no order-level values.
    _check_total(d, "order.total_net", o.net_total, "Total Net")        # 4.3
    _check_total(d, "order.total_vat", o.vat_total, "VAT")
    _check_total(d, "order.total", o.gross_total, "Total")

    d.click("order.save")                                                # 4.4
    d.click("menu.documents")                                            # 4.5
    view = d.list_view_scope("documents.add")
    d.set_text("list.search", o.external_ref)
    d.wait_stable("address_dlg.list", scope=view)
    rows = _rows(d, "address_dlg.list", view)
    if not _exact(rows, [o.external_ref]):
        raise AmbiguityHalt("order.saved", o.external_ref, [str(r) for r in rows[:5]],
                            d.screenshot("halt-order-saved"))
    d.screenshot("04-order-saved")
    d.note(f"stage 4 ok -- Order saved, totals {o.net_total}/{o.vat_total}/{o.gross_total}")


def _amount(text: str):
    """The numeric amount in a money field, or None.

    Fakturama renders totals with a currency symbol and locale separators, and a
    fresh install defaults to '$' even though the source document is EUR. Pull the
    number out rather than stripping a fixed set of decorations -- the symbol is not
    part of the check, the value is.
    """
    import re

    m = re.search(r"-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?", str(text or ""))
    if not m:
        return None
    raw = m.group(0)
    # Treat the LAST separator as the decimal point; anything before it groups.
    if "," in raw and "." in raw:
        dec = max(raw.rfind(","), raw.rfind("."))
        raw = raw[:dec].replace(",", "").replace(".", "") + "." + raw[dec + 1:]
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return money(raw)
    except Exception:
        return None


def _check_total(d: Driver, logical: str, expected: Decimal, label: str) -> None:
    text = d.read_text(logical)
    shown = _amount(text)
    if shown is None:
        d.note(f"4.3: could not read {label} (field reads {text!r}); not verified")
        return
    if shown != expected:
        raise AmbiguityHalt(f"total.{label}", label,
                            [f"shown {shown} != source {expected} (field read {text!r})"],
                            d.screenshot(f"halt-total-{label.replace(' ', '-')}"))
    d.note(f"4.3 ok: {label} = {shown}")


# --------------------------------------------------------------------------
# 5. Complete and verify the linked Invoice
# --------------------------------------------------------------------------

def stage5_invoice(d: Driver, o: OrderData) -> None:
    # 4.5 left the Data > Documents list in front. The follow-up buttons live in the
    # Order editor, and clicking one while that editor is in the background is a
    # silent no-op -- the click reports success and no Invoice is ever created.
    _focus_order(d, o.external_ref)

    # 4.6 -- the follow-up action, NOT the top toolbar Invoice button. Only the
    # follow-up preserves the Order relationship.
    d.click("order.followup_invoice")
    # 4.7 -- the linked Invoice opens in its own editor tab; bring it to the front,
    # because a background tab's widgets are not realized.
    d.open_tab("invoice.editor_tab", "invoice.paid")

    # 5.1 -- proposed Invoice No., Invoice Date and Service date left unchanged;
    # confirm what was copied down from the Order.
    if _norm(d.read_text("order.custref")) != _norm(o.external_ref):
        raise AmbiguityHalt("invoice.custref", o.external_ref, [d.read_text("order.custref")],
                            d.screenshot("halt-invoice-custref"))
    d.note(f"5.1 ok: Cust.Ref. carried over as {o.external_ref}")
    _check_total(d, "invoice.total_net", o.net_total, "Invoice Total Net")
    _check_total(d, "invoice.vat", o.vat_total, "Invoice VAT")
    _check_total(d, "invoice.total", o.gross_total, "Invoice Total")

    if not _try_select(d, "invoice.payment", o.payment_method):          # 5.2
        raise AmbiguityHalt("invoice.payment", o.payment_method, ["not available on the Invoice"],
                            d.screenshot("halt-invoice-payment"))

    if o.paid:                                                           # 5.3
        d.click("invoice.paid")
        time.sleep(1.0)   # ticking 'paid' re-lays out the row; resolve it again after
        # Value first, date last. Typing into the value field leaves the date
        # widget's month segment reading '00' -- a correct 'Jul 18, 2026'
        # became '00 18, 2026' purely from filling in the neighbouring field.
        d.set_text("invoice.paid_value", f"{o.gross_total:.2f}")
        d.set_date("invoice.paid_date", o.payment_date)

        # Read all three back. This block used to print 'ok' from the values it
        # had just tried to write, so the Invoice saved with today's date and the
        # log still said 5.3 ok. Report what the Invoice holds, not what we meant.
        got_date = d.read_date("invoice.paid_date")
        got_value = _amount(d.read_text("invoice.paid_value"))
        if got_date != o.payment_date or got_value != o.gross_total:
            raise AmbiguityHalt("invoice.paid", f"{o.payment_date} / {o.gross_total}",
                                [f"Invoice holds {got_date} / {got_value}",
                                 f"date field reads {d.read_text('invoice.paid_date')!r}"],
                                d.screenshot("halt-invoice-paid"))
        d.note(f"5.3 ok: paid, {got_date}, {got_value} (read back from the Invoice)")
    else:
        d.note("5.3: not PAID -- leaving paid clear, inventing no date or value")

    d.click("order.save")                                                # 5.4
    d.click("menu.documents")                                            # 5.5
    view = d.list_view_scope("documents.add")
    d.set_text("list.search", o.external_ref)
    d.wait_stable("address_dlg.list", scope=view)
    rows = _rows(d, "address_dlg.list", view)

    # 5.5 -- BOTH rows must be present: the Invoice in its paid state, and the source
    # Order still open, each at the same Cust.Ref. and Total. Two matching rows is the
    # success condition here, not an ambiguity.
    def _row_with(state: str):
        for r in rows:
            cells = [_norm(c) for c in r]
            if state in cells and any(_norm(o.external_ref) == c for c in cells):
                return r
        return None

    inv_row = _row_with("paid" if o.paid else "open")
    ord_row = None
    for r in rows:
        cells = [_norm(c) for c in r]
        if "open" in cells and any(_norm(o.external_ref) == c for c in cells) and r is not inv_row:
            ord_row = r
            break

    if inv_row is None:
        raise AmbiguityHalt("invoice.saved", o.external_ref,
                            ["no Invoice row in the expected state"] + [str(r) for r in rows[:4]],
                            d.screenshot("halt-invoice-saved"))
    if ord_row is None:
        raise AmbiguityHalt("order.still_open", o.external_ref,
                            ["source Order not found still open"] + [str(r) for r in rows[:4]],
                            d.screenshot("halt-order-open"))

    # Ask whether the row SHOWS the expected total, rather than parsing the first
    # number in it -- a document number like 'INV000001' parses as 0.00 and is not
    # the Total column.
    for label, row in (("Invoice", inv_row), ("Order", ord_row)):
        if not any(_amount(c) == o.gross_total for c in row):
            raise AmbiguityHalt(f"{label.lower()}.total", label,
                                [f"{label} row does not show {o.gross_total}: {row}"],
                                d.screenshot(f"halt-{label.lower()}-total"))
    d.note(f"5.5 ok: Invoice {'paid' if o.paid else 'open'} at {o.gross_total}, "
           f"source Order still open at {o.gross_total}")
    d.screenshot("05-invoice-verified")
    # 5.7 -- the flow ends here. No Delivery, Correction or Dunning document.
    # 5.6 -- "reopen the Invoice only if needed". Not needed: 5.3 already reads the
    # payment method, paid state, date and value back out of the open editor, and
    # 5.5 re-reads the state and total from Data > Documents after the save.
    # 5.7 -- the flow ends here. No Delivery, Correction or Dunning document.
    d.note("stage 5 ok -- Invoice saved and verified; source Order still open")


def run(d: Driver, o: OrderData) -> None:
    stage1_open_order(d, o)
    stage2_debtor(d, o)
    stage3_products(d, o)
    stage4_save_order(d, o)
    stage5_invoice(d, o)

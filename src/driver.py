"""UIA driver -- the only module that knows what a window is.

Grounding strategy, four tiers, first unambiguous hit wins (see DESIGN.md §3):

  T1  scoped tree search      control_type + name/auto_id, within one window
  T2  anchor-relative walk    find a named anchor, then nth matching descendant
  T3  property disambiguation tooltip / help_text / enabled state
  T4  vision tiebreak         screenshot the ANCHOR'S OWN UIA rect, ask an LLM,
                              click the element it names -- by UIA handle

No coordinate is ever authored here or persisted between runs. Every position comes
from the live UIA tree at call time, so resize, DPI and theme changes are survivable.

Imports cleanly on Linux (pywinauto is Windows-only) so the selector table and the
resolution logic stay lint-able and testable off-platform.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:  # pragma: no cover - platform split
    from pywinauto import Application
    from pywinauto.findbestmatch import MatchError
    HAVE_UIA = True
except ImportError:  # Linux / CI
    Application = None
    MatchError = Exception
    HAVE_UIA = False


# pywinauto's type_keys() reads these as modifiers/grouping. Wrapping each in braces
# makes it type the literal character -- this is what keeps '+49 30 5550 1420' intact.
_TYPE_KEYS_SPECIAL = "^+%~(){}[]"


def colkey(name: str) -> str:
    """Normalize a column header for matching.

    OCR is exact about geometry but not about punctuation: the same header comes back
    as 'Qty.' on one read and 'Qty' on the next, 'Item No.' as 'Item No', 'Pos.' as
    'Po5.'. Matching on letters and digits only makes the lookup stable without
    becoming a fuzzy match -- 'U.Price' -> 'uprice' still cannot collide with
    'Price' -> 'price'.
    """
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _digits(text: str) -> str:
    """Just the digits, so '0' and '0%' and '$678.30' compare on their numbers."""
    return "".join(ch for ch in str(text) if ch.isdigit())


def _same_value(got: str, wanted) -> bool:
    return str(got).strip() == str(wanted).strip()


def _landed(got: str, wanted, el) -> bool:
    """Did the value arrive, allowing for how Fakturama renders it?

    Two shapes mean it did NOT arrive, and both were seen in real runs:
    an empty field, and a field reading its own label -- read_text() falls back
    to window_text(), which on an empty SWT Edit returns the control's Name.
    """
    got = str(got).strip()
    if not got:
        return False
    try:
        if got == (el.element_info.name or "").strip():
            return False
    except Exception:
        pass
    want_digits = _digits(wanted)
    return _digits(got) == want_digits if want_digits else True


def escape_keys(text: str) -> str:
    return "".join("{" + c + "}" if c in _TYPE_KEYS_SPECIAL else c for c in str(text))


def raw_click(x: int, y: int) -> None:
    """Click at absolute screen coordinates without activating any window.

    pywinauto's click_input() activates the target window first, and activating the
    shell DISMISSES Fakturama's address-role popup before the click can land -- the
    popup is an override-redirect panel that closes on focus change. Moving the
    physical cursor and sending raw button events leaves focus alone, so the popup
    survives long enough to receive the click.
    """
    import ctypes

    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def raw_key(vk: int, shift: bool = False) -> None:
    """Send a key at OS level without touching window focus.

    Same reason as raw_click: anything that activates a window dismisses Fakturama's
    address-role popup. The popup gives keyboard focus to its first checkbox (it
    draws the dotted focus border), so Space toggles it and Tab moves between them.
    """
    import ctypes

    KEYEVENTF_KEYUP = 0x0002
    VK_SHIFT = 0x10
    u = ctypes.windll.user32
    if shift:
        u.keybd_event(VK_SHIFT, 0, 0, 0)
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    if shift:
        u.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)


VK_SPACE, VK_TAB, VK_RETURN, VK_ESCAPE = 0x20, 0x09, 0x0D, 0x1B


class AmbiguityHalt(Exception):
    """Spec steps 2.3, 2.10.2, 3.3, 3.5, 3.12 and 5.2 all collapse into this.

    More than one exact candidate, or a candidate whose properties conflict with the
    source. Guessing between two Debtors is the one failure this must never produce.
    """

    def __init__(self, what: str, query: str, candidates: list[str], shot: Path | None = None):
        self.what, self.query, self.candidates, self.shot = what, query, candidates, shot
        super().__init__(
            f"{what}: ambiguous for {query!r} -- {len(candidates)} candidates: "
            f"{candidates[:5]}" + (f" (screenshot: {shot})" if shot else "")
        )


@dataclass(frozen=True)
class Selector:
    """One logical control. Data, not code -- a version or locale bump edits this table.

    tier is implicit: anchor set -> T2, else T1. `discriminator` adds T3.
    """

    control_type: str
    name: str | None = None          # T1: exact or regex name
    auto_id: str | None = None       # T1: AutomationId, when SWT bothers to set one
    anchor: str | None = None        # T2: logical name of a stable named neighbour
    index: int = 0                   # T2: nth matching descendant under the anchor
    discriminator: str | None = None # T3: substring expected in tooltip/help_text
    same_row: bool = False           # T2b: pick from controls on the anchor label's row
    within: bool = False             # T2c: search INSIDE the anchor, not its parent
    pick: str = ""                   # "first"/"last" by screen order, when duplicates are expected
    describe: str = ""               # T4: what the LLM should look for


# The selector registry. Starts as a dict; becomes YAML the moment a second locale
# is real. Names below are the English strings Fakturama renders.
SELECTORS: dict[str, Selector] = {
    # --- stage 1: the Order editor -----------------------------------------
    "toolbar.order":        Selector("Button", name="Create: New Order",
                                     describe="the 'Create: New Order' button in the top toolbar"),
    # VERIFIED: 'No.' and 'Date' are Text labels; their Edits are unnamed. The Date
    # Edit is the 2nd Edit under the row Pane that the 'Date' label sits in (the 1st
    # is the No. field), so both resolve by anchor + index, never by coordinate.
    "order.no_label":       Selector("Text", name="No."),
    "order.no":             Selector("Edit", anchor="order.no_label", index=0, same_row=True),
    "order.date_label":     Selector("Text", name="Date"),
    "order.date":           Selector("Edit", anchor="order.date_label", index=0, same_row=True),
    "order.custref":        Selector("Edit", name="Cust.Ref."),
    # VERIFIED 1.7: the document price mode is an UNNAMED ComboBox on the Date row
    # (it reads 'Gross' by default), not a radio button. The VAT-mode ComboBox beside
    # it is named 'VAT' and already reads 'With VAT'.
    "order.pricemode":      Selector("ComboBox", anchor="order.date_label", index=0, same_row=True),
    "order.vat_with":       Selector("ComboBox", name="VAT"),
    "order.shipping":       Selector("ComboBox", name="Shipping"),
    "order.save":           Selector("Button", name="Save the current contents",
                                     describe="the toolbar Save control"),

    # --- stage 2: Debtor ----------------------------------------------------
    # Two icons sit beside 'Addresses'. The upper opens the existing-contact picker;
    # the lower green + starts a NEW Debtor and must never be clicked here (step 2.1).
    # They share a control type and carry no AutomationId, so they are resolved by
    # position under the 'Addresses' anchor, then confirmed by tooltip.
    "order.addresses":      Selector("Text", name="Addresses"),
    # VERIFIED against Fakturama 2.2.0: these are unnamed *Image* controls sharing a
    # Pane with the 'Addresses' Text. No Name, no usable AutomationId (numeric handles
    # only) -- tree order under the anchor is the ONLY reliable handle, and it matches
    # vertical order: index 0 sits at y=299, index 1 at y=327.
    "address_picker.open":  Selector("Image", anchor="order.addresses", index=0,
                                     describe="the UPPER icon beside 'Addresses' that opens the "
                                              "existing-contact picker -- NOT the lower green + icon"),
    "address_picker.new":   Selector("Image", anchor="order.addresses", index=1,
                                     describe="the LOWER green + icon beside 'Addresses'"),
    # VERIFIED: the dialog's search box is an UNNAMED Edit beside a 'Search:' Text.
    "address_dlg.searchlbl": Selector("Text", name="Search:"),
    "address_dlg.search":   Selector("Edit", anchor="address_dlg.searchlbl", index=0, same_row=True),
    "address_dlg.list":     Selector("Table"),
    "address_dlg.ok":       Selector("Button", name="OK"),
    "address_dlg.cancel":   Selector("Button", name="Cancel"),
    # VERIFIED: the Order shows addresses as unnamed Edits INSIDE tabs named
    # 'Invoice address' / 'Delivery address'.
    "order.invoice_tab":    Selector("Tab", name="Invoice address"),
    "order.invoice_addr":   Selector("Edit", anchor="order.invoice_tab", within=True, index=0),
    "order.delivery_tab":   Selector("Tab", name="Delivery address"),
    "order.delivery_addr":  Selector("Edit", anchor="order.delivery_tab", within=True, index=0),

    "new.contact":          Selector("SplitButton", name="Create a new contact",
                                     describe="'Create a new contact' in the toolbar / left New panel"),
    "debtor.company":       Selector("Edit", name="Company"),
    # VERIFIED: Fakturama labels two fields with one Text. 'First Name Last Name' owns
    # both unnamed Edits; same for 'ZIP - City'. Index 0 is left, 1 is right.
    "debtor.name_label":    Selector("Text", name="First Name Last Name"),
    "debtor.firstname":     Selector("Edit", anchor="debtor.name_label", index=0, same_row=True),
    "debtor.lastname":      Selector("Edit", anchor="debtor.name_label", index=1, same_row=True),
    "debtor.salutation_lbl": Selector("Text", name="Salutation"),
    "debtor.salutation":    Selector("ComboBox", anchor="debtor.salutation_lbl", index=0, same_row=True),
    "debtor.tab_addresses": Selector("TabItem", name="Addresses"),
    "debtor.tab_mainaddr":  Selector("TabItem", name="Main address"),
    # The '+' beside the 'Main address' tab adds a SECOND address to the Debtor,
    # which 2.8 needs when billing and delivery differ.
    "debtor.add_address":   Selector("Button", name="+", anchor="debtor.tab_mainaddr",
                                     index=0, same_row=True),
    "debtor.addrtype_lbl":  Selector("Text", name="address type"),
    "debtor.addrtype":      Selector("Edit", anchor="debtor.addrtype_lbl", index=0, same_row=True),
    "debtor.addl_name":     Selector("Edit", name="additional name"),
    "debtor.street":        Selector("Edit", name="Street"),
    "debtor.zipcity_label": Selector("Text", name="ZIP - City"),
    "debtor.zip":           Selector("Edit", anchor="debtor.zipcity_label", index=0, same_row=True),
    "debtor.city":          Selector("Edit", anchor="debtor.zipcity_label", index=1, same_row=True),
    "debtor.country":       Selector("ComboBox", name="Country"),
    "debtor.email":         Selector("Edit", name="E-Mail"),
    "debtor.phone":         Selector("Edit", name="Telephone"),
    "debtor.role_invoice":  Selector("CheckBox", name="Invoice address"),
    "debtor.role_delivery": Selector("CheckBox", name="Delivery address"),
    # The editor tabs across the top of the shell. Fakturama marks unsaved editors
    # with a leading '*', so a freshly opened Debtor is '*New Debtor'.
    "debtor.editor_tab":    Selector("TabItem", name="*New Debtor", pick="first"),
    "order.editor_tab":     Selector("TabItem", name="*New Order", pick="first"),
    "invoice.editor_tab":   Selector("TabItem", name="*New Invoice", pick="first"),
    "debtor.tab_misc":      Selector("TabItem", name="Miscellaneous"),
    "debtor.alias":         Selector("Edit", name="Alias name"),
    "debtor.discount":      Selector("Edit", name="Discount"),
    "debtor.netgross":      Selector("ComboBox", name="Net or Gross"),
    # VERIFIED: no 'Payment' TabItem exists; the Payment ComboBox is on Miscellaneous.
    "debtor.payment":       Selector("ComboBox", name="Payment"),

    # --- stage 2.10: payment terms -----------------------------------------
    # The Data menu works but is stateful -- its items only exist while the menu is
    # open, which makes the traversal flaky. Fakturama's left Navigation View lists
    # the same views as plain entries, so open them directly.
    "menu.data":            Selector("MenuItem", name="Data"),
    "menu.payments":        Selector("Text", name="terms of payment"),
    # VERIFIED: the spec's "green + control at the upper-right of the list" is a
    # properly NAMED Button, one per list view -- no anchor walk needed.
    "payment.add":          Selector("Button", name="Create a new term of payment"),
    # VERIFIED: Fakturama labels the VAT list's add button 'Create a new tax rate',
    # not 'VAT' -- the view is called VATs but the control is not.
    "vat.add":              Selector("Button", name="Create a new tax rate"),
    "list.search":          Selector("Edit", anchor="address_dlg.searchlbl", index=0, same_row=True),
    "payment.name":         Selector("Edit", name="Name"),
    "payment.description":  Selector("Edit", name="Description"),
    # VERIFIED: Fakturama 2.2.0 ships this control with an UNTRANSLATED i18n key as
    # its name -- the English bundle is missing the string, so UIA reports the literal
    # '!editorPaymentPaymentcode!'. Exactly the kind of breakage a selector registry
    # absorbs as a data edit instead of a code change.
    "payment.code":         Selector("ComboBox", name="!editorPaymentPaymentcode!"),
    "payment.cash_discount": Selector("Edit", name="Cash discount"),
    "payment.discount_days": Selector("Edit", name="Discount Days"),
    "payment.net_days":     Selector("Edit", name="Net Days"),

    # --- stage 3: VAT and Product ------------------------------------------
    "menu.vats":            Selector("Text", name="VATs"),
    "vat.name":             Selector("Edit", name="Name"),
    "vat.description":      Selector("Edit", name="Description"),
    # VERIFIED: the full label, exactly as the spec writes it in 3.5/3.6.
    "vat.code":             Selector("ComboBox", name="VAT code (E-Invoice)"),
    "vat.value":            Selector("Edit", name="Value"),

    "order.items":          Selector("Text", name="Items"),
    "product_picker.open":  Selector("Image", anchor="order.items", index=0,
                                     describe="the UPPER Product-selection icon beside the Items "
                                              "table -- NOT the green + control"),
    "product_dlg.search":   Selector("Edit", anchor="address_dlg.searchlbl", index=0, same_row=True),
    "product_dlg.ok":       Selector("Button", name="OK"),
    "product_dlg.cancel":   Selector("Button", name="Cancel"),
    # 'Create a new product' exists TWICE (main toolbar and the Products list view),
    # which is an ambiguity halt waiting to happen. The left New panel's entry is
    # unique and is what the spec calls 'New product'.
    "new.product":          Selector("Text", name="New product"),
    "product.itemno":       Selector("Edit", name="Item Number"),
    "product.name":         Selector("Edit", name="Name"),
    "product.description":  Selector("Edit", name="Description"),
    # VERIFIED: 'Price (gross)', 'cost price (net)' and 'Stock' are Text labels with
    # UNNAMED Edits beside them -- resolved on the label's row, left to right.
    "product.price_label":  Selector("Text", name="Price (gross)"),
    "product.price_gross":  Selector("Edit", anchor="product.price_label", index=0, same_row=True),
    "product.cost_label":   Selector("Text", name="cost price (net)"),
    "product.cost_price":   Selector("Edit", anchor="product.cost_label", index=0, same_row=True),
    # 'VAT' as a Text is ambiguous even inside the Product editor -- the ComboBox
    # carries its own display Text of the same name. Anchor on the unique
    # 'cost price (net)' label instead: the editor's ComboBoxes are Category then
    # VAT in tree order, so the VAT one is index 1.
    "product.vat":          Selector("ComboBox", anchor="product.cost_label", index=1),
    "product.stock_label":  Selector("Text", name="Stock"),
    "product.stock":        Selector("Edit", anchor="product.stock_label", index=0, same_row=True),

    # --- item line fields (resolved within the Items row, see flow._complete_line) --
    "order.qty":            Selector("Edit", name="Qty."),
    "order.uprice":         Selector("Edit", name="U.Price"),
    "order.vat":            Selector("ComboBox", name="VAT"),
    "order.discount":       Selector("Edit", name="Discount"),
    "order.line_price":     Selector("Edit", name="Price"),

    # --- order totals (4.3) -------------------------------------------------
    # The label follows the document price mode: 'Total Net' in Net mode (1.7),
    # 'Total Gross' in Gross mode. Both are resolved; the flow reads whichever exists.
    "order.total_net":      Selector("Edit", name="Total Net"),
    "order.total_gross":    Selector("Edit", name="Total Gross"),
    "order.total_vat":      Selector("Edit", name="VAT"),
    "order.total":          Selector("Edit", name="Total"),

    # --- stages 4 and 5 -----------------------------------------------------
    "menu.documents":       Selector("Text", name="Documents"),
    # The Documents list view's own toolbar button, used to scope grid reads to that
    # view rather than to whatever else is open.
    "documents.add":        Selector("Button", name="Create: Order"),
    # 4.6 -- MUST be the Invoice button INSIDE the 'Create a follow-up document'
    # group; only the follow-up action preserves the Order relationship. Without
    # within=True this searches the group's PARENT and can match the top toolbar's
    # Invoice button, which the spec explicitly forbids -- and which silently opened
    # nothing here, leaving the Order in front with no Invoice tab.
    "order.followup_invoice": Selector("Button", name="Invoice",
                                      anchor="order.followup", within=True, index=0,
                                      describe="'Invoice' inside the saved Order's 'Create a "
                                               "follow-up document' area -- NOT the top toolbar Invoice"),
    "order.followup":       Selector("Group", name="Create a follow-up document"),
    # VERIFIED in the linked Invoice editor: 'paid' is the ONLY CheckBox in the whole
    # application, and the payment row sits alongside it -- the payment-method
    # ComboBox and the date/value Edits are all UNNAMED, so they are resolved on the
    # checkbox's row, left to right. Ticking 'paid' re-lays out that row, which is why
    # the fields are resolved again after ticking rather than cached.
    "invoice.paid":         Selector("CheckBox", name="paid"),
    "invoice.payment":      Selector("ComboBox", anchor="invoice.paid", index=0, same_row=True),
    "invoice.paid_date":    Selector("Edit", anchor="invoice.paid", index=0, same_row=True),
    "invoice.paid_value":   Selector("Edit", anchor="invoice.paid", index=1, same_row=True),
    # The Invoice's own totals (5.1): named, unlike the payment row.
    "invoice.total_net":    Selector("Edit", name="Total Net"),
    "invoice.vat":          Selector("Edit", name="VAT"),
    "invoice.total":        Selector("Edit", name="Total"),
}


@dataclass
class Driver:
    """Thin wrapper over pywinauto's UIA backend. Resolution, waiting, screenshots."""

    app_path: str
    shots: Path = Path("docs/screenshots")
    timeout: float = 20.0
    poll: float = 0.25
    vision_tiebreak: bool = True
    app: object | None = None
    _scope: object | None = None
    _main: object | None = None   # the Fakturama shell; dialogs are its children
    _grid_y: list = field(default_factory=list)   # row centres from the last grid read
    _log: list[str] = field(default_factory=list)
    _t0: float = field(default_factory=time.monotonic)   # for the elapsed column
    film: bool = False            # capture a frame after every click (--film)
    _frame: int = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        if not HAVE_UIA:
            raise RuntimeError("pywinauto is Windows-only; run the flow on Windows.")
        self.shots.mkdir(parents=True, exist_ok=True)
        self.app = Application(backend="uia").start(self.app_path)
        self.scope_window(".*Fakturama.*")
        self.maximize()
        return self

    def connect(self):
        """Attach to an already-running Fakturama instead of launching one."""
        if not HAVE_UIA:
            raise RuntimeError("pywinauto is Windows-only; run the flow on Windows.")
        self.shots.mkdir(parents=True, exist_ok=True)
        self.app = Application(backend="uia").connect(title_re=".*Fakturama.*", timeout=self.timeout)
        self.scope_window(".*Fakturama.*")
        self.maximize()
        return self

    # -- scoping (T1 depends on this) ---------------------------------------

    def scope_window(self, title_re: str):
        """Resolve the active window/dialog. Never search from the desktop root.

        Scoping is what makes 'Save' mean THIS Order's Save, and what keeps the
        still-open Order tab from colliding with the Debtor editor opened on top.

        Fakturama renders its dialogs ('Select the address', 'Select a product') as
        CHILD Windows inside the main shell, not as top-level windows -- verified
        against 2.2.0. So look inside the main window first and only fall back to
        top-level. Searching top-level alone silently finds nothing.
        """
        import re as _re
        pat = _re.compile(title_re)

        main = self._main or self._scope
        if main is not None:
            try:
                matches = [c for c in main.descendants(control_type="Window")
                           if pat.search(c.element_info.name or "")]
                # A cancelled dialog can linger in the tree with no children, and
                # binding to it makes every lookup inside "the dialog" fail even
                # though the real one is open. Prefer a visible window that actually
                # has contents, newest last.
                def usable(c):
                    try:
                        return bool(c.is_visible()) and bool(c.children())
                    except Exception:
                        return False
                live = [c for c in matches if usable(c)]
                chosen = (live or matches)[-1] if (live or matches) else None
                if chosen is not None:
                    self._scope = chosen
                    self.note(f"scope -> child window {chosen.element_info.name!r}"
                              f"{'' if live else ' (no live candidate; using last)'}")
                    return chosen
            except Exception:
                pass

        # Desktop enumeration rather than Application.window(): a modal child dialog
        # leaves the shell not-"ready", so .wait("visible ready") times out even though
        # the window is perfectly findable and usable.
        from pywinauto import Desktop
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            for w in Desktop(backend="uia").windows():
                try:
                    if pat.search(w.window_text() or ""):
                        self._scope = w
                        if self._main is None:
                            self._main = w
                        self.note(f"scope -> top-level {w.window_text()!r}")
                        return w
                except Exception:
                    continue
            time.sleep(self.poll)
        raise TimeoutError(f"no top-level window matching {title_re!r}")

    def maximize(self):
        """Maximize the shell before doing anything else.

        SWT does not create widgets that are not visible, so on a small window whole
        sections are absent from the UIA tree entirely -- 'Addresses' simply does not
        exist to be found. Maximizing realizes them. It also makes the screenshots
        legible. Position still never matters: every click resolves its element and
        uses that element's own rectangle at click time.
        """
        try:
            self._main.maximize()
            self.note("shell maximized")
        except Exception as exc:
            self.note(f"maximize skipped: {exc}")

    def open_tab(self, tab_logical: str, expect_logical: str, tries: int = 3):
        """Click a tab and confirm it actually switched.

        SWT TabItems reject the UIA SelectionItem pattern (COMError 'Member not
        found'), so the only way in is a click -- which silently does nothing if the
        editor is not focused yet. Verify by waiting for a control that only exists
        on the target tab, and retry.
        """
        for attempt in range(1, tries + 1):
            try:
                self.click(tab_logical)
            except Exception as exc:
                self.note(f"{tab_logical}: click failed ({exc})")
            try:
                self.wait_until(lambda: bool(self.find(expect_logical)),
                                f"{tab_logical} to show {expect_logical}", timeout=6)
                return True
            except Exception:
                self.note(f"{tab_logical}: not switched (attempt {attempt}/{tries})")
        raise LookupError(f"{tab_logical}: could not switch (never saw {expect_logical})")

    def retry(self, fn, what: str, tries: int = 5, delay: float = 1.0):
        """Run an action that may race a rebuilding dialog.

        Waiting for a control to exist is not sufficient: SWT dialogs replace their
        contents while loading, so a control can resolve on two consecutive polls and
        be gone 0.25s later, when the action runs. Observed repeatedly on the product
        picker's search box. Retrying the ACTION -- not just the lookup -- is what
        actually survives the churn.
        """
        last = None
        for attempt in range(1, tries + 1):
            try:
                return fn()
            except Exception as exc:
                last = exc
                self.note(f"{what}: attempt {attempt}/{tries} failed ({type(exc).__name__})")
                if attempt == tries:
                    self.describe_scope(what)
                time.sleep(delay)
        raise last

    def describe_scope(self, what: str = "") -> None:
        """Log what the current scope actually contains.

        A LookupError says a control was not found; it does not say where we were
        looking. When an action fails repeatedly, print the scope's identity and its
        visible labels so the next fix is based on evidence instead of a theory.
        """
        sc = self._scope
        try:
            i = sc.element_info
            self.note(f"scope is {i.control_type} {i.name!r} {i.rectangle}")
        except Exception as exc:
            self.note(f"scope unreadable: {type(exc).__name__}")
            return
        for kind in ("Text", "Edit", "Button"):
            try:
                names = [(e.element_info.name or "").strip()
                         for e in sc.descendants(control_type=kind)]
                names = [n for n in names if n][:8]
                self.note(f"  {kind}: {names}")
            except Exception as exc:
                self.note(f"  {kind}: unreadable ({type(exc).__name__})")
        try:
            wins = [(w.element_info.name, w.element_info.rectangle)
                    for w in (self._main or sc).descendants(control_type="Window")]
            self.note(f"  open child windows: {wins[:6]}")
        except Exception:
            pass

    def dialog_open(self, title_re: str) -> bool:
        """Is a dialog matching this title currently open?"""
        import re as _re
        pat = _re.compile(title_re)
        root = self._main or self._scope
        try:
            return any(pat.search(w.element_info.name or "")
                       for w in root.descendants(control_type="Window"))
        except Exception:
            return False

    def scope_main(self):
        """Return scope to the Fakturama shell (the still-open Order tab).

        Cheaper and more reliable than re-searching by title: the shell reference is
        captured once at start()/connect() and never changes, whereas re-resolving
        '.*Fakturama.*' can match nothing while a modal child dialog is closing.
        """
        if self._main is None:
            return self.scope_window(".*Fakturama.*")
        self._scope = self._main
        self.note("scope -> main shell")
        return self._main

    # -- resolution ---------------------------------------------------------

    def find(self, logical: str, scope=None):
        sel = SELECTORS[logical]
        root = scope or self._scope
        if root is None:
            raise RuntimeError("no window scope set; call scope_window() first")

        cands = self._t2(sel, root) if sel.anchor else self._t1(sel, root)
        if len(cands) > 1 and sel.discriminator:
            cands = self._t3(sel, cands) or cands
        if len(cands) > 1 and sel.pick:
            # Some controls legitimately appear more than once and screen order picks
            # the right one deterministically. Fakturama can end up with two
            # '*New Order' editor tabs; the one 1.8 says to keep open is the first
            # opened, i.e. the leftmost.
            ordered = sorted(cands, key=lambda c: (c.element_info.rectangle.top,
                                                   c.element_info.rectangle.left))
            chosen = ordered[0] if sel.pick == "first" else ordered[-1]
            self.note(f"{logical}: {len(cands)} candidates, taking {sel.pick} by screen order")
            return chosen
        if len(cands) == 1:
            return cands[0]
        if not cands:
            raise LookupError(f"{logical}: no candidate matched {sel}")
        if self.vision_tiebreak:
            return self._t4(logical, sel, cands)
        raise AmbiguityHalt(logical, str(sel), [self._label(c) for c in cands])

    def _t1(self, sel: Selector, root) -> list:
        """Scoped tree search by control type + name/auto_id."""
        kw = {"control_type": sel.control_type}
        if sel.auto_id:
            kw["auto_id"] = sel.auto_id
        if sel.name:
            kw["title"] = sel.name
        try:
            return root.descendants(**kw)
        except MatchError:
            return []

    def _t2(self, sel: Selector, root) -> list:
        """Anchor-relative walk: 'upper icon, not lower +' as tree position, not pixels.

        Fakturama puts anchored controls in one of two shapes, both seen in 2.2.0:

          children  Text 'First Name Last Name' OWNS the two unnamed Edits;
                    likewise 'ZIP - City' and 'Salutation'.
          siblings  Text 'Addresses' sits beside the two unnamed Image icons,
                    both under a shared Pane.

        So: look inside the anchor first, fall back to the anchor's parent. Everything
        is read from the live tree at call time -- this is the concrete answer to
        'no hardcoded coordinates': ordering is a property of the widget hierarchy.
        """
        anchor = self.find(sel.anchor, scope=root)
        # `within`: the control is a CHILD of the anchor (the Order's address Edit
        # lives inside a Tab named 'Invoice address'). Otherwise the anchor is a
        # label or header sitting BESIDE the control, so search its parent.
        container = anchor if sel.within else (anchor.parent() or root)
        try:
            matches = container.descendants(control_type=sel.control_type)
        except Exception:
            return []

        # An anchored selector may ALSO carry a name. Honour it: the follow-up group
        # holds Confirmation, Invoice, Delivery and Proforma, and taking index 0
        # while ignoring name="Invoice" resolved to Confirmation -- which would have
        # created the wrong document type entirely (4.6).
        if sel.name:
            named = [m for m in matches if (m.element_info.name or "") == sel.name]
            if named:
                matches = named

        if sel.same_row:
            # Fakturama labels two fields with one Text ('First Name Last Name',
            # 'ZIP - City'), and the enclosing Pane spans the whole editor -- so a
            # plain index is meaningless there. Keep only controls whose vertical
            # centre sits inside the label's band and which start at or right of it,
            # then order left to right. Geometry read from the live tree, never
            # authored: a re-themed or re-flowed window still pairs label to field.
            a = anchor.element_info.rectangle
            band = []
            for m in matches:
                r = m.element_info.rectangle
                mid = (r.top + r.bottom) / 2
                if a.top - 4 <= mid <= a.bottom + 4 and r.left >= a.left:
                    band.append((r.left, m))
            band.sort(key=lambda t: t[0])
            matches = [m for _, m in band]

        return [matches[sel.index]] if sel.index < len(matches) else []

    def _t3(self, sel: Selector, cands: list) -> list:
        """Property disambiguation for structurally identical siblings."""
        needle = sel.discriminator.casefold()
        out = []
        for c in cands:
            props = " ".join(
                str(getattr(c.element_info, attr, "") or "")
                for attr in ("name", "help_text", "access_key", "automation_id")
            ).casefold()
            if needle in props:
                out.append(c)
        return out

    def _t4(self, logical: str, sel: Selector, cands: list):
        """Vision tiebreak. The model chooses WHICH; UIA supplies WHERE.

        The screenshot region is the anchor element's own UIA rectangle -- read at
        runtime, never authored -- so this stays inside the no-hardcoded-coordinates
        rule. Expected to fire rarely, possibly never; it exists so one unnamed SWT
        toolbar icon cannot sink the whole flow.
        """
        shot = self.screenshot(f"tiebreak-{logical.replace('.', '-')}")
        from src.vision import pick_control  # local import: optional dependency

        labels = [self._label(c) for c in cands]
        try:
            idx = pick_control(shot, sel.describe or logical, labels)
        except Exception as exc:
            raise AmbiguityHalt(logical, str(sel), labels, shot) from exc
        if idx is None or not (0 <= idx < len(cands)):
            raise AmbiguityHalt(logical, str(sel), labels, shot)
        self.note(f"T4 vision picked [{idx}] {labels[idx]} for {logical}")
        return cands[idx]

    @staticmethod
    def _label(c) -> str:
        i = c.element_info
        return f"{i.control_type}(name={i.name!r}, id={i.automation_id!r})"

    # -- actions ------------------------------------------------------------

    def click_for(self, click_logical: str, expect_logical: str, tries: int = 3):
        """Click something whose whole purpose is to open an editor, and insist.

        A single click here is a coin flip when the application is still busy.
        'New Contact' pressed three seconds after the payment method was saved
        did nothing at all, and the run died waiting for a Debtor editor that
        was never going to appear -- on the machine, in the middle of a demo.

        Same shape as open_tab: act, wait for the thing the act should produce,
        and try again rather than time out on the first miss.
        """
        last = None
        for attempt in range(1, tries + 1):
            try:
                self.click(click_logical)
            except Exception as exc:
                last = exc
                self.note(f"{click_logical}: click failed ({type(exc).__name__})")
            try:
                self.wait_exists(expect_logical, timeout=max(6.0, self.timeout / tries))
                return
            except Exception as exc:
                last = exc
                self.note(f"{click_logical}: no {expect_logical} yet "
                          f"(attempt {attempt}/{tries})")
                self.scope_main()
        raise TimeoutError(f"{click_logical}: clicked {tries}x, {expect_logical} never appeared"
                           + (f" (last: {last})" if last else ""))

    def snap(self, label: str) -> None:
        """One numbered frame, for the storyboard. Off unless --film.

        Screenshots taken at a halt show where it stopped; these show what it did.
        Run with --film and tools/storyboard.py lays them out in order, which is
        the closest thing to watching the automation without watching it.
        """
        if not self.film:
            return
        self._frame += 1
        try:
            d = self.shots.parent / "film"
            d.mkdir(parents=True, exist_ok=True)
            from PIL import ImageGrab
            safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in label)[:48]
            ImageGrab.grab().save(d / f"{self._frame:03d}-{safe}.png")
        except Exception:
            pass

    def click(self, logical: str, scope=None):
        """SWT ignores synthetic clicks on an unfocused dialog -- focus first.

        Found the hard way on Fakturama's initialization dialog: click_input() on the
        OK button did nothing until set_focus() was called on its parent window.
        """
        el = self.find(logical, scope)
        try:
            el.set_focus()
        except Exception:
            pass
        try:
            el.click_input()
        except Exception:
            el.invoke()          # fall back to the UIA Invoke pattern
        self.note(f"click {logical}")
        self.snap(f"click-{logical}")
        return el

    def _focus_field(self, el) -> None:
        """Put the caret in THIS field before typing into it.

        set_focus() alone is not enough: on SWT it raises a COMError as often as
        not, and type_keys() then sends the keystrokes to whatever held focus
        before. That is not a silent no-op -- it is a silent write into the wrong
        control. Creating a Product typed the SKU into a field nobody had focused,
        found it had not landed, retyped it with a click, and then typed the
        product NAME with no click -- which went straight into the Item Number
        field the click had just focused. The Product saved with its name as its
        SKU, the picker could not find the SKU afterwards, and the Order billed
        the previous product at the new product's price.

        One click costs nothing and removes the whole class.
        """
        try:
            el.set_focus()
        except Exception:
            pass
        try:
            el.click_input()
        except Exception:
            try:
                r = el.rectangle()
                raw_click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            except Exception:
                pass

    def set_text(self, logical: str, value, scope=None):
        el = self.find(logical, scope)
        self._focus_field(el)
        if not hasattr(el, "set_edit_text"):
            # Some fields the spec describes as text are ComboBoxes in Fakturama
            # (Country, Net or Gross). Selecting is the right verb for those.
            try:
                el.select(str(value))
                self.note(f"set {logical} = {value!r} (combo select)")
                return el
            except Exception:
                el.type_keys(str(value), with_spaces=True)
                self.note(f"set {logical} = {value!r} (combo type)")
                return el
        # set_edit_text(), never type_keys(): type_keys treats +, ^, %, ~, (, ) and {}
        # as modifiers, so the extracted phone '+49 30 5550 1420' was silently typed as
        # '$9 30 5550 1420' (+4 -> Shift+4). A money/contact path must not reinterpret
        # its own data, and no exception would ever have surfaced it.
        # TYPE, do not SetValue. A UIA ValuePattern write updates the widget's
        # displayed text but does NOT fire SWT's ModifyListener, so Fakturama's data
        # binding never sees it: the field looks correct on screen and saves as NULL.
        # That is how the Debtor's Company was lost -- visible in the editor, absent
        # from FKT_CONTACT, which then broke the exact-match search on the next run.
        # Real keystrokes fire the listener. escape_keys() keeps '+', '^', '%', '~'
        # literal so '+49 30 5550 1420' survives.
        try:
            el.type_keys("^a{BACKSPACE}", with_spaces=True)
            el.type_keys(escape_keys(value), with_spaces=True)
        except Exception as exc:
            self.note(f"{logical}: typing failed ({type(exc).__name__}), SetValue fallback")
            try:
                el.set_edit_text(str(value))
            except Exception:
                raise
        # Read back, and do not merely complain. A warning here used to be printed
        # and ignored, which is how an entire Product got created with every field
        # empty: each one logged "wrote 'MAT-DESK-02' but field reads 'Item Number'"
        # -- the control's own LABEL, meaning nothing had been typed into it at all
        # -- and the run carried on and reported the Product created.
        #
        # Most mismatches here are cosmetic: Fakturama renders '0' as '0%' and
        # '678.30' as '$678.30'. So compare on the digits, and only treat it as a
        # failure when the value genuinely did not arrive.
        for attempt in (1, 2):
            got = self.read_text(logical, scope, el=el)
            if _same_value(got, value):
                break
            if not _landed(got, value, el):
                self.note(f"{logical}: attempt {attempt} did not land "
                          f"(field reads {got!r}); retyping")
                if attempt == 2:
                    raise AmbiguityHalt(logical, str(value),
                                        [f"field still reads {got!r} after 2 attempts"],
                                        self.screenshot(f"halt-set-{logical.replace('.', '-')}"))
                el = self.find(logical, scope)
                self._focus_field(el)
                el.type_keys("^a{BACKSPACE}", with_spaces=True)
                el.type_keys(escape_keys(value), with_spaces=True)
                continue
            self.note(f"note {logical}: wrote {value!r}, field renders it as {got!r}")
            break
        self.note(f"set {logical} = {value!r}")
        return el

    #: Display formats Fakturama's segmented date widget is known to render.
    #: The order of the fields in the format IS the order the widget accepts
    #: digits in, which is what set_date() relies on.
    DATE_FORMATS = ("%b %d, %Y", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d")

    def set_date(self, logical: str, value, scope=None):
        """Write a date into an SWT CDateTime, then prove it took.

        This is NOT an Edit with text in it. It is a segmented widget that
        ignores every non-digit and feeds digits into whichever segment the
        caret is in. So typing the rendered string is actively wrong: the
        payment date 'Jul 18, 2026' put 'Sep 20, 0026' in the field -- the
        letters were dropped and '18' '2026' landed in the wrong segments.
        Nothing raised; the Invoice simply saved with today's date.

        set_edit_text() does produce the right display, but it is a UIA
        ValuePattern write and SWT's ModifyListener never fires -- the same
        trap that saved the Debtor's Company as NULL (see set_text).

        What works is what a human does: caret to the front, then the digits
        in the order the widget displays them.
        """
        el = self.find(logical, scope)
        shown = self.read_text(logical, scope)
        digits = self._date_digits(value, shown)

        for attempt in (1, 2, 3):
            # Click the LEFT EDGE, not the centre. set_focus() on SWT raises a
            # COMError as often as not, and a centre click lands the caret on
            # whichever segment sits under the middle of the field -- which is
            # the year here, so all eight digits piled into it and the field
            # read 'Sep 19, 714'. {HOME} does not help: this widget steps
            # between segments with the arrow keys, not Home/End. So: click at
            # the left edge, then walk left to be certain, then type. Digits
            # auto-advance to the next segment as each one fills.
            r = el.rectangle()
            y = (r.top + r.bottom) // 2
            try:
                el.click_input(coords=(6, y - r.top))
            except Exception:
                raw_click(r.left + 6, y)
            el.type_keys("{LEFT 6}")
            el.type_keys(digits)
            got = self.read_text(logical, scope, el=el)
            if self._parse_date(got) == value:
                self.note(f"set {logical} = {value} (field reads {got!r})")
                return el
            self.note(f"{logical}: attempt {attempt} left {got!r}, wanted {value}")
            el = self.find(logical, scope)
            shown = got or shown
            digits = self._date_digits(value, shown)

        raise AmbiguityHalt(logical, str(value), [f"field reads {self.read_text(logical, scope)!r}"],
                            self.screenshot(f"halt-date-{logical.replace('.', '-')}"))

    def read_date(self, logical: str, tries: int = 4, scope=None):
        """The date a widget actually holds, or None if it stays unreadable.

        Worth retrying: typing into a neighbouring field re-lays out the row,
        and for a moment the date widget's Value pattern comes back empty even
        though the date is still there. A single read turned a correct
        'Jul 18, 2026' into None and failed the run.
        """
        for attempt in range(tries):
            got = self._parse_date(self.read_text(logical, scope))
            if got is not None:
                return got
            if attempt < tries - 1:
                time.sleep(0.5)
        return None

    @classmethod
    def _date_format(cls, shown: str) -> str:
        for fmt in cls.DATE_FORMATS:
            try:
                datetime.strptime(shown, fmt)
                return fmt
            except ValueError:
                continue
        return cls.DATE_FORMATS[0]

    @classmethod
    def _date_digits(cls, value, shown: str) -> str:
        """The keystrokes for `value`, in the segment order `shown` is rendered in.

        The month segment renders as 'Jul' but is typed as '07', so the digits
        cannot come from the rendered string -- they come from the numeric
        equivalent of its format. Getting this backwards drops the month
        entirely and shifts every remaining digit one segment left.
        """
        fmt = cls._date_format(shown).replace("%b", "%m").replace("%B", "%m")
        return "".join(ch for ch in value.strftime(fmt) if ch.isdigit())

    @classmethod
    def _parse_date(cls, shown: str):
        for fmt in cls.DATE_FORMATS:
            try:
                return datetime.strptime(shown.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def read_text(self, logical: str, scope=None, el=None) -> str:
        """An Edit's CONTENT, not its label.

        window_text() on a UIA Edit returns its Name (e.g. 'Cust.Ref.'), so every
        postcondition that compared a typed value against it timed out. The Value
        pattern is what holds the text the user sees.

        `el` reuses a handle the caller already resolved. Resolution walks the
        descendants of an Eclipse window holding well over a thousand controls and
        is the single most expensive thing this driver does -- measured at 30% of a
        run. set_text() resolved the control and then called this, which resolved
        the very same control a second time, for every field written.

        Only ever pass a handle from the same operation. A cached handle that
        outlives its editor is how you get a fast wrong answer, and this codebase
        has produced enough silent wrong answers already.
        """
        el = el if el is not None else self.find(logical, scope)
        for getter in ("get_value", "window_text"):
            try:
                v = getattr(el, getter)()
                if v:
                    return str(v).strip()
            except Exception:
                continue
        try:
            return str(el.legacy_properties().get("Value") or "").strip()
        except Exception:
            return ""

    def select(self, logical: str, value: str, scope=None):
        """Choose a ComboBox item, tolerating Fakturama's padded option text.

        Every entry in the payment-code list ships with a trailing space
        ('Credit transfer '), so an exact select() raises IndexError. Fall back to
        matching on stripped, case-folded text and selecting by index.
        """
        el = self.find(logical, scope)
        try:
            el.select(value)
            self.note(f"select {logical} -> {value!r}")
            return el
        except Exception:
            pass

        want = str(value).strip().casefold()
        try:
            el.expand()
        except Exception:
            pass
        # Act on the ListItem itself rather than ComboBox.select(index): pywinauto's
        # index path calls selected_index(), which does texts().index(selected_text())
        # -- and texts() on these SWT combos returns the control's own labels
        # (['!editorPaymentPaymentcode!', 'Open']), never the items. It raises even
        # after the selection has already succeeded.
        items = el.descendants(control_type="ListItem")
        hits = [it for it in items if (it.element_info.name or "").strip().casefold() == want]
        if not hits:
            seen = [(it.element_info.name or "") for it in items][:10]
            raise AmbiguityHalt(logical, value, seen or ["<no items readable>"])
        chosen = hits[0]
        # Click, do not call select(). ListItem.select() is a UIA pattern call, and
        # on SWT a pattern call updates the widget without firing the listener the
        # application binds to -- the same trap as SetValue on an Edit. The payment
        # method saved with code 'Mutually defined' while the log said 'Credit
        # transfer'. A synthesised click fires it; the pattern call is the fallback.
        try:
            chosen.click_input()
        except Exception:
            chosen.select()
        self.note(f"select {logical} -> {chosen.element_info.name!r} (padded text, clicked)")
        return el

    # -- waiting: observed state, never sleep -------------------------------

    def wait_until(self, predicate, what: str, timeout: float | None = None):
        """Poll a UIA-observed postcondition. No sleeps anywhere in this codebase."""
        deadline = time.monotonic() + (timeout or self.timeout)
        last = None
        while time.monotonic() < deadline:
            try:
                if predicate():
                    self.note(f"ok: {what}")
                    return True
            except Exception as exc:
                last = exc
            time.sleep(self.poll)
        raise TimeoutError(f"timed out waiting for {what}" + (f" (last error: {last})" if last else ""))

    def wait_exists(self, logical: str, timeout: float | None = None, stable: int = 2):
        """Wait until a control exists -- and keeps existing.

        A dialog rebuilds its widgets while it populates, so a control can resolve on
        one poll and be gone on the next: observed with the product picker's search
        box, where wait_exists() passed and the very next lookup raised. Requiring N
        consecutive successful resolutions means we act on a settled tree.
        """
        streak = 0

        def present():
            nonlocal streak
            try:
                self.find(logical)
            except Exception:
                streak = 0
                return False
            streak += 1
            return streak >= stable

        return self.wait_until(present, f"{logical} to exist and settle", timeout)

    def wait_value(self, logical: str, expected: str, timeout: float | None = None):
        return self.wait_until(
            lambda: self.read_text(logical) == str(expected),
            f"{logical} == {expected!r}", timeout,
        )

    def wait_stable(self, logical: str | None = None, rounds: int = 3, scope=None):
        """Step 2.2: 'wait for the list to stabilize'.

        The selector grids are custom-painted, so there are no DataItems to count.
        Stability is therefore measured on the pixels inside the grid's own UIA
        rectangle: when consecutive captures hash identically, the list has settled.
        Pure local comparison -- no model call, no cost.
        """
        import hashlib
        import io

        last, streak = None, 0

        def steady():
            nonlocal last, streak
            buf = io.BytesIO()
            self.grid_pane(scope).capture_as_image().save(buf, format="PNG")
            h = hashlib.sha256(buf.getvalue()).hexdigest()
            streak = streak + 1 if h == last else 0
            last = h
            return streak >= rounds

        return self.wait_until(steady, f"{logical or 'grid'} pixels to settle")

    # -- reading a grid UIA cannot see --------------------------------------

    def list_view_scope(self, near_logical: str):
        """The list view that contains `near_logical`, as a scope for grid reads.

        Reading the grid against the whole shell is wrong once other editors are
        open: the biggest Edit-free Pane may belong to the Order editor (and its
        blinking caret means the pixels never settle, so wait_stable times out).
        Walk up from a control known to live in the view -- its 'create new' button --
        until an ancestor also contains a sizeable grid-shaped Pane.
        """
        node = self.find(near_logical)
        for _ in range(8):
            node = node.parent()
            if node is None:
                break
            try:
                grids = [p for p in node.descendants(control_type="Pane")
                         if not p.descendants(control_type="Edit")
                         and not p.descendants(control_type="Document")]
            except Exception:
                continue
            for g in grids:
                r = g.element_info.rectangle
                if (r.right - r.left) * (r.bottom - r.top) > 50_000:
                    self.note(f"list view scope via {near_logical}")
                    return node
        return self._scope

    def editor_scope(self, near_logical: str, min_edits: int = 3):
        """The editor form containing `near_logical`, as a scope for its other fields.

        Fakturama keeps several editors open at once, and their labels collide: while
        a New Product editor is open the Order editor is too, so Text 'VAT' matches
        twice and resolution correctly halts as ambiguous. Walking up from a control
        unique to this editor gives a scope in which its own labels are unique again.
        """
        node = self.find(near_logical)
        for _ in range(8):
            node = node.parent()
            if node is None:
                break
            try:
                if len(node.descendants(control_type="Edit")) >= min_edits:
                    self.note(f"editor scope via {near_logical}")
                    return node
            except Exception:
                continue
        return self._scope

    def items_pane(self):
        """The Order's Items table: the big Pane to the right of the 'Items' label.

        It exposes nothing to UIA -- no rows, no cells -- so it is located by tree
        geometry relative to the label that names it, then read and written through
        pixels. Anchoring this way keeps it distinct from every other grid in the
        shell, which is what grid_pane() gets wrong when several views are open.
        """
        anchor = self.find("order.items")
        a = anchor.element_info.rectangle
        root = anchor.parent().parent() or self._scope
        best, best_area = None, 0
        for p in root.descendants(control_type="Pane"):
            r = p.element_info.rectangle
            if abs(r.top - a.top) > 14 or r.left < a.right:
                continue
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best_area:
                best, best_area = p, area
        if best is None:
            raise LookupError("Items table pane not found beside the 'Items' label")
        return best

    def edit_cell(self, pane, column: str, row_index: int, value, commit: str = "{ENTER}"):
        """Set one cell of a table that exposes no cells.

        A SINGLE click on the cell makes Fakturama create a real inline editor -- an
        unnamed Edit appearing inside the table's rectangle -- so the value goes into
        an actual UIA control rather than being typed blind at the pane. (Typing at
        the pane only selected the row: Qty. stayed 1.00 and the line price never
        recalculated.)

        The cell's position comes from OCR-measured column spans and row centres, as
        percentages of the pane, and the click is relative to the pane element's own
        rectangle -- nothing absolute is stored.
        """
        from src import ocr
        from src.vision import read_layout as _vision_layout

        shot = self.shots / "_items.png"
        self.shots.mkdir(parents=True, exist_ok=True)
        pane.capture_as_image().save(shot)
        layout = ocr.read_layout(shot) if ocr.available() else _vision_layout(shot)
        if not layout.get("columns"):
            layout = _vision_layout(shot)

        cols = {colkey(c.get("name", "")): c for c in layout.get("columns", [])}
        col = cols.get(colkey(column))
        if col is None:
            raise AmbiguityHalt("items.column", column,
                                [c.get("name", "") for c in layout.get("columns", [])],
                                self.screenshot("halt-items-column"))
        rows = layout.get("rows", [])
        if row_index >= len(rows):
            raise AmbiguityHalt("items.row", f"row {row_index}",
                                [str(r.get("cells")) for r in rows][:5],
                                self.screenshot("halt-items-row"))

        r = pane.element_info.rectangle
        w, h = r.right - r.left, r.bottom - r.top
        x = max(2, min(w - 3, int(w * float(col["x_pct"]) / 100.0)))
        y = max(2, min(h - 3, int(h * float(rows[row_index]["y_pct"]) / 100.0)))

        def inline_editors():
            found = []
            for e in (self._main or self._scope).descendants(control_type="Edit"):
                er = e.element_info.rectangle
                if r.left <= er.left <= r.right and r.top <= er.top <= r.bottom:
                    found.append(e)
            return found

        # One click on an ALREADY-selected row opens its editor; on an unselected row
        # the first click only selects it. Row 0 worked with a single click because the
        # picker had just selected it, while row 1 needed selecting first. So: click,
        # and if no editor appears, click again.
        editor = None
        for attempt in (1, 2, 3):
            pane.click_input(coords=(x, y))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                es = inline_editors()
                if es:
                    editor = es[-1]
                    break
                time.sleep(0.2)
            if editor is not None:
                if attempt > 1:
                    self.note(f"items: editor opened on click {attempt}")
                break
        if editor is None:
            raise AmbiguityHalt("items.editor", column,
                                ["no inline editor appeared after three clicks on the cell"],
                                self.screenshot("halt-items-editor"))

        try:
            editor.set_focus()
        except Exception:
            pass
        editor.type_keys("^a{BACKSPACE}", with_spaces=True)
        editor.type_keys(escape_keys(value), with_spaces=True)
        editor.type_keys(commit)
        time.sleep(0.5)
        self.note(f"items: {column} row {row_index} = {value!r} "
                  f"(cell {col['x_pct']:.1f}%, {rows[row_index]['y_pct']:.1f}%)")

    def set_address_roles(self, roles: list[str]) -> bool:
        """Tick the address-type roles (2.8) in a popup UIA cannot see.

        'address type' is an Edit with a separate expander Button immediately to its
        RIGHT, outside the Edit's own rectangle -- clicking inside the Edit never
        opens anything, which is why this looked impossible for a long time. Clicking
        the Button renders a small panel of checkboxes ('Invoice address',
        'Delivery address') that is completely absent from the accessibility tree:
        no Window, no List, no CheckBox.

        So it is driven the same way as the painted grids: UIA gives the anchor
        rectangle, OCR measures the label positions inside the captured image, and the
        click lands on the checkbox beside the label -- relative to the window, never
        an authored coordinate.
        """
        from src import ocr

        field = self.find("debtor.addrtype")
        fr = field.element_info.rectangle
        buttons = [b for b in (self._main or self._scope).descendants(control_type="Button")
                   if abs(b.element_info.rectangle.top - fr.top) < 6
                   and fr.right - 4 <= b.element_info.rectangle.left <= fr.right + 40]
        if not buttons:
            self.note("2.8: no expander button beside 'address type'")
            return False

        expander = buttons[0]
        try:
            expander.set_focus()
        except Exception:
            pass
        expander.click_input()
        time.sleep(1.2)

        win = self._main or self._scope
        wr = win.element_info.rectangle
        shot = self.shots / "_roles.png"
        self.shots.mkdir(parents=True, exist_ok=True)
        win.capture_as_image().save(shot)

        if not ocr.available():
            self.note("2.8: local OCR unavailable; cannot locate the role checkboxes")
            return False

        er = expander.element_info.rectangle
        # The panel renders just below the expander. Keep the band generous: on the
        # second address it sat lower and a tight window found only one of the two
        # labels, which left the Delivery role unset.
        y_lo, y_hi = er.bottom - wr.top - 20, er.bottom - wr.top + 220
        x_lo = er.left - wr.left - 120

        def looks_like(text: str, want: str) -> bool:
            """Tolerate OCR's confusion over the leading character.

            'Invoice address' comes back as 'Jnvoice address' -- I, J and l are the
            classic OCR substitution. Dropping the first character of the target still
            keeps 'nvoice address' and 'elivery address' distinct from each other.
            """
            t, w = text.lower().strip(), want.lower().strip()
            return w in t or (len(w) > 2 and w[1:] in t)

        # The panel gives keyboard focus to its first checkbox -- it draws the dotted
        # focus border -- so Space toggles it and Tab steps between them. Keys, not
        # clicks: clicking through pywinauto activates the window, and activating
        # dismisses the panel before the click lands. Raw OS events leave focus alone.
        labels = []
        for f in ocr._boxes(shot):
            if y_lo <= f[0] <= y_hi and f[1] >= x_lo and "address" in f[3].lower():
                labels.append((f[1], f[3]))
        labels.sort()
        order = [t for _x, t in labels]
        if not order:
            self.note("2.8: no role labels found in the panel")
            return False
        self.note(f"2.8: panel offers {order}")

        def field_text() -> str:
            try:
                return field.get_value() or ""
            except Exception:
                return ""

        # If OCR only caught some of the labels, fall back to position: the panel
        # always offers Invoice then Delivery, so Tab-stepping still reaches the one
        # we want. Every attempt is verified against the field, so a wrong guess is
        # detected rather than silently accepted.
        for w in roles:
            if not any(looks_like(lbl, w) for lbl in order):
                order.append(w)
                self.note(f"2.8: {w!r} not read by OCR; reaching it by position")

        ticked, at = [], 0
        for idx, label in enumerate(order):
            wanted = next((w for w in roles if looks_like(label, w)), None)
            if wanted is None:
                continue
            # Space TOGGLES, so only press it when the role is not already set --
            # otherwise a second run would switch the role back off.
            if looks_like(field_text(), wanted):
                self.note(f"2.8: {wanted!r} already set")
                ticked.append(wanted)
                continue
            while at < idx:
                raw_key(VK_TAB)
                at += 1
            raw_key(VK_SPACE)
            time.sleep(0.6)
            shown = field_text()
            if looks_like(shown, wanted):
                ticked.append(wanted)
                self.note(f"2.8: {wanted!r} set; field reads {shown!r}")
            else:
                raw_key(VK_SPACE)   # put it back rather than leave it half-toggled
                self.note(f"2.8: Space on {label!r} did not set {wanted!r} (field {shown!r})")

        # dismiss the panel so it does not swallow later clicks
        try:
            field.click_input()
        except Exception:
            pass
        time.sleep(0.4)
        return len(ticked) == len(roles)

    def grid_pane(self, scope=None):
        """The results grid inside a selector dialog: the largest childless Pane.

        Fakturama's selector tables are custom-painted by SWT and expose no Table,
        DataGrid, List or Custom element -- just empty Panes. Picking the biggest one
        with no element children reliably lands on the table body plus its header.
        """
        root = scope or self._scope
        best, best_area = None, 0
        for p in root.descendants(control_type="Pane"):
            try:
                # The grid Pane owns a ScrollBar (which brings its own Buttons) but
                # never an Edit: every enclosing Pane holds the search box.
                if p.descendants(control_type="Edit"):
                    continue
                # ...and never a Document or Image. Without this, in the MAIN SHELL the
                # biggest Edit-free Pane is Fakturama's HTML start page (the 'yourLogo'
                # welcome browser), so the VAT and payment-terms lookups silently read
                # the wrong region and reported zero rows. In a dialog this never fired;
                # only the list views exposed it.
                if p.descendants(control_type="Document") or p.descendants(control_type="Image"):
                    continue
                r = p.element_info.rectangle
                area = (r.right - r.left) * (r.bottom - r.top)
            except Exception:
                continue
            if area > best_area:
                best, best_area = p, area
        if best is None:
            raise LookupError("no candidate grid Pane in this scope")
        return best

    def grid_rows(self, scope=None) -> list[list[str]]:
        """Read the grid's rows by capturing its UIA rectangle and reading the pixels."""
        pane = self.grid_pane(scope)
        shot = self.shots / "_grid.png"
        self.shots.mkdir(parents=True, exist_ok=True)
        pane.capture_as_image().save(shot)

        from src.vision import read_table  # local import: optional dependency

        table = read_table(shot)
        rows = [[str(c) for c in row] for row in table.get("rows", [])]

        # Row POSITIONS come from OCR, never from the model. Asked for a row's centre
        # the model estimates, and the estimates are wrong in the same way its column
        # estimates were: a first row reported at 68.2% of the grid, which clicked an
        # empty row and selected nothing while the dialog closed on OK. OCR measures.
        self._grid_y = [float(y) for y in table.get("row_y_pct", [])]
        try:
            from src import ocr
            if ocr.available():
                measured = [float(r["y_pct"]) for r in ocr.read_layout(shot).get("rows", [])]
                if len(measured) >= len(rows) and measured:
                    self._grid_y = measured[:len(rows)]
                    self.note(f"grid rows measured by OCR at {[round(y,1) for y in self._grid_y][:4]}%")
        except Exception as exc:
            self.note(f"grid row measurement fell back to the model ({type(exc).__name__})")
        how = table.get("_engine", "vision")
        self.note(f"grid: {len(rows)} row(s) via {how}, columns {table.get('columns')}")
        return rows

    def click_grid_row(self, index: int, scope=None):
        """Click a row in a grid that exposes no rows to UIA.

        There is no DataItem to click -- SWT paints these tables itself. The row's
        vertical position comes from the same vision read that produced its text, as a
        PERCENTAGE of the grid image, and the click is issued relative to the grid
        element's own rectangle (pywinauto's coords= is element-relative). So nothing
        absolute is stored: move or resize the window and the same row is still hit.
        """
        pane = self.grid_pane(scope)
        r = pane.element_info.rectangle
        h, w = r.bottom - r.top, r.right - r.left
        if index < len(getattr(self, "_grid_y", [])):
            y = int(h * self._grid_y[index] / 100.0)
        else:
            raise AmbiguityHalt("grid.row", f"row {index}",
                                ["vision returned no vertical position for this row"],
                                self.screenshot("halt-grid-row"))
        y = max(1, min(h - 2, y))
        x = max(1, min(w - 2, int(w * 0.25)))
        try:
            pane.set_focus()
        except Exception:
            pass
        pane.click_input(coords=(x, y))
        self.note(f"clicked grid row {index} at {self._grid_y[index]:.1f}% of the grid")
        return pane

    # -- artefacts ----------------------------------------------------------

    def screenshot(self, name: str) -> Path:
        self.shots.mkdir(parents=True, exist_ok=True)
        p = self.shots / f"{name}.png"
        (self._scope or self.app.top_window()).capture_as_image().save(p)
        return p

    def note(self, msg: str):
        """Log a step. Never let an un-encodable character abort the run.

        Fakturama's grids contain glyphs like the 'Standard' column's checkmark, and a
        Windows console on cp1252 raises UnicodeEncodeError trying to print them --
        which would kill the automation over a log line.
        """
        self._log.append(msg)
        # Elapsed seconds on every line. A run takes minutes and the question
        # "where does the time actually go" should be answerable from the log a
        # run already produces, not from a stopwatch and a guess.
        t = time.monotonic() - getattr(self, "_t0", time.monotonic())
        try:
            print(f"  {t:6.1f}s . {msg}", flush=True)
        except UnicodeEncodeError:
            safe = msg.encode("ascii", "replace").decode("ascii")
            print(f"  {t:6.1f}s . {safe}", flush=True)

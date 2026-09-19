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


def escape_keys(text: str) -> str:
    return "".join("{" + c + "}" if c in _TYPE_KEYS_SPECIAL else c for c in str(text))


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
    "debtor.addrtype_lbl":  Selector("Text", name="address type"),
    "debtor.addrtype":      Selector("Edit", anchor="debtor.addrtype_lbl", index=0, same_row=True),
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
    "debtor.editor_tab":    Selector("TabItem", name="*New Debtor"),
    "order.editor_tab":     Selector("TabItem", name="*New Order"),
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
    "vat.add":              Selector("Button", name="Create a new VAT"),
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
    "vat.code":             Selector("ComboBox", name="VAT code"),
    "vat.value":            Selector("Edit", name="Value"),

    "order.items":          Selector("Text", name="Items"),
    "product_picker.open":  Selector("Image", anchor="order.items", index=0,
                                     describe="the UPPER Product-selection icon beside the Items "
                                              "table -- NOT the green + control"),
    "product_dlg.search":   Selector("Edit", anchor="address_dlg.searchlbl", index=0, same_row=True),
    "product_dlg.ok":       Selector("Button", name="OK"),
    "product_dlg.cancel":   Selector("Button", name="Cancel"),
    "new.product":          Selector("Button", name="Create a new product"),
    "product.itemno":       Selector("Edit", name="Item Number"),
    "product.name":         Selector("Edit", name="Name"),
    "product.description":  Selector("Edit", name="Description"),
    "product.price_gross":  Selector("Edit", name="Price"),
    "product.cost_price":   Selector("Edit", name="cost price"),
    "product.vat":          Selector("ComboBox", name="VAT"),
    "product.stock":        Selector("Edit", name="Stock"),

    # --- item line fields (resolved within the Items row, see flow._complete_line) --
    "order.qty":            Selector("Edit", name="Qty."),
    "order.uprice":         Selector("Edit", name="U.Price"),
    "order.vat":            Selector("ComboBox", name="VAT"),
    "order.discount":       Selector("Edit", name="Discount"),
    "order.line_price":     Selector("Edit", name="Price"),

    # --- order totals (4.3) -------------------------------------------------
    "order.total_net":      Selector("Edit", name="Total Gross"),   # label depends on price mode
    "order.total_vat":      Selector("Edit", name="VAT"),
    "order.total":          Selector("Edit", name="Total"),

    # --- stages 4 and 5 -----------------------------------------------------
    "menu.documents":       Selector("Text", name="Documents"),
    "order.followup_invoice": Selector("Button", name="Invoice",
                                      anchor="order.followup", index=0,
                                      describe="'Invoice' inside the saved Order's 'Create a "
                                               "follow-up document' area -- NOT the top toolbar Invoice"),
    "order.followup":       Selector("Group", name="Create a follow-up document"),
    "invoice.payment":      Selector("ComboBox", name="Payment"),
    "invoice.paid":         Selector("CheckBox", name="paid"),
    "invoice.paid_date":    Selector("Edit", name="payment date"),
    "invoice.paid_value":   Selector("Edit", name="Value"),
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
                for c in main.descendants(control_type="Window"):
                    if pat.search(c.element_info.name or ""):
                        self._scope = c
                        self.note(f"scope -> child window {c.element_info.name!r}")
                        return c
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
        return el

    def set_text(self, logical: str, value, scope=None):
        el = self.find(logical, scope)
        try:
            el.set_focus()
        except Exception:
            pass
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
        got = self.read_text(logical, scope)
        if got.strip() != str(value).strip():
            self.note(f"WARNING {logical}: wrote {value!r} but field reads {got!r}")
        self.note(f"set {logical} = {value!r}")
        return el

    def read_text(self, logical: str, scope=None) -> str:
        """An Edit's CONTENT, not its label.

        window_text() on a UIA Edit returns its Name (e.g. 'Cust.Ref.'), so every
        postcondition that compared a typed value against it timed out. The Value
        pattern is what holds the text the user sees.
        """
        el = self.find(logical, scope)
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
        try:
            chosen.select()
        except Exception:
            chosen.click_input()
        self.note(f"select {logical} -> {chosen.element_info.name!r} (padded text)")
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

    def wait_exists(self, logical: str, timeout: float | None = None):
        return self.wait_until(lambda: bool(self.find(logical)), f"{logical} to exist", timeout)

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
        self._grid_y = [float(y) for y in table.get("row_y_pct", [])]
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
        try:
            print(f"  . {msg}", flush=True)
        except UnicodeEncodeError:
            safe = msg.encode("ascii", "replace").decode("ascii")
            print(f"  . {safe}", flush=True)

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
    describe: str = ""               # T4: what the LLM should look for


# The selector registry. Starts as a dict; becomes YAML the moment a second locale
# is real. Names below are the English strings Fakturama renders.
SELECTORS: dict[str, Selector] = {
    # --- stage 1: the Order editor -----------------------------------------
    "toolbar.order":        Selector("Button", name="Create: New Order",
                                     describe="the 'Create: New Order' button in the top toolbar"),
    "order.no":             Selector("Edit", name="No."),
    "order.date":           Selector("Edit", name="Date"),
    "order.custref":        Selector("Edit", name="Cust.Ref."),
    "order.pricemode_net":  Selector("RadioButton", name="Net"),
    "order.vat_with":       Selector("ComboBox", name="With VAT"),
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
    "address_dlg.search":   Selector("Edit", anchor="address_dlg.searchlbl", index=0),
    "address_dlg.list":     Selector("Table"),
    "address_dlg.ok":       Selector("Button", name="OK"),
    "address_dlg.cancel":   Selector("Button", name="Cancel"),
    "order.invoice_addr":   Selector("Edit", name="Invoice address"),
    "order.delivery_addr":  Selector("Edit", name="Delivery address"),

    "new.contact":          Selector("SplitButton", name="Create a new contact",
                                     describe="'Create a new contact' in the toolbar / left New panel"),
    "debtor.company":       Selector("Edit", name="Company"),
    "debtor.firstname":     Selector("Edit", name="First Name"),
    "debtor.lastname":      Selector("Edit", name="Name"),
    "debtor.salutation":    Selector("ComboBox", name="Salutation"),
    "debtor.tab_addresses": Selector("TabItem", name="Addresses"),
    "debtor.street":        Selector("Edit", name="Street"),
    "debtor.zip":           Selector("Edit", name="ZIP"),
    "debtor.city":          Selector("Edit", name="City"),
    "debtor.country":       Selector("Edit", name="Country"),
    "debtor.email":         Selector("Edit", name="E-Mail"),
    "debtor.phone":         Selector("Edit", name="Telephone"),
    "debtor.role_invoice":  Selector("CheckBox", name="Invoice address"),
    "debtor.role_delivery": Selector("CheckBox", name="Delivery address"),
    "debtor.tab_misc":      Selector("TabItem", name="Miscellaneous"),
    "debtor.alias":         Selector("Edit", name="Alias name"),
    "debtor.discount":      Selector("Edit", name="Discount"),
    "debtor.netgross":      Selector("ComboBox", name="Net or Gross"),
    "debtor.tab_payment":   Selector("TabItem", name="Payment"),
    "debtor.payment":       Selector("ComboBox", name="Payment"),

    # --- stage 2.10: payment terms -----------------------------------------
    "menu.data":            Selector("MenuItem", name="Data"),
    "menu.payments":        Selector("MenuItem", name="terms of payment"),
    "list.add":             Selector("Image", anchor="list.toolbar", index=0,
                                     describe="the green + control at the upper-right of the list"),
    "list.toolbar":         Selector("ToolBar"),
    "list.search":          Selector("Edit", anchor="address_dlg.searchlbl", index=0),
    "payment.name":         Selector("Edit", name="Name"),
    "payment.description":  Selector("Edit", name="Description"),
    "payment.code":         Selector("ComboBox", name="payment code"),
    "payment.cash_discount": Selector("Edit", name="Cash discount"),
    "payment.discount_days": Selector("Edit", name="Discount Days"),
    "payment.net_days":     Selector("Edit", name="Net Days"),

    # --- stage 3: VAT and Product ------------------------------------------
    "menu.vats":            Selector("MenuItem", name="VATs"),
    "vat.name":             Selector("Edit", name="Name"),
    "vat.description":      Selector("Edit", name="Description"),
    "vat.code":             Selector("ComboBox", name="VAT code"),
    "vat.value":            Selector("Edit", name="Value"),

    "order.items":          Selector("Text", name="Items"),
    "product_picker.open":  Selector("Image", anchor="order.items", index=0,
                                     describe="the UPPER Product-selection icon beside the Items "
                                              "table -- NOT the green + control"),
    "product_dlg.search":   Selector("Edit", anchor="address_dlg.searchlbl", index=0),
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
    "menu.documents":       Selector("MenuItem", name="Documents"),
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
    _log: list[str] = field(default_factory=list)

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        if not HAVE_UIA:
            raise RuntimeError("pywinauto is Windows-only; run the flow on Windows.")
        self.shots.mkdir(parents=True, exist_ok=True)
        self.app = Application(backend="uia").start(self.app_path)
        self.scope_window(".*Fakturama.*")
        return self

    def connect(self):
        """Attach to an already-running Fakturama instead of launching one."""
        if not HAVE_UIA:
            raise RuntimeError("pywinauto is Windows-only; run the flow on Windows.")
        self.shots.mkdir(parents=True, exist_ok=True)
        self.app = Application(backend="uia").connect(title_re=".*Fakturama.*", timeout=self.timeout)
        self.scope_window(".*Fakturama.*")
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

        spec = self.app.window(title_re=title_re)
        spec.wait("visible ready", timeout=self.timeout)
        self._scope = spec
        if self._main is None:
            self._main = spec
        self.note(f"scope -> top-level {title_re}")
        return spec

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

        Reads the anchor's parent subtree at call time, so a resized or re-themed
        window still resolves. This is the concrete answer to 'no hardcoded coordinates'.
        """
        anchor = self.find(sel.anchor, scope=root)
        parent = anchor.parent() or root
        matches = parent.descendants(control_type=sel.control_type)
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
        el.set_edit_text("")
        el.type_keys(str(value), with_spaces=True)
        self.note(f"set {logical} = {value!r}")
        return el

    def read_text(self, logical: str, scope=None) -> str:
        return (self.find(logical, scope).window_text() or "").strip()

    def select(self, logical: str, value: str, scope=None):
        el = self.find(logical, scope)
        el.select(value)
        self.note(f"select {logical} -> {value!r}")
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

    def wait_stable(self, logical: str, rounds: int = 3):
        """Step 2.2: 'wait for the list to stabilize' -- row count unchanged N polls running."""
        seen, streak = None, 0

        def steady():
            nonlocal seen, streak
            n = len(self.find(logical).descendants(control_type="DataItem"))
            streak = streak + 1 if n == seen else 0
            seen = n
            return streak >= rounds

        return self.wait_until(steady, f"{logical} row count to settle")

    # -- artefacts ----------------------------------------------------------

    def screenshot(self, name: str) -> Path:
        self.shots.mkdir(parents=True, exist_ok=True)
        p = self.shots / f"{name}.png"
        (self._scope or self.app.top_window()).capture_as_image().save(p)
        return p

    def note(self, msg: str):
        self._log.append(msg)
        print(f"  · {msg}", flush=True)

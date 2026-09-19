"""One-time Fakturama initialization.

Fakturama's first launch shows a 'Fakturama Initialization' dialog asking for a
working directory before the main window exists. This accepts the defaults so the
workspace and database get created. Run once per machine; the automation proper
(src/run.py) assumes an initialized Fakturama.

    python tools\\first_run.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pywinauto import Application, Desktop  # noqa: E402

EXE = r"C:\Program Files\Fakturama2\Fakturama.exe"
WORKDIR = str(Path.home() / "Fakturama2")


def find_window(title_re: str, timeout: float = 90) -> object | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for w in Desktop(backend="uia").windows():
            try:
                if title_re.lower() in (w.window_text() or "").lower():
                    return w
            except Exception:
                pass
        time.sleep(1)
    return None


def main() -> int:
    if not find_window("Fakturama", timeout=2):
        print(f"launching {EXE}")
        Application(backend="uia").start(EXE)

    init = find_window("Fakturama Initialization", timeout=90)
    if init:
        print("initialization dialog found")
        def one(title, ctype):
            """UIAWrapper has descendants(), not child_window() (that is a
            WindowSpecification method). Same lookup style as src/driver.py."""
            m = [e for e in init.descendants(control_type=ctype)
                 if (e.element_info.name or "") == title]
            return m[0] if m else None

        try:
            edit = one("Working Directory", "Edit")
            current = edit.get_value() if hasattr(edit, "get_value") else edit.window_text()
            print(f"  working directory currently: {current!r}")
            if not (current or "").strip():
                edit.set_edit_text(WORKDIR)
                print(f"  set to {WORKDIR}")
        except Exception as exc:
            print(f"  could not read/set working directory: {exc}")

        try:
            cb = one("use default database settings", "CheckBox")
            if cb.get_toggle_state() != 1:
                cb.toggle()
            print("  'use default database settings' checked")
        except Exception as exc:
            print(f"  checkbox: {exc}")

        one("OK", "Button").click_input()
        print("  clicked OK")
    else:
        print("no initialization dialog - already initialized?")

    # After OK, Fakturama shows "To switch the workspace, Fakturama will be
    # restarted" and then relaunches. Dismiss any confirmation dialogs until the
    # real main window (title contains Fakturama, not 'Initialization') appears.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        wins = Desktop(backend="uia").windows()
        titles = [(w, (w.window_text() or "")) for w in wins]

        main = [w for w, t in titles
                if "fakturama" in t.lower() and "initialization" not in t.lower()]
        if main:
            print(f"main window: {main[0].window_text()!r}")
            return 0

        for w, t in titles:
            if "initialization" not in t.lower():
                continue
            for dlg in w.descendants(control_type="Window"):
                dname = dlg.element_info.name or ""
                if dname in ("Information", "Confirmation", "Question", "Warning"):
                    oks = [b for b in dlg.descendants(control_type="Button")
                           if (b.element_info.name or "") in ("OK", "Yes", "Continue")]
                    if oks:
                        print(f"  dismissing {dname!r} -> {oks[0].element_info.name}")
                        oks[0].click_input()
                        time.sleep(2)
        time.sleep(2)

    print("main window did not appear within 300s")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

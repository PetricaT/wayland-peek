"""
wayland-peek - portal edition
Keyboard shortcuts are handled via the XDG GlobalShortcuts portal so that
the app works inside a Flatpak sandbox with zero extra setup from the user.

Portal flow (all async over D-Bus):
  1. CreateSession   → get session_handle
  2. BindShortcuts   → show one-time consent dialog; user assigns keys
  3. Listen for Activated / Deactivated signals on the portal object
     • "freeze-hold"   → Ctrl held   (hold-to-pause, release to resume)
     • "freeze-toggle" → Shift press (toggle pause on/off)
"""

import os
import random
import string
import subprocess
import sys
import threading
import time

from colorama import Fore, Style
from PySide6 import QtCore, QtWidgets
from PySide6.QtUiTools import QUiLoader

# ---------------------------------------------------------------------------
# Try to import dbus-python (python-dbus).  This is the only new dependency.
# ---------------------------------------------------------------------------
try:
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib
    _HAVE_DBUS = True
except ImportError:
    _HAVE_DBUS = False


# ---------------------------------------------------------------------------
# Keyboard state manager (unchanged API from the evdev version)
# ---------------------------------------------------------------------------

class KeyboardManager:
    def __init__(self):
        self._run_event = threading.Event()
        self._run_event.set()
        self._ctrl_held = False
        self._shift_frozen = False

    def _update(self):
        if self._ctrl_held or self._shift_frozen:
            self._run_event.clear()
        else:
            self._run_event.set()

    def on_ctrl_down(self):
        self._ctrl_held = True
        self._update()

    def on_ctrl_up(self):
        self._ctrl_held = False
        self._update()

    def on_shift_toggle(self):
        self._shift_frozen = not self._shift_frozen
        self._update()

    def wait_if_paused(self):
        self._run_event.wait()


# ---------------------------------------------------------------------------
# XDG GlobalShortcuts portal helper
# ---------------------------------------------------------------------------

PORTAL_BUS   = "org.freedesktop.portal.Desktop"
PORTAL_PATH  = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

# IDs we register with the portal – the user maps actual keys to them
# in the consent dialog.
SHORTCUT_CTRL_DOWN   = "freeze-hold-down"    # "hold Ctrl" → press
SHORTCUT_CTRL_UP     = "freeze-hold-up"      # "hold Ctrl" → release
SHORTCUT_SHIFT_TOGGLE = "freeze-toggle"      # "toggle freeze"


def _random_token(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


class PortalShortcutListener:
    """
    Sets up a GlobalShortcuts session and wires Activated/Deactivated
    signals to the KeyboardManager.  Runs its own GLib main loop in a
    daemon thread so it doesn't interfere with the Qt event loop.
    """

    def __init__(self, handler: KeyboardManager):
        self._handler = handler
        self._session_handle = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Give the portal setup a few seconds before we continue
        self._ready.wait(timeout=10)

    # ------------------------------------------------------------------ #
    # Internal – runs in the daemon thread                                 #
    # ------------------------------------------------------------------ #

    def _run(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._loop = GLib.MainLoop()
        self._bus  = dbus.SessionBus()

        try:
            self._portal = self._bus.get_object(PORTAL_BUS, PORTAL_PATH)
        except dbus.DBusException as e:
            print(f"[portal] Cannot connect to xdg-desktop-portal: {e}")
            print("[portal] Is xdg-desktop-portal installed and running?")
            self._ready.set()
            return

        # Wire up the Activated / Deactivated signals
        self._bus.add_signal_receiver(
            self._on_activated,
            signal_name="Activated",
            dbus_interface=PORTAL_IFACE,
            bus_name=PORTAL_BUS,
            path=PORTAL_PATH,
        )
        self._bus.add_signal_receiver(
            self._on_deactivated,
            signal_name="Deactivated",
            dbus_interface=PORTAL_IFACE,
            bus_name=PORTAL_BUS,
            path=PORTAL_PATH,
        )

        self._create_session()
        self._loop.run()

    def _await_response(self, handle_path: str, callback):
        """Subscribe to a Request::Response signal and call callback(results)."""
        obj = self._bus.get_object(PORTAL_BUS, handle_path)
        done = threading.Event()

        def _response(response_code, results):
            done.set()
            if response_code == 0:
                callback(results)
            else:
                print(f"[portal] Request {handle_path} cancelled or denied (code={response_code})")

        obj.connect_to_signal("Response", _response, dbus_interface=REQUEST_IFACE)
        return done

    # ------------------------------------------------------------------ #
    # Portal session lifecycle                                             #
    # ------------------------------------------------------------------ #

    def _create_session(self):
        token = _random_token()
        session_token = _random_token()
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        handle_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        options = dbus.Dictionary({
            "handle_token":        dbus.String(token),
            "session_handle_token": dbus.String(session_token),
        }, signature="sv")

        self._await_response(handle_path, self._on_session_created)

        try:
            self._portal.CreateSession(
                options,
                dbus_interface=PORTAL_IFACE,
            )
        except dbus.DBusException as e:
            print(f"[portal] CreateSession failed: {e}")
            self._ready.set()

    def _on_session_created(self, results):
        self._session_handle = str(results["session_handle"])
        print(f"[portal] Session created: {self._session_handle}")
        self._bind_shortcuts()

    def _bind_shortcuts(self):
        token = _random_token()
        sender = self._bus.get_unique_name().lstrip(":").replace(".", "_")
        handle_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        shortcuts = dbus.Array([
            (
                dbus.String(SHORTCUT_CTRL_DOWN),
                dbus.Dictionary({
                    "description":        dbus.String("Freeze updates (hold)"),
                    "preferred_trigger":  dbus.String("CTRL"),
                }, signature="sv"),
            ),
            (
                dbus.String(SHORTCUT_CTRL_UP),
                dbus.Dictionary({
                    "description":        dbus.String("Resume updates (release Ctrl)"),
                    "preferred_trigger":  dbus.String("CTRL"),
                }, signature="sv"),
            ),
            (
                dbus.String(SHORTCUT_SHIFT_TOGGLE),
                dbus.Dictionary({
                    "description":        dbus.String("Toggle freeze on/off"),
                    "preferred_trigger":  dbus.String("SHIFT"),
                }, signature="sv"),
            ),
        ], signature="(sa{sv})")

        options = dbus.Dictionary({"handle_token": dbus.String(token)}, signature="sv")

        self._await_response(handle_path, self._on_shortcuts_bound)

        try:
            self._portal.BindShortcuts(
                dbus.ObjectPath(self._session_handle),
                shortcuts,
                dbus.String(""),   # parent window — empty for now
                options,
                dbus_interface=PORTAL_IFACE,
            )
        except dbus.DBusException as e:
            print(f"[portal] BindShortcuts failed: {e}")
            self._ready.set()

    def _on_shortcuts_bound(self, results):
        bound = results.get("shortcuts", [])
        print(f"[portal] Shortcuts bound: {[str(s[0]) for s in bound]}")
        self._ready.set()

    # ------------------------------------------------------------------ #
    # Signal handlers                                                      #
    # ------------------------------------------------------------------ #

    def _on_activated(self, session_handle, shortcut_id, timestamp, options):
        sid = str(shortcut_id)
        print(f"[portal] Activated: {sid}")
        if sid == SHORTCUT_CTRL_DOWN:
            self._handler.on_ctrl_down()
        elif sid == SHORTCUT_SHIFT_TOGGLE:
            self._handler.on_shift_toggle()

    def _on_deactivated(self, session_handle, shortcut_id, timestamp, options):
        sid = str(shortcut_id)
        print(f"[portal] Deactivated: {sid}")
        if sid == SHORTCUT_CTRL_DOWN:
            self._handler.on_ctrl_up()


# ---------------------------------------------------------------------------
# Fallback: warn the user when portal is unavailable
# ---------------------------------------------------------------------------

def start_keyboard_listener(handler: KeyboardManager):
    if not _HAVE_DBUS:
        print(
            Fore.YELLOW + Style.BRIGHT + "Warning:" + Style.RESET_ALL
            + " python-dbus or PyGObject not found. "
            "Install them for keyboard shortcuts:\n"
            "  pip install dbus-python\n"
            "  # or via your distro: python3-dbus python3-gi"
        )
        return

    print("[portal] Starting GlobalShortcuts portal listener…")
    PortalShortcutListener(handler)


# ---------------------------------------------------------------------------
# Qt UI (unchanged from the evdev version)
# ---------------------------------------------------------------------------

class LabelUpdater(QtCore.QObject):
    position_changed = QtCore.Signal(str)


class MainApp:
    def __init__(self):
        self.app = QtWidgets.QApplication(sys.argv)
        self.window = QUiLoader().load("app.ui")
        self.polling_interval = 0.1

        self.cursor_label = self.window.findChild(QtWidgets.QLabel, "CursorPositionLabel")
        if self.cursor_label is not None:
            self.cursor_label.setText(self._query_info("cursor_info"))

        self._updater = LabelUpdater()
        self._updater.position_changed.connect(self.cursor_label.setText)

        self.window_label = self.window.findChild(QtWidgets.QLabel, "WindowInfoLabel")
        if self.window_label is not None:
            self.window_label.setText(self._query_info("window_info"))

        self._window_updater = LabelUpdater()
        self._window_updater.position_changed.connect(self.window_label.setText)

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self.window.show()
        self.app.exec()

    def _poll_loop(self):
        while True:
            keyboard_handler.wait_if_paused()
            pos  = self._query_info("cursor_info")
            pos2 = self._query_info("window_info")
            self._updater.position_changed.emit(pos)
            self._window_updater.position_changed.emit(pos2)
            time.sleep(self.polling_interval)

    def _query_info(self, kind: str | None = None) -> str:
        result = (
            subprocess.run(
                [
                    "kdotool",
                    "getmouselocation",
                    "getwindowname",
                    "getwindowclassname",
                    "getwindowgeometry",
                    "getwindowpid",
                ],
                stdout=subprocess.PIPE,
            )
            .stdout.strip()
            .decode("utf-8")
        )

        try:
            if kind == "cursor_info":
                lines = result.split("\n")
                parts = lines[0].split(" ")
                x      = parts[0].split(":")[1]
                y      = parts[1].split(":")[1]
                screen = parts[2].split(":")[1]
                uuid   = (
                    parts[3]
                    .replace("{", "").replace("}", "").replace("window:", "")
                )
                return f"X: {x}, Y: {y}\nScreen: {screen}\nUUID: {uuid}"
            else:
                lines = result.split("\n")
                title    = lines[1]
                exe      = lines[2]
                pos_raw  = lines[4].split(":")[1].split(",")
                pos_str  = f"{int(pos_raw[0])}, {int(pos_raw[1])}"
                geo_raw  = lines[5].split(":")[1].split("x")
                geo_str  = f"{int(geo_raw[0])}x{int(geo_raw[1])}"
                pid      = lines[6]
                return (
                    f"Title: {title}\nExe name: {exe}\n"
                    f"Window position: {pos_str}\nWindow geometry: {geo_str}\n"
                    f"Window PID: {pid}"
                )
        except IndexError as e:
            return f"Index error| {e}"
        except ValueError as e:
            return f"Value error| {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global keyboard_handler
    keyboard_handler = KeyboardManager()
    start_keyboard_listener(keyboard_handler)
    MainApp()


if __name__ == "__main__":
    if os.name != "posix":
        print(
            "This tool only supports "
            + Fore.GREEN + Style.BRIGHT + "Linux" + Style.RESET_ALL
            + ", sorry…"
        )
        sys.exit(0)

    try:
        subprocess.run(["kdotool", "--version"], stdout=subprocess.PIPE)
    except FileNotFoundError:
        print(
            Fore.RED + Style.BRIGHT + "kdotool" + Style.RESET_ALL
            + " is not installed, please install it first"
        )
        sys.exit(0)

    print(f"PID: {os.getpid()}")
    main()

import os
import subprocess
import sys
import threading
import time

import evdev
from colorama import Fore, Style
from evdev import InputDevice, ecodes
from PySide6 import QtCore, QtWidgets
from PySide6.QtUiTools import QUiLoader


class keyboardManager:
    def __init__(self):
        self._run_event = threading.Event()
        self._run_event.set()

        self._ctrl_pressed = False
        self._shift_frozen = False

    def _update(self):
        if self._ctrl_pressed or self._shift_frozen:
            self._run_event.clear()
        else:
            self._run_event.set()

    def handle_ctrl_switch(self, value: bool):
        self._ctrl_pressed = value
        self._update()

    def handle_shift_switch(self):
        self._shift_frozen = not self._shift_frozen
        self._update()

    def wait_if_paused(self):
        self._run_event.wait()


def _find_keyboards() -> list[InputDevice]:
    """
    Return all /dev/input devices that look like real keyboards.
    Requires a broad spread of letter, number, and modifier keys so that
    tablets, gamepads, and other HID devices that only expose a few KEY_*
    codes are excluded.
    """
    # Every key in this set must be present for the device to qualify.
    REQUIRED_KEYS = {
        ecodes.KEY_ESC,
        ecodes.KEY_LEFTCTRL,
        ecodes.KEY_LEFTSHIFT,
        ecodes.KEY_A,
        ecodes.KEY_Z,  # letters
        ecodes.KEY_ENTER,
        ecodes.KEY_SPACE,  # basics
    }
    keyboards = []
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities(verbose=True)
            if ecodes.EV_KEY in caps:
                key_codes = set(caps[ecodes.EV_KEY])
                if REQUIRED_KEYS.issubset(key_codes):
                    print(f"[keyboard] Found device: {dev.name!r} ({dev.path})")
                    print(caps)
                    keyboards.append(dev)
                else:
                    # missing = REQUIRED_KEYS - key_codes
                    missing = REQUIRED_KEYS
                    print(
                        f"[keyboard] Skipping {dev.name!r} ({dev.path}) — not a keyboard, missing: {[ecodes.KEY[k] for k in missing]}"
                    )
        except (PermissionError, OSError) as e:
            print(f"[keyboard] Skipping {path}: {e}")
    return keyboards


def _listen_keyboard(dev: InputDevice, handler: keyboardManager):
    """
    Read key events from a single InputDevice in a tight loop.
    Runs in its own daemon thread (one per keyboard device found).

    evdev key event values:
        0 = key up
        1 = key down
        2 = key hold (auto-repeat)
    """
    print(f"[keyboard] Listening on {dev.name!r} ({dev.path})")
    try:
        for event in dev.read_loop():
            if event.type != ecodes.EV_KEY:
                continue

            code = event.code
            value = event.value  # 0=up, 1=down, 2=hold

            # ESC hard exit
            if code == ecodes.KEY_ESC and value == 1:
                print("[keyboard] ESC pressed → exiting")
                os._exit(0)

            # CTRL hold-to-freeze
            if code in (ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL):
                if value == 1:
                    print(f"[keyboard] CTRL down (code={code}, device={dev.path})")
                    handler.handle_ctrl_switch(True)
                elif value == 0:
                    print(f"[keyboard] CTRL up   (code={code}, device={dev.path})")
                    handler.handle_ctrl_switch(False)

            # Left Shift toggle freeze
            if code == ecodes.KEY_LEFTSHIFT and value == 1:
                print(f"[keyboard] SHIFT toggle (device={dev.path})")
                handler.handle_shift_switch()

    except (OSError, IOError) as e:
        print(f"[keyboard] Device {dev.path} lost: {e}")
        pass


def start_keyboard_listeners(handler: keyboardManager):
    """
    Discover all keyboard devices and spawn one daemon listener thread each.
    Also spawns a watchdog that re-scans every 5 s to pick up hot-plugged
    keyboards.
    """
    active_paths: set[str] = set()

    def _spawn(dev: InputDevice):
        active_paths.add(dev.path)
        t = threading.Thread(target=_listen_keyboard, args=(dev, handler), daemon=True)
        t.start()

    # Initial scan
    for dev in _find_keyboards():
        _spawn(dev)

    if not active_paths:
        print(
            Fore.YELLOW
            + Style.BRIGHT
            + "Warning:"
            + Style.RESET_ALL
            + " No keyboard devices found in /dev/input. "
            "Make sure your user is in the 'input' group:\n"
            "  sudo usermod -aG input $USER  (then re-login)"
        )

    # Watchdog for hot-plug
    def _watchdog():
        while True:
            time.sleep(5)
            for dev in _find_keyboards():
                if dev.path not in active_paths:
                    print(f"[keyboard] New keyboard detected: {dev.name} ({dev.path})")
                    _spawn(dev)

    threading.Thread(target=_watchdog, daemon=True).start()


class MainApp:
    def __init__(self):
        self.app = QtWidgets.QApplication(sys.argv)
        self.window = QUiLoader().load("app.ui")

        self.polling_interval = 0.1

        self.cursor_label = self.window.findChild(
            QtWidgets.QLabel, "CursorPositionLabel"
        )
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
            keyboardHandler.wait_if_paused()
            pos = self._query_info("cursor_info")
            pos2 = self._query_info("window_info")
            self._updater.position_changed.emit(pos)
            self._window_updater.position_changed.emit(pos2)
            time.sleep(self.polling_interval)

    def _query_info(self, type: str | None = None) -> str:
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
            if type == "cursor_info":
                result = result.split("\n")
                split_cords = result[0].split(" ")
                self._x = split_cords[0].split(":")[1]
                self._y = split_cords[1].split(":")[1]
                self._screen = split_cords[2].split(":")[1]
                self._window_uuid = (
                    split_cords[3]
                    .replace("{", "")
                    .replace("}", "")
                    .replace("window:", "")
                )
                return f"X: {self._x}, Y: {self._y}\nScreen: {self._screen}\nUUID: {self._window_uuid}"
            else:
                result = result.split("\n")
                self._window_title = result[1]
                self._executable_name = result[2]
                self._window_position = result[4].split(":")[1].split(",")
                self._window_position = (
                    f"{int(self._window_position[0])}, {int(self._window_position[1])}"
                )
                self._window_geometry = result[5].split(":")[1].split("x")
                self._window_geometry = (
                    f"{int(self._window_geometry[0])}x{int(self._window_geometry[1])}"
                )
                self._window_pid = result[6]
                return f"Title: {self._window_title}\nExe name: {self._executable_name}\nWindow position: {self._window_position}\nWindow geometry: {self._window_geometry}\nWindow PID: {self._window_pid}"
        except IndexError as e:
            return f"Index error| {e}"
        except ValueError as e:
            return f"Value error| {e}"


class LabelUpdater(QtCore.QObject):
    position_changed = QtCore.Signal(str)


def main():
    global keyboardHandler
    keyboardHandler = keyboardManager()
    start_keyboard_listeners(keyboardHandler)
    MainApp()


if __name__ == "__main__":
    if os.name != "posix":
        print(
            "This tool only supports "
            + Fore.GREEN
            + Style.BRIGHT
            + "Linux"
            + Style.RESET_ALL
            + ", sorry...."
        )
        sys.exit(0)
    try:
        subprocess.run(["kdotool", "--version"], stdout=subprocess.PIPE)
    except FileNotFoundError:
        print(
            Fore.RED
            + Style.BRIGHT
            + "kdotool"
            + Style.RESET_ALL
            + " is not installed, please install it first"
        )
        sys.exit(0)

    print(f"PID: {os.getpid()}")
    main()

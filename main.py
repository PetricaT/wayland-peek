import os
import subprocess
import sys
import threading
import time

import pynput.keyboard as keyboard
from colorama import Fore, Style
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

    def handle_ctrl_switch(self, value):
        self._ctrl_pressed = value
        self._update()

    def handle_shift_switch(self):
        self._shift_frozen = not self._shift_frozen
        self._update()

    def wait_if_paused(self):
        """Block the calling thread until polling is allowed."""
        self._run_event.wait()


def get_mouse_location() -> str:
    result = subprocess.run(["kdotool", "getmouselocation"], stdout=subprocess.PIPE)
    return result.stdout.strip().decode("utf-8")


class MainApp:
    def __init__(self):
        self.app = QtWidgets.QApplication(sys.argv)
        self.window = QUiLoader().load("app.ui")

        self.label = self.window.findChild(QtWidgets.QLabel, "CursorPositionLabel")
        self.label.setText(get_mouse_location())

        self._updater = LabelUpdater()
        self._updater.position_changed.connect(self.label.setText)

        self.polling_interval = 0.1

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        self.window.show()
        self.app.exec()

    def _poll_loop(self):
        while True:
            keyboardHandler.wait_if_paused()
            pos = get_mouse_location()
            self._updater.position_changed.emit(pos)
            time.sleep(self.polling_interval)


class LabelUpdater(QtCore.QObject):
    position_changed = QtCore.Signal(str)


def main():
    global keyboardHandler
    keyboardHandler = keyboardManager()

    # Panic button
    keyboard.Listener(
        on_press=lambda key: sys.exit(0) if key == keyboard.Key.esc else None
    ).start()

    # CTRL hold to freeze
    keyboard.Listener(
        on_press=lambda key: (
            keyboardHandler.handle_ctrl_switch(True)
            if key == keyboard.Key.ctrl
            else None
        ),
        on_release=lambda key: (
            keyboardHandler.handle_ctrl_switch(False)
            if key == keyboard.Key.ctrl
            else None
        ),
    ).start()

    # Shift toggle freeze
    keyboard.Listener(
        on_press=lambda key: (
            keyboardHandler.handle_shift_switch()
            if key == keyboard.Key.shift_l
            else None
        )
    ).start()

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

    # print the process PID for debugging
    print(f"PID: {os.getpid()}")

    main()

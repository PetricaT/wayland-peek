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
        # Block the calling thread until polling is allowed.
        self._run_event.wait()


def parse_cursor_location() -> str:
    result = (
        subprocess.run(["kdotool", "getmouselocation"], stdout=subprocess.PIPE)
        .stdout.strip()
        .decode("utf-8")
    )
    result = result.split(":")
    try:
        _x = result[1].split(" ")[0]
        _y = result[2].split(" ")[0]
        _screen = result[3].split(" ")[0]
        _window_uuid = result[4].replace("{", "").replace("}", "")

        result = f"X: {_x}, Y: {_y}\nScreen: {_screen}\nUUID: {_window_uuid}"
    except IndexError:
        result = ""
    return result


class MainApp:
    def __init__(self):
        self.app = QtWidgets.QApplication(sys.argv)
        self.window = QUiLoader().load("app.ui")

        self.polling_interval = 0.1

        self.cursor_label = self.window.findChild(
            QtWidgets.QLabel, "CursorPositionLabel"
        )
        self.cursor_label.setText(self._query_info("cursor_info"))

        self.window_label = self.window.findChild(QtWidgets.QLabel, "WindowInfoLabel")
        self.window_label.setText(self._query_info("window_info"))

        self._updater = LabelUpdater()
        self._updater.position_changed.connect(self.cursor_label.setText)
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
        #       x:3638 y:766 screen:0 window:{b34df91d-e16f-4e24-a8c2-5c1a07fab161}
        #       ~ : zsh — Konsole
        #       org.kde.konsole
        #       Window {b34df91d-e16f-4e24-a8c2-5c1a07fab161}
        #           Position: 2648.599999999982,125.90000000000202
        #           Geometry: 1477.000000000001x870
        #           16704

        # ['x:886 y:881 screen:1 window:{09937981-4a68-43ed-a0f4-73f0ce8e8a8c}', 'main.py - wayland-peek - Visual Studio Code', 'code', 'Window {09937981-4a68-43ed-a0f4-73f0ce8e8a8c}', '  Position: 0,28', '  Geometry: 1919x1412', '2562']
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
                # Title: wayland-peek
                # Exe name: python3
                # Window position:   Position: 2016.7000000000012,627.6000000000015
                # Window geometry:   Geometry:
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

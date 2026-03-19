"""
cx_Freeze setup script for Wayland Peek.

Build targets:
  Native executable:  python setup.py build
  AppImage:           python setup.py bdist_appimage

kdotool (v0.2.2) is downloaded at build time and bundled into the
output directory so the application ships as a fully self-contained
package. The helper script below (fetch_kdotool.py) handles the
download + extraction; this setup.py calls it automatically via the
build_exe post-install hook.

Requirements:
  pip install cx_Freeze requests
"""

import stat
import tarfile
import urllib.request
from pathlib import Path

from cx_Freeze import Executable, setup
from cx_Freeze.command.build_exe import build_exe as _BuildEXE

# kdotool release to bundle
KDOTOOL_VERSION = "0.2.2"
KDOTOOL_URL = (
    f"https://github.com/jinliu/kdotool/releases/download/"
    f"v{KDOTOOL_VERSION}/"
    f"kdotool-{KDOTOOL_VERSION}-x86_64-unknown-linux-gnu.tar.gz"
)
KDOTOOL_BINARY_NAME = "kdotool"


def _fetch_and_bundle_kdotool(build_dir: str) -> None:
    """Download kdotool, extract the binary, and place it in *build_dir*."""
    dest_bin = Path(build_dir) / KDOTOOL_BINARY_NAME
    if dest_bin.exists():
        print(f"[setup] kdotool already present at {dest_bin}, skipping download.")
        return

    archive_path = Path(build_dir) / f"kdotool-{KDOTOOL_VERSION}.tar.gz"
    print(f"[setup] Downloading kdotool {KDOTOOL_VERSION} from:\n  {KDOTOOL_URL}")
    urllib.request.urlretrieve(KDOTOOL_URL, archive_path)

    print(f"[setup] Extracting {archive_path} …")
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith(KDOTOOL_BINARY_NAME) and not member.isdir():
                member.name = KDOTOOL_BINARY_NAME
                tar.extract(member, path=build_dir)
                break
        else:
            raise RuntimeError(
                f"Could not find '{KDOTOOL_BINARY_NAME}' inside the archive. "
                "Please inspect the tarball and adjust KDOTOOL_BINARY_NAME."
            )

    # Make binary executable
    dest_bin.chmod(dest_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    archive_path.unlink(missing_ok=True)  # clean up tarball
    print(f"[setup] kdotool installed to {dest_bin}")


class BuildEXEWithKdotool(_BuildEXE):
    """Extends the standard build_exe step to bundle kdotool."""

    def run(self):
        super().run()
        if self.build_exe is not None:
            _fetch_and_bundle_kdotool(self.build_exe)


build_options = {
    "packages": ["queue", "pynput", "evdev"],
    "excludes": [],
    "include_files": [],
    "optimize": 1,
}

base = "gui"

executables = [
    Executable(
        "main.py",
        base=base,
        target_name="Wayland Peek",
        icon="assets/icon.png",
    )
]

setup(
    name="wayland-peek",
    version="1.0",
    description="A window spy alternative. Kdotool GUI implementation.",
    options={
        "build_exe": build_options,
    },
    executables=executables,
    cmdclass={"build_exe": BuildEXEWithKdotool},
)

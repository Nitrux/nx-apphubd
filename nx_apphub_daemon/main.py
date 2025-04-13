#!/usr/bin/env python3

#############################################################################################################################################################################
#   The license used for this file and its contents is: BSD-3-Clause                                                                                                        #
#                                                                                                                                                                           #
#   Copyright <2025> <Uri Herrera <uri_herrera@nxos.org>>                                                                                                                   #
#                                                                                                                                                                           #
#   Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:                          #
#                                                                                                                                                                           #
#    1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.                                        #
#                                                                                                                                                                           #
#    2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer                                      #
#       in the documentation and/or other materials provided with the distribution.                                                                                         #
#                                                                                                                                                                           #
#    3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software                    #
#       without specific prior written permission.                                                                                                                          #
#                                                                                                                                                                           #
#    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,                      #
#    THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS                  #
#    BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE                 #
#    GOODS OR SERVICES; LOSS OF USE, DATA,   OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,                      #
#    STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.   #
#############################################################################################################################################################################

import os
import shutil
import subprocess
import time
import logging
import re
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

watch_dir = Path.home() / ".local/bin/nx-apphub"
extract_dir = Path.home() / ".cache/nx-apphubd"
apps_dir = Path.home() / ".local/share/applications"
icons_dir = Path.home() / ".local/share/icons"
log_file = Path.home() / ".nx-apphubd.log"

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def sanitize_name(name: str) -> str:
    return re.sub(r'[:+~]', '-', name)

def extract_appbox(appbox_path: Path, quiet=True):
    logging.info(f"Extracting {appbox_path}")
    extract_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(extract_dir)

    kwargs = {}
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL

    subprocess.run(
        [str(appbox_path), "--appimage-extract"],
        check=True,
        **kwargs
    )

def find_desktop_file():
    desktop_dir = extract_dir / "squashfs-root"
    for root, _, files in os.walk(desktop_dir):
        for file in files:
            if file.endswith(".desktop"):
                return Path(root) / file
    return None

def find_icon_file():
    icon_dir = extract_dir / "squashfs-root"
    for root, _, files in os.walk(icon_dir):
        for file in files:
            if file.lower().endswith((".png", ".svg", ".xpm")):
                return Path(root) / file
    return None

def integrate_appbox(appbox_path: Path):
    extract_dir.mkdir(parents=True, exist_ok=True)

    if not os.access(appbox_path, os.X_OK):
        logging.warning(f"{appbox_path.name} is not executable; setting mode 755")
        appbox_path.chmod(0o755)

    if not appbox_path.is_file():
        logging.error(f"{appbox_path} is not a valid file")
        return

    extract_appbox(appbox_path, quiet=True)

    desktop_file = find_desktop_file()
    if not desktop_file:
        logging.warning(f"No desktop file found in {appbox_path}")
        shutil.rmtree(extract_dir / "squashfs-root", ignore_errors=True)
        return

    icon_file = find_icon_file()
    icon_dest = None

    sanitized_stem = sanitize_name(appbox_path.stem)

    if icon_file:
        icon_dest = icons_dir / f"{sanitized_stem}{icon_file.suffix}"
        icons_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(icon_file, icon_dest)

    new_desktop_path = apps_dir / f"{sanitized_stem}.desktop"
    apps_dir.mkdir(parents=True, exist_ok=True)
    with open(desktop_file, "r") as f:
        content = f.read()

    lines = []
    for line in content.splitlines():
        if line.startswith("Exec="):
            lines.append(f"Exec={str(appbox_path)}")
        elif line.startswith("TryExec="):
            lines.append(f"TryExec={str(appbox_path)}")
        elif line.startswith("Icon=") and icon_file:
            lines.append(f"Icon={str(icon_dest)}")
        else:
            lines.append(line)
    content = "\n".join(lines)

    with open(new_desktop_path, "w") as f:
        f.write(content)

    logging.info(f"Integrated {appbox_path.name} as {new_desktop_path.name}")
    shutil.rmtree(extract_dir / "squashfs-root", ignore_errors=True)

def remove_integration(appbox_path: Path):
    appbox_name = sanitize_name(appbox_path.stem)
    for file in apps_dir.glob("*.desktop"):
        try:
            with open(file, "r", encoding="utf-8", errors="ignore") as f:
                if str(appbox_path) in f.read():
                    file.unlink()
                    logging.info(f"Removed desktop entry {file.name}")
        except Exception as e:
            logging.error(f"Failed to read {file}: {e}")

    for file in icons_dir.iterdir():
        if file.stem == appbox_name:
            file.unlink()
            logging.info(f"Removed icon {file.name}")

class AppBoxHandler(FileSystemEventHandler):
    @staticmethod
    def wait_for_file_complete(path: Path, timeout=10, interval=0.2):
        """Wait until the file size stops changing."""
        start_time = time.time()
        last_size = -1

        while time.time() - start_time < timeout:
            try:
                current_size = path.stat().st_size
                if current_size == last_size:
                    return True
                last_size = current_size
            except FileNotFoundError:
                pass
            time.sleep(interval)
        return False

    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".AppBox"):
            return

        appbox_path = Path(event.src_path)

        if not self.wait_for_file_complete(appbox_path):
            logging.warning(f"File not ready after timeout: {appbox_path}")
            return

        integrate_appbox(appbox_path)

def main():
    logging.info("Starting nx-apphubd")
    observer = Observer()
    observer.schedule(AppBoxHandler(), path=str(watch_dir), recursive=False)
    observer.start()

    for appbox in watch_dir.glob("*.AppBox"):
        integrate_appbox(appbox)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    logging.info("Stopping nx-apphubd")

if __name__ == "__main__":
    main()

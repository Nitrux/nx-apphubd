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

from pathlib import Path
import shutil
import subprocess
import time
import logging
from logging.handlers import RotatingFileHandler
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


appbox_dir = Path.home() / ".local/bin/nx-apphub"
applications_dir = Path.home() / ".local/share/applications"
icons_dir = Path.home() / ".local/share/icons"
log_file = Path.home() / ".nx-apphubd.log"


log_handler = RotatingFileHandler(
    filename=log_file,
    maxBytes=1_000_000,
    backupCount=3
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[log_handler]
)

class AppBoxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".AppBox"):
            return
        integrate_appbox(Path(event.src_path))

    def on_deleted(self, event):
        if event.is_directory or not event.src_path.endswith(".AppBox"):
            return
        remove_appbox_integration(Path(event.src_path))


def notify_user(app_name):
    subprocess.run([
        "notify-send",
        "-a", "nx-apphubd",
        "-u", "normal",
        "--action=Understood",
        "-i", "dialog-information",
        "AppBox Integrated",
        f"{app_name} has been added to your applications menu."
    ])


def integrate_appbox(appbox_path):
    output_desktop = applications_dir / f"{appbox_path.stem}.desktop"

    if output_desktop.exists():
        with open(output_desktop, "r") as f:
            if any("X-AppBox-Integrated=true" in line for line in f):
                return

    extract_dir = Path("/tmp") / f"{appbox_path.stem}-extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)

    subprocess.run([str(appbox_path), "--appimage-extract"], cwd="/tmp", check=True)
    extracted = Path("/tmp/squashfs-root")

    desktop_files = list(extracted.rglob("*.desktop"))
    if not desktop_files:
        return
    desktop_file = desktop_files[0]

    with open(desktop_file, "r") as f:
        lines = f.readlines()

    updated_lines = []
    for line in lines:
        if line.startswith("Exec="):
            continue
        elif line.startswith("Icon="):
            icon_value = line.strip().split("=", 1)[-1]
            icon_file = next(extracted.rglob(f"{icon_value}.*"), None)
            if icon_file:
                icon_dest = icons_dir / icon_file.name
                icon_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(icon_file, icon_dest)
                updated_lines.append(f"Icon={icon_dest}\n")
            else:
                updated_lines.append(line)
        else:
            updated_lines.append(line)

    firejail_options = [
        "--env=DESKTOPINTEGRATION=appimaged",
        "--private",
        "--appimage",
        f"--apparmor={appbox_path.stem}"
    ]
    exec_line = f"Exec=firejail {' '.join(firejail_options)} {str(appbox_path)} %u\n"

    updated_lines.append(exec_line)
    updated_lines.append("X-AppBox-Integrated=true\n")

    with open(output_desktop, "w") as f:
        f.writelines(updated_lines)

    logging.info(f"Integrated AppBox: {appbox_path.name}")
    notify_user(appbox_path.name)

def remove_appbox_integration(appbox_path):
    desktop_file = applications_dir / f"{appbox_path.stem}.desktop"
    if desktop_file.exists():
        desktop_file.unlink()

    for icon_file in icons_dir.glob(f"{appbox_path.stem}.*"):
        icon_file.unlink()

    logging.info(f"Removed AppBox integration: {appbox_path.name}")


def main():
    applications_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(AppBoxHandler(), path=str(appbox_dir), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

main()

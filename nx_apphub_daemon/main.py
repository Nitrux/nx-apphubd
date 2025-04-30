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


# -- Set integration directories.

watch_dir = Path.home() / ".local/bin/nx-apphub"
extract_dir = Path.home() / ".cache/nx-apphubd"
apps_dir = Path.home() / ".local/share/applications"
icons_dir = Path.home() / ".local/share/icons"


# -- Use a log file.

log_file = Path.home() / ".nx-apphubd.log"

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def sanitize_name(name: str) -> str:
    return re.sub(r'[:+~]', '-', name)


def get_base_app_name(filename_stem: str) -> str:
    """
    Extract the base application name from an AppBox filename.
    """
    filename_stem = re.sub(r'-[^-]+$', '', filename_stem)

    base_name = filename_stem.split('-')[0]

    parts = filename_stem.split('-')
    if len(parts) >= 2 and not re.search(r'\d', parts[1]):
        base_name = '-'.join(parts[:2])

    return base_name


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


def wait_until_file_ready(path: Path, timeout=10, interval=0.2) -> bool:
    """Wait until the file is no longer changing (and not locked)."""
    start_time = time.time()
    last_size = -1

    while time.time() - start_time < timeout:
        try:
            current_size = path.stat().st_size
            if current_size == last_size and os.access(path, os.X_OK):
                return True
            last_size = current_size
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(interval)

    return False


def integrate_appbox(appbox_path: Path):
    extract_dir.mkdir(parents=True, exist_ok=True)

    if not wait_until_file_ready(appbox_path):
        logging.warning(f"AppBox not ready after timeout: {appbox_path}")
        return

    if not os.access(appbox_path, os.X_OK):
        logging.warning(f"{appbox_path.name} is not executable; setting mode 755")
        appbox_path.chmod(0o755)

    if not appbox_path.is_file():
        logging.error(f"{appbox_path} is not a valid file")
        return

    sanitized_stem = get_base_app_name(appbox_path.stem)
    desktop_file_path = apps_dir / f"{sanitize_name(sanitized_stem)}.desktop"

    # --- Skip integration if already integrated.

    if desktop_file_path.exists():
        logging.info(f"AppBox {appbox_path.name} already integrated as {desktop_file_path.name}")
        return

    extract_appbox(appbox_path, quiet=True)

    desktop_file = find_desktop_file()
    if not desktop_file:
        logging.warning(f"No desktop file found in {appbox_path}")
        shutil.rmtree(extract_dir / "squashfs-root", ignore_errors=True)
        return

    icon_file = find_icon_file()
    icon_dest = None

    if icon_file:
        icon_dest = icons_dir / f"{sanitize_name(sanitized_stem)}{icon_file.suffix}"
        icons_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(icon_file, icon_dest)

    new_desktop_path = apps_dir / f"{sanitize_name(sanitized_stem)}.desktop"
    apps_dir.mkdir(parents=True, exist_ok=True)

    with open(desktop_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    lines = []
    is_cli_app = False

    for line in content.splitlines():
        if line.startswith("Exec="):
            lines.append(f"Exec={str(appbox_path)}")
        elif line.startswith("TryExec="):
            lines.append(f"TryExec={str(appbox_path)}")
        elif line.startswith("Icon=") and icon_file:
            lines.append(f"Icon={str(icon_dest)}")
        elif line.startswith("NoDisplay=true"):
            is_cli_app = True
            lines.append("NoDisplay=true")
        else:
            lines.append(line)

    content = "\n".join(lines)

    with open(new_desktop_path, "w", encoding="utf-8") as f:
        f.write(content)

    logging.info(f"Integrated {appbox_path.name} as {new_desktop_path.name}")

    # --- Handle CLI application (create ZSH alias).

    if is_cli_app:
        alias_command = f"alias {sanitized_stem}='{str(appbox_path)}'"
        zsh_alias_file = Path.home() / ".zshrc"

        if zsh_alias_file.exists():
            with open(zsh_alias_file, "r", encoding="utf-8", errors="ignore") as zshrc:
                lines = [line.rstrip() for line in zshrc.readlines()]
        else:
            lines = []

        existing_alias_block = f"# Alias for {sanitized_stem}\n{alias_command}"

        # -- Check if the alias already exists.

        if any(alias_command in line for line in lines):
            logging.info(f"ZSH alias for {sanitized_stem} already exists.")
        else:
            with open(zsh_alias_file, "a", encoding="utf-8") as zshrc:
                if lines and lines[-1] != "":
                    zshrc.write("\n")
                zshrc.write(f"# Alias for {sanitized_stem}\n{alias_command}\n")
            logging.info(f"Added ZSH alias for CLI application: {sanitized_stem}")

    shutil.rmtree(extract_dir / "squashfs-root", ignore_errors=True)


def remove_integration(appbox_path: Path):
    appbox_name = sanitize_name(get_base_app_name(appbox_path.stem))

    for file in apps_dir.glob("*.desktop"):
        try:
            with open(file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                if str(appbox_path) in content:
                    file.unlink()
                    logging.info(f"Removed desktop entry {file.name}")

                    match = re.search(r'^Icon=(.+)$', content, re.MULTILINE)
                    if match:
                        icon_path = Path(match.group(1).strip())
                        if icon_path.exists() and icon_path.is_file():
                            icon_path.unlink()
                            logging.info(f"Removed icon {icon_path.name}")

        except Exception as e:
            logging.error(f"Failed to process {file}: {e}")

    # --- Always attempt to remove ZSH alias and its comment block.

    zsh_alias_file = Path.home() / ".zshrc"
    if zsh_alias_file.exists():
        try:
            with open(zsh_alias_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            new_lines = []
            skip_next = False
            removed = False

            for line in lines:
                if skip_next:
                    skip_next = False
                    continue

                if line.strip() == f"# Alias for {appbox_name}":
                    skip_next = True
                    removed = True
                    continue

                new_lines.append(line)

            if removed:
                with open(zsh_alias_file, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                logging.info(f"Removed ZSH alias for CLI app: {appbox_name}")

        except Exception as e:
            logging.error(f"Failed to clean ZSH alias: {e}")


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

    def on_deleted(self, event):
        if event.is_directory or not event.src_path.endswith(".AppBox"):
            return
        remove_integration(Path(event.src_path))


def clean_stale_integrations():
    """Remove stale desktop entries and icons if the corresponding AppBox is missing."""

    existing_appboxes = {str(p) for p in watch_dir.glob("*.AppBox")}

    for desktop_file in apps_dir.glob("*.desktop"):
        try:
            with open(desktop_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            exec_lines = [line for line in content.splitlines() if line.startswith("Exec=")]

            for line in exec_lines:
                exec_cmd = line.replace("Exec=", "").strip().strip('"')
                exec_path = Path(exec_cmd)

                if exec_path.is_absolute() and str(exec_path).startswith(str(watch_dir)):
                    if str(exec_path) not in existing_appboxes:
                        desktop_file.unlink()
                        logging.info(f"Removed stale desktop entry {desktop_file.name}")
                        break

        except Exception as e:
            logging.error(f"Failed to process desktop file {desktop_file}: {e}")

    for icon_file in icons_dir.iterdir():
        if not icon_file.is_file():
            continue

        sanitized_name = icon_file.stem
        related_desktop = apps_dir / f"{sanitized_name}.desktop"

        if not related_desktop.exists():
            try:
                icon_file.unlink()
                logging.info(f"Removed stale icon {icon_file.name}")
            except Exception as e:
                logging.error(f"Failed to remove icon {icon_file}: {e}")
    
    clean_stale_aliases()


def clean_stale_aliases():
    """Remove stale ZSH aliases that point to missing AppBoxes only."""

    zshrc_path = Path.home() / ".zshrc"
    if not zshrc_path.exists():
        return

    existing_appboxes = {str(p) for p in watch_dir.glob("*.AppBox")}

    with open(zshrc_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    new_lines = []
    modified = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("alias ") and '=' in stripped:
            alias_name, alias_value = stripped[6:].split("=", 1)
            alias_value = alias_value.strip().strip("'\"")
            alias_path = Path(alias_value)

            # Only process aliases pointing INSIDE the AppBox watch directory
            if alias_path.is_absolute() and str(alias_path).startswith(str(watch_dir)):
                if str(alias_path) not in existing_appboxes:
                    logging.info(f"Removed stale ZSH alias: {alias_name}")
                    modified = True
                    continue

        new_lines.append(line)

    if modified:
        zshrc_path.write_text("".join(new_lines))


def scan_existing_appboxes():
    """Scan existing AppBoxes and integrate missing ones."""
    for appbox in watch_dir.glob("*.AppBox"):
        base_name = get_base_app_name(appbox.stem)
        expected_desktop = apps_dir / f"{base_name}.desktop"
        if not expected_desktop.exists():
            logging.info(f"Found unintegrated AppBox: {appbox.name}, integrating...")
            integrate_appbox(appbox)


def main():
    logging.info("Starting nx-apphubd")

    clean_stale_integrations()
    scan_existing_appboxes()

    observer = Observer()
    handler = AppBoxHandler()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()
    logging.info("Stopping nx-apphubd")


if __name__ == "__main__":
    main()

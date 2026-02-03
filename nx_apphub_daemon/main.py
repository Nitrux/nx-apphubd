#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright <2025> <Uri Herrera <uri_herrera@nxos.org>>

import os
import shutil
import subprocess
import time
import logging
import re
import configparser
import threading
import errno
import yaml
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# <---
# --->
# -- Define Directories using XDG standards.

home = Path.home()
xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))

watch_dir = home / ".local/bin/nx-apphub"
extract_dir = home / ".cache/nx-apphubd"
apps_dir = xdg_data_home / "applications"
icons_dir = xdg_data_home / "icons" / "nx-apphub"
config_dir = xdg_config_home / "nx-apphub"
alias_file = config_dir / "aliases.zsh"

file_lock = threading.Lock()


# -- Use a log file.

log_file = home / ".nx-apphubd.log"

logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s"
)


def sanitize_name(name: str) -> str:
    """
    Sanitize application name by replacing problematic characters with hyphens.

    Args:
        name: The application name to sanitize.

    Returns:
        The sanitized name with ':+~' characters replaced by '-'.
    """
    return re.sub(r'[:+~]', '-', name)


def get_base_app_name(filename_stem: str) -> str:
    """
    Extract the base application name from an AppBox filename.

    Args:
        filename_stem: The filename without extension.

    Returns:
        The base application name extracted from the filename.
    """
    filename_stem = re.sub(r'-[^-]+$', '', filename_stem)
    base_name = filename_stem.split('-')[0]

    parts = filename_stem.split('-')
    if len(parts) >= 2 and not re.search(r'\d', parts[1]):
        base_name = '-'.join(parts[:2])

    return base_name


def is_elf_binary(path: Path) -> bool:
    """
    Check for ELF magic bytes to ensure file is executable binary.

    Args:
        path: Path to the file to check.

    Returns:
        True if the file is a valid ELF binary, False otherwise.
    """
    try:
        with open(path, "rb") as f:
            return f.read(4) == b'\x7fELF'
    except Exception as e:
        logging.error(f"Failed to check file signature for {path}: {e}")
        return False


def is_valid_appbox(path: Path) -> tuple[bool, str]:
    """
    Validate that a file is a genuine AppBox and not a renamed AppImage.

    By design, nx-apphubd is intended to integrate AppBoxes, not AppImages.
    This function validates that the AppBox file has a corresponding YAML
    definition and build marker in the nx-apphub-cli directory structure,
    which proves it was built through the proper nx-apphub-cli workflow.

    AppBoxes are built from YAMLs in:
    ~/.local/share/nx-apphub-cli/<repo>/apps/<arch>/<appname>/app.yml

    Build markers are created at:
    ~/.local/share/nx-apphub-cli/.built/{name}-{version}-{arch}

    The YAML file contains buildinfo with name, version, and arch that must
    match the AppBox filename format: {name}-{version}-{arch}.AppBox

    Args:
        path: Path to the file to validate.

    Returns:
        A tuple of (is_valid, reason) where is_valid is True if the file
        passes validation, and reason provides details if validation fails.
        Returns False for AppImages or files without YAML definitions or build markers.
    """
    if not path.exists():
        return False, "File does not exist"

    if not is_elf_binary(path):
        return False, "Not a valid ELF binary"

    nx_apphub_cli_dir = xdg_data_home / "nx-apphub-cli"

    if not nx_apphub_cli_dir.exists():
        logging.warning(
            f"nx-apphub-cli directory not found at {nx_apphub_cli_dir}. "
            f"Cannot validate {path.name} as a genuine AppBox."
        )
        return False, "No nx-apphub-cli directory found - AppBox definitions missing"

    filename_stem = path.stem

    arch_mapping = {
        'x86_64': 'amd64',
        'aarch64': 'arm64'
    }

    parts = filename_stem.split('-')
    if len(parts) < 3:
        return False, "Invalid AppBox filename format"

    file_arch = parts[-1]

    found_yaml = False
    found_yaml_path = None

    try:
        for yaml_file in nx_apphub_cli_dir.rglob("app.yml"):
            if not yaml_file.is_file():
                continue

            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    yaml_data = yaml.safe_load(f)

                if not yaml_data or 'buildinfo' not in yaml_data:
                    continue

                buildinfo = yaml_data['buildinfo']
                yaml_name = buildinfo.get('name', '')

                if not yaml_name:
                    continue

                yaml_arch = None
                if 'distrorepo' in buildinfo and buildinfo['distrorepo']:
                    yaml_arch = buildinfo['distrorepo'][0].get('arch', '')

                reverse_arch_mapping = {v: k for k, v in arch_mapping.items()}
                expected_arch = reverse_arch_mapping.get(yaml_arch, yaml_arch)

                # Check if the filename starts with the app name and has matching arch
                # This allows different versions to be validated against the same YAML
                if filename_stem.startswith(f"{yaml_name}-") and file_arch == expected_arch:
                    found_yaml = True
                    found_yaml_path = yaml_file
                    logging.info(
                        f"Found matching YAML definition for {path.name}: {yaml_file}"
                    )
                    break

            except yaml.YAMLError as e:
                logging.debug(f"Failed to parse YAML {yaml_file}: {e}")
                continue
            except Exception as e:
                logging.debug(f"Error checking YAML {yaml_file}: {e}")
                continue

    except Exception as e:
        logging.error(f"Error searching for YAML definition for {path.name}: {e}")
        return False, f"Error validating AppBox: {e}"

    if not found_yaml:
        logging.error(
            f"No YAML definition found for {path.name}. "
            "This file may be a renamed AppImage or was not built through nx-apphub-cli. "
            "nx-apphubd is designed to integrate AppBoxes built from YAML definitions. "
            "Integration refused."
        )
        return False, "No corresponding YAML definition found - not a valid AppBox"

    # Check for build marker - proves the AppBox was built by nx-apphub-cli
    build_markers_dir = nx_apphub_cli_dir / ".built"
    build_marker_file = build_markers_dir / filename_stem

    if not build_marker_file.exists():
        logging.error(
            f"Build marker not found for {path.name}. "
            "This AppBox was not built through nx-apphub-cli. "
            f"Expected marker at: {build_marker_file}. "
            "Integration refused."
        )
        return False, "No build marker found - AppBox not built by nx-apphub-cli"

    return True, "Valid AppBox"


def send_notification(summary: str, body: str, icon: Path = None):
    """
    Send a desktop notification using notify-send.

    Args:
        summary: Notification title.
        body: Notification message body.
        icon: Optional path to icon file for the notification.
    """
    if not shutil.which("notify-send"):
        return

    cmd = [
        "notify-send",
        "-a", "NX AppHub",
        "-u", "normal",
    ]

    if icon and icon.exists():
        cmd.extend(["-i", str(icon)])
    else:
        # Fallback icon
        cmd.extend(["-i", "application-x-executable"])

    cmd.append(summary)
    cmd.append(body)

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL,
            check=False
        )
    except Exception as e:
        logging.error(f"Failed to send notification: {e}")


def update_alias_file(alias_name: str, appbox_path: Path, remove=False):
    """
    Update the dedicated aliases.zsh file in a thread-safe manner.

    Args:
        alias_name: The alias name to add or remove.
        appbox_path: Path to the AppBox executable.
        remove: If True, remove the alias; otherwise add it.
    """
    with file_lock:
        config_dir.mkdir(parents=True, exist_ok=True)

        lines = []
        if alias_file.exists():
            with open(alias_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

        alias_cmd = f"alias {alias_name}='{str(appbox_path)}'\n"
        header = f"# Alias for {alias_name}\n"

        new_lines = []
        skip = False

        for i, line in enumerate(lines):
            if skip:
                skip = False
                continue

            if line.strip() == header.strip():
                if i + 1 < len(lines) and lines[i+1].strip().startswith(f"alias {alias_name}="):
                    skip = True
                    continue

            new_lines.append(line)

        if not remove:
            if new_lines and new_lines[-1].strip() != "":
                new_lines.append("\n")
            new_lines.append(header)
            new_lines.append(alias_cmd)

        with open(alias_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        logging.info(f"{'Removed' if remove else 'Added'} alias for {alias_name} in {alias_file}")


def wait_until_file_ready(path: Path, timeout=90, interval=0.2) -> bool:
    """
    Wait until the file is no longer changing (and not locked).

    Args:
        path: Path to the file to monitor.
        timeout: Maximum time to wait in seconds (default: 90).
        interval: Time between checks in seconds (default: 0.2).

    Returns:
        True if file is ready and accessible, False if timeout occurs.
    """
    start_time = time.time()
    last_size = -1

    while time.time() - start_time < timeout:
        try:
            current_size = path.stat().st_size
            if current_size == last_size and os.access(path, os.R_OK):
                return True
            last_size = current_size
        except (FileNotFoundError, PermissionError):
            pass
        time.sleep(interval)

    return False


def integrate_appbox(appbox_path: Path):
    """
    Main integration logic for AppBox files. Designed to run in a separate thread.

    Extracts the AppBox, parses the desktop file, installs icons, and creates
    desktop integration. Handles CLI applications by creating shell aliases.

    Args:
        appbox_path: Path to the AppBox file to integrate.
    """
    extract_dir.mkdir(parents=True, exist_ok=True)

    if not wait_until_file_ready(appbox_path):
        logging.warning(f"AppBox not ready after timeout: {appbox_path}")
        return

    is_valid, validation_reason = is_valid_appbox(appbox_path)
    if not is_valid:
        logging.error(
            f"AppBox validation failed for {appbox_path.name}: {validation_reason}. "
            "Skipping integration."
        )
        send_notification(
            "Integration Failed",
            f"{appbox_path.name} could not be integrated. {validation_reason}."
        )
        return

    if not os.access(appbox_path, os.X_OK):
        logging.warning(f"{appbox_path.name} is not executable; setting mode 755")
        appbox_path.chmod(0o755)

    raw_base_name = get_base_app_name(appbox_path.stem)
    sanitized_name = sanitize_name(raw_base_name)

    desktop_file_path = apps_dir / f"{sanitized_name}.desktop"

    if desktop_file_path.exists():
        logging.info(f"AppBox {appbox_path.name} already integrated as {desktop_file_path.name}")
        return

    specific_extract_dir = extract_dir / f"{sanitize_name(appbox_path.stem)}"
    specific_extract_dir.mkdir(parents=True, exist_ok=True)

    logging.info(f"Extracting {appbox_path.name}...")

    max_retries = 5
    extraction_success = False

    for attempt in range(max_retries):
        try:
            subprocess.run(
                [str(appbox_path), "--appimage-extract"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                cwd=specific_extract_dir
            )
            extraction_success = True
            break
        except OSError as e:
            if e.errno == errno.ETXTBSY:
                logging.warning(f"File {appbox_path.name} is busy (ETXTBSY), retrying extraction in 1s (Attempt {attempt + 1}/{max_retries})...")
                time.sleep(1.0)
            else:
                logging.error(f"OSError during extraction of {appbox_path}: {e}")
                break
        except subprocess.CalledProcessError as e:
            logging.error(f"Extraction command failed for {appbox_path}: {e}")
            break

    if not extraction_success:
        logging.error(f"Failed to extract {appbox_path} after retries.")
        shutil.rmtree(specific_extract_dir, ignore_errors=True)
        return

    squashfs_root = specific_extract_dir / "squashfs-root"

    extracted_desktop_file = None
    for root, _, files in os.walk(squashfs_root):
        for file in files:
            if file.endswith(".desktop"):
                extracted_desktop_file = Path(root) / file
                break
        if extracted_desktop_file:
            break

    if not extracted_desktop_file:
        logging.warning(f"No desktop file found in {appbox_path}")
        shutil.rmtree(specific_extract_dir, ignore_errors=True)
        return

    icon_file = None
    for root, _, files in os.walk(squashfs_root):
        for file in files:
            if file.lower().endswith((".png", ".svg", ".xpm")):
                icon_file = Path(root) / file
                break
        if icon_file:
            break

    icon_dest = None
    if icon_file:
        icon_dest = icons_dir / f"{sanitized_name}{icon_file.suffix}"
        icons_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(icon_file, icon_dest)

    parser = configparser.ConfigParser()
    parser.optionxform = str

    try:
        parser.read(extracted_desktop_file, encoding='utf-8')
    except configparser.Error as e:
        logging.error(f"Error parsing desktop file for {appbox_path}: {e}")
        shutil.rmtree(specific_extract_dir, ignore_errors=True)
        return

    if 'Desktop Entry' not in parser:
        logging.warning(f"Invalid desktop file (no [Desktop Entry]): {extracted_desktop_file}")
        shutil.rmtree(specific_extract_dir, ignore_errors=True)
        return

    parser['Desktop Entry']['Exec'] = str(appbox_path)
    parser['Desktop Entry']['TryExec'] = str(appbox_path)
    if icon_dest:
        parser['Desktop Entry']['Icon'] = str(icon_dest)

    is_cli_app = parser['Desktop Entry'].get('NoDisplay', 'false').lower() == 'true'

    apps_dir.mkdir(parents=True, exist_ok=True)
    with open(desktop_file_path, "w", encoding="utf-8") as f:
        parser.write(f, space_around_delimiters=False)

    logging.info(f"Integrated {appbox_path.name} as {desktop_file_path.name}")

    send_notification(
        "Application Installed",
        f"{sanitized_name.title()} has been successfully integrated with the system.",
        icon=icon_dest
    )

    if is_cli_app:
        update_alias_file(sanitized_name, appbox_path, remove=False)

    shutil.rmtree(specific_extract_dir, ignore_errors=True)


def remove_integration(appbox_path: Path):
    """
    Remove all desktop integration for a specific AppBox.

    Removes desktop files, icons, and shell aliases associated with the AppBox.

    Args:
        appbox_path: Path to the AppBox file being removed.
    """
    appbox_name = sanitize_name(get_base_app_name(appbox_path.stem))
    removed_anything = False

    for file in apps_dir.glob("*.desktop"):
        try:
            if appbox_name in file.name: 
                parser = configparser.ConfigParser()
                parser.optionxform = str
                parser.read(file)

                if 'Desktop Entry' in parser:
                    exec_path = parser['Desktop Entry'].get('Exec', '')
                    if str(appbox_path) in exec_path:
                        file.unlink()
                        logging.info(f"Removed desktop entry {file.name}")
                        removed_anything = True

                        icon_path_str = parser['Desktop Entry'].get('Icon', '')
                        if icon_path_str:
                            icon_path = Path(icon_path_str)
                            if icon_path.exists() and icons_dir in icon_path.parents:
                                icon_path.unlink()
                                logging.info(f"Removed icon {icon_path.name}")
        except Exception as e:
            logging.error(f"Failed to process removal for {file}: {e}")

    if removed_anything:
        update_alias_file(appbox_name, appbox_path, remove=True)
        send_notification(
            "Application Removed",
            f"Integration for {appbox_name.title()} has been removed from the system."
    )


class AppBoxHandler(FileSystemEventHandler):
    """Handle creation and deletion events for AppBox files.

    This class watches the configured directory for `.AppBox` files.
    When a file is created, it triggers integration in a background thread.
    When a file is deleted, it removes the corresponding integration.
    """

    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".AppBox"):
            return

        appbox_path = Path(event.src_path)

        t = threading.Thread(
            target=integrate_appbox, 
            args=(appbox_path,), 
            name=f"Integrator-{appbox_path.name}"
        )
        t.start()

    def on_deleted(self, event):
        if event.is_directory or not event.src_path.endswith(".AppBox"):
            return

        remove_integration(Path(event.src_path))


def clean_stale_integrations():
    """
    Remove stale desktop entries, icons, and aliases if the AppBox is missing.

    Scans all desktop entries and aliases, removing those that reference
    non-existent AppBox files in the watched directory.
    """
    existing_appboxes = {str(p) for p in watch_dir.glob("*.AppBox")}

    for desktop_file in apps_dir.glob("*.desktop"):
        try:
            parser = configparser.ConfigParser()
            parser.optionxform = str
            parser.read(desktop_file)

            if 'Desktop Entry' in parser:
                exec_cmd = parser['Desktop Entry'].get('Exec', '').strip("'\"")
                if str(watch_dir) in exec_cmd:
                    possible_path = exec_cmd.split()[0].strip("'\"")
                    if possible_path not in existing_appboxes:
                        desktop_file.unlink()
                        logging.info(f"Removed stale desktop entry {desktop_file.name}")
        except Exception:
            pass

    if alias_file.exists():
        with file_lock:
            with open(alias_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            new_lines = []
            skip = False
            modified = False

            for i, line in enumerate(lines):
                if skip:
                    skip = False
                    continue

                if line.strip().startswith("# Alias for"):
                    if i + 1 < len(lines) and "alias " in lines[i+1]:
                        cmd_line = lines[i+1]
                        match = re.search(r"='([^']+)'", cmd_line)
                        if match:
                            path_str = match.group(1)
                            if str(watch_dir) in path_str and path_str not in existing_appboxes:
                                logging.info(f"Removing stale alias from line {i+1}")
                                modified = True
                                skip = True
                                continue

                new_lines.append(line)

            if modified:
                with open(alias_file, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)


def scan_existing_appboxes():
    """
    Scan existing AppBoxes and integrate missing ones.

    Called at daemon startup to ensure all AppBox files in the watched
    directory are properly integrated with the desktop environment.
    """
    for appbox in watch_dir.glob("*.AppBox"):
        base_name = sanitize_name(get_base_app_name(appbox.stem))
        expected_desktop = apps_dir / f"{base_name}.desktop"

        if not expected_desktop.exists():
            logging.info(f"Found unintegrated AppBox: {appbox.name}, integrating...")
            threading.Thread(
                target=integrate_appbox,
                args=(appbox,),
                name=f"Integrator-{appbox.name}"
            ).start()


def ensure_zsh_source():
    """
    Ensure the alias file is sourced in .zshrc.

    Adds a source line to the user's .zshrc file to load AppBox shell aliases.
    Creates .zshrc if it doesn't exist.
    """
    zshrc = home / ".zshrc"

    try:
        relative_path = alias_file.relative_to(home)
        source_line = f'source "$HOME/{relative_path}"'
    except ValueError:
        source_line = f'source "{alias_file}"'

    try:
        if not zshrc.exists():
            zshrc.touch()

        current_content = zshrc.read_text(encoding="utf-8", errors="ignore")

        if source_line in current_content or str(alias_file) in current_content:
            return

        logging.info(f"Adding source line to {zshrc}")

        with open(zshrc, "a", encoding="utf-8") as f:
            if current_content and not current_content.endswith("\n"):
                f.write("\n")
            f.write(f"\n# Added by nx-apphubd\n{source_line}\n")

    except Exception as e:
        logging.error(f"Failed to update .zshrc: {e}")


def main():
    """
    Main entry point for the nx-apphubd daemon.

    Initializes directories, cleans stale integrations, scans existing AppBoxes,
    sets up file system monitoring, and runs the daemon event loop.
    """
    logging.info("Starting nx-apphubd")

    for path in [watch_dir, extract_dir, apps_dir, icons_dir, config_dir]:
        path.mkdir(parents=True, exist_ok=True)

    if not alias_file.exists():
        alias_file.touch()

    clean_stale_integrations()
    scan_existing_appboxes()
    ensure_zsh_source()

    if alias_file.exists():
        logging.info(f"IMPORTANT: Please source {alias_file} in your .zshrc to enable CLI aliases.")

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

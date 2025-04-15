# NX AppHub Daemon | [![License](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)

<p align="center">
  <img width="128" height="128" src="https://raw.githubusercontent.com/Nitrux/luv-icon-theme/refs/heads/master/Luv/mimetypes/64/application-x-iso9660-appimage.svg">
</p>

# Introduction

NX AppHub Daemon is a daemon that integrates AppBoxes generated with NX Apphub CLI with the desktop.

> _⚠️ Important: NX AppHub Daemon primarily targets Nitrux OS, and using this utility in other distributions may or may not work. To request formal support for other distributions, open a PR regarding this use case._

For more in-depth information about NX AppHub CLI, please see the [Wiki](https://github.com/Nitrux/nx-apphub/wiki).

### Requirements

- Nitrux 4.0.0 and newer.
    - _♦ Information: To use `nx-apphubd` in previous versions of Nitrux use a container._
- Python 3.10 and newer.

NX AppHub Daemon requires the following utilities to function properly:

- appstream
- binutils
- file
- fuse3
- libfuse2t64

# Installation

To install NX AppHub Daemon we recommend using pipx.

### Single-user

```
pipx install git+https://github.com/Nitrux/nx-apphubd.git
```


### System-wide

```
pipx install --system-site-packages git+https://github.com/Nitrux/nx-apphubd.git
```

# Usage

To use NX AppHub Daemon, simply run it.


# Licensing

The license for this repository and its contents is **BSD-3-Clause**.

# Issues

If you find problems with the contents of this repository, please create an issue.

©2025 Nitrux Latinoamericana S.C.

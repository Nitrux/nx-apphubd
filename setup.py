#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright <2025> <Uri Herrera <uri_herrera@nxos.org>>

from setuptools import setup, find_packages


setup(
    name="nx-apphubd",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "watchdog",
    ],
    entry_points={
        'console_scripts': [
            'nx-apphubd = nx_apphub_daemon.main:main'
        ]
    },
    author="Your Name",
    description="AppBox desktop integration daemon for nx-apphub",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: POSIX :: Linux",
    ],
    python_requires='>=3.7',
)

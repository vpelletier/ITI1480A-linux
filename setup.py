#!/usr/bin/env python
# Copyright (C) 2010-2015  Vincent Pelletier <plr.vincent@gmail.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
from setuptools import setup, find_packages

setup(
    name='ITI1480A-linux',
    description='Tools for the ITI1480A usb protocol analyser',
    keywords='usb protocol analyser ITI1480A',
    version='3.0',
    author='Vincent Pelletier',
    author_email='plr.vincent@gmail.com',
    url='http://github.com/vpelletier/ITI1480A-linux',
    license='GPLv2+',
    platforms=['any'],
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'spt2hex=iti1480a.spt2hex:main',
            'iti1480a-capture=iti1480a.capture:main',
            'iti1480a-display=iti1480a.display:main',
        ],
    },
    classifiers=[
        'Intended Audience :: Information Technology',
        'License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)',
        'Operating System :: OS Independent',
    ],
    install_requires=[
        'libusb1',
        'ply',
    ],
)


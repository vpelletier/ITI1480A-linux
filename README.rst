User-space driver and tools for ITI1480A USB analyser.

Home: http://github.com/vpelletier/ITI1480A-linux

Disclaimer
==========

I am not affiliated with International Test Instruments in any way.
ITI kindly accepted that I start reverse-engineering their windows-only
software so I could use with Linux the I bought from them.
I had no privileged access to any documentation of any kind.

Dependencies
============

- Python_ 2.4+ (though mostly tested with 2.7 nowadays) or pypy_ for ~7 times
  faster parsing

- libusb_ 1.0

- python-libusb1_

- cycfx2prog_

- udev (should come from your distribution)

- ply_

Firmwares
---------

The ITI1480A analyser is composed of 2 programable chips:

- A Cypress FX2, to be programmed on first device enumeration after connection
  to analysis computer. Its firmware transmits commands send by analysis
  computer to the main analyser chip and streams capture data back.

  This chip controls the "Host power" led: if the firmware is properly loaded,
  this led must be on.

- An Altera Cyclone 2 FPGA, which produces data which is stored in capture
  files by listening to events from the link under test. Its firmware is
  loaded at the beginning of each capture session.

  This chip controls the "Link power" and "Link activity" leds.

Just for completeness, there is a small flash chip which merely contains
power-on vendor and device identifiers so an unprogrammed ITI1480A FX2 can
still be distinguished from any other device's FX2.

FX2
+++

A free software implementation of this firmware exists, so you have two
options:

- Either using the proprietary firmware from
  `1480A USB Protocol Analyzer Software`_, found under the name `ITI1480A.spt`
  in installation directory.

- Or provided Free Software firmware, available pre-built or from source. If
  you want to build it you need:

  - fx2lib_

  - sdcc_

Cyclone 2
+++++++++

No free software implementation exist (yet). Contributions welcome.

I the mean time, you need to use the proprietary implementation from
`1480A USB Protocol Analyzer Software`_, found under the name
`ulpitest.rbf` in installation directory.

`4.1.0b ultitest.rbf`_ (latest version as of this writing) has been released
stand-alone.

Installation
============

Notes: paths and udev group are suitable for at least Debian. You may need to
edit udev/ITI1480A.rules and adapt below paths.

::

  python setup.py install
  cp udev/ITI1480A.rules /etc/udev/rules.d/
  cp udev/ITI1480A.sh /lib/udev/
  wget -O /lib/firmware/ITI1480A.rbf http://www.internationaltestinstruments.com/Downloads/UlpiTest.rbf

- ITI's FX2 firmware::

    spt2hex /path/to/ITI1480A.spt
    cp ITI1480A_1.ihx /lib/firmware/ITI1480A.ihx

  spt2hex may generate several files (ITI1480A_0.ihx, ITI1480A_1.ihx, ...), use
  the highest-numbered one.

- Free Software FX2 firmware:

    To rebuild from source::

      FX2LIBDIR=/path_to/fx2lib/ make -C CY7C68013A

    To install::

      cp CY7C68013A/build/ITI1480A.ihx /lib/firmware/

To test installation, (re)plug your protocol analyser. If the "Host power" led
turns on within a few seconds, your FX2 firmware was successfully installed and
loaded, your analyser is ready to use.

Usage
=====

To start a capture::

  iti1480a-capture > captured.usb

Send signal SIGINT (^C) or SIGTERM to stop the capture, wait analyser to push
all data to host and exit.

Send signal SIGTSTP (^Z) to pause the analyser, SIGCONT (fg) to resume.

To get a human-friendly text dump of a previos capture::

  iti1480a-display -i captured.usb

To wath running capture without saving it::

  iti1480a-capture | iti1480a-display -f

To watch running capture and save it for later analysis::

  iti1480a-capture | iti1480a-display -ft captured.usb

By default, iti1480a-display hides a lot of verbose events, like NAK'ed and SOF
transactions, or EOP events. You can tweak its filtering using -q (quieter) and
-v (more verbose). Default verbosity level is 0, -q decrements it and -v
increments it. Verbosity levels go from -1 (most quiet) to 4 (most verbose).

Example outputs: https://github.com/vpelletier/ITI1480A-linux/tree/master/examples

Red timestamps mean that output is detected as being non-chronological. This
happens for implementation detail reasons, and is considered a bug
(`issue #4`_).

.. _Python: http://www.python.org/
.. _pypy: http://www.pypy.org/
.. _libusb: http://www.libusb.org/wiki/libusb-1.0
.. _python-libusb1: http://pypi.python.org/pypi/libusb1
.. _cycfx2prog: http://www.triplespark.net/elec/periph/USB-FX2/software/
.. _1480A USB Protocol Analyzer Software: http://www.internationaltestinstruments.com/
.. _4.1.0b ultitest.rbf: http://www.internationaltestinstruments.com/Downloads/UlpiTest.rbf
.. _ply: http://www.dabeaz.com/ply/
.. _fx2lib: https://github.com/djmuhlestein/fx2lib
.. _sdcc: http://sdcc.sourceforge.net
.. _issue #4: https://github.com/vpelletier/ITI1480A-linux/issues/4

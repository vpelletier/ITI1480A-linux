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
from iti1480a.parser import *
import signal
import sys
import errno
import select
import fcntl
import os

COLOR_GREEN = '\x1b[32m'
COLOR_STRONG_GREEN = '\x1b[1;32m'
COLOR_YELLOW = '\x1b[1;33m'
COLOR_RED = '\x1b[31m'
COLOR_STRONG_RED = '\x1b[1;31m'
COLOR_BLUE = '\x1b[1;34m'
COLOR_ORANGE = '\x1b[33m'

TOKEN_COLOR = {
    TOKEN_TYPE_OUT: COLOR_BLUE,
    TOKEN_TYPE_IN: COLOR_BLUE,
    TOKEN_TYPE_SETUP: COLOR_BLUE,
    TOKEN_TYPE_SSPLIT: COLOR_BLUE,
    TOKEN_TYPE_CSPLIT: COLOR_BLUE,

    TOKEN_TYPE_ACK: COLOR_STRONG_GREEN,
    TOKEN_TYPE_NAK: COLOR_RED,
    TOKEN_TYPE_STALL: COLOR_STRONG_RED,

    TOKEN_TYPE_NYET: COLOR_YELLOW,
    TOKEN_TYPE_PING: COLOR_YELLOW,

    TOKEN_TYPE_DATA0: COLOR_GREEN,
    TOKEN_TYPE_DATA1: COLOR_GREEN,
    TOKEN_TYPE_DATA2: COLOR_GREEN,
    TOKEN_TYPE_MDATA: COLOR_GREEN,

    TOKEN_TYPE_SOF: COLOR_ORANGE,
}

def hexdump(data):
    result = []
    append = result.append
    offset = 0
    while data:
        line = data[:16]
        half1 = line[:8]
        half2 = line[8:]
        data = data[16:]
        append(
            ' ' * 20
            + ('\x1b[33m%03x \x1b[0;36m' % offset)
            + ' '.join([
                '%02x' % ord(x) for x in half1
            ] + ['  '] * (8 - len(half1)))
            + '  '
            + ' '.join([
                '%02x' % ord(x) for x in half2
            ] + ['  '] * (8 - len(half2)))
            + ' '
            + ''.join(x if x.isalnum() or x == ' ' else '.' for x in line)
            + ' ' * (16 - len(line))
            + '\x1b[0m'
        )
        offset += 16
    return '\n'.join(result)

class HumanReadable(object):
    def __init__(self, write, error, verbosity):
        self._write = write
        self._error_write = error
        self._verbosity = verbosity
        self._sof_start = (None, None)
        self._sof_count = 0
        self._sof_major = None
        self._sof_minor = None
        self._last_tic = None
        noop = lambda _, __: None
        self._dispatch = {
            MESSAGE_RAW: (lambda _, x: x) if verbosity > -1 else noop,
            MESSAGE_RESET: self._reset if verbosity > -1 else noop,
            MESSAGE_TRANSACTION: self._transaction,
            MESSAGE_INCOMPLETE: (
                lambda x, y: self._transaction(x, y, incomplete=True)
            ) if verbosity > 0 else noop,
            MESSAGE_TRANSACTION_ERROR: self._error,
            MESSAGE_LS_EOP: self._ls_eop if verbosity > 2 else noop,
            MESSAGE_FS_EOP: self._fs_eop if verbosity > 2 else noop,
            MESSAGE_FS_TO_CHIRP: self._fs_to_chirp,
        }

    def _print(self, tic, printable, write):
        if tic is None:
            time = '?'
        else:
            time = tic_to_time(tic)
            if tic < self._last_tic:
                time = '\x1b[31m' + time + '\x1b[0m'
            else:
                self._last_tic = tic
        write(time + ' ' + printable + '\n')

    def _printSOFCount(self):
        sof_tic, sof_start_frame = self._sof_start
        self._print(
            sof_tic,
            TOKEN_COLOR[TOKEN_TYPE_SOF] + 'SOF (%i) %s -> %i.%i\x1b[0m' % (
                self._sof_count,
                sof_start_frame,
                self._sof_major,
                self._sof_minor,
            ), self._write)
        self._sof_count = 0

    def push(self, tic, message_type, data):
        printable = self._dispatch[message_type](tic, data)
        if printable is not None:
            if self._sof_count:
                self._printSOFCount()
            self._print(tic, printable, self._write)

    def _error(self, tic, data):
        self._print(tic, '\x1b[41m%r\x1b[0m' % (data, ), self._error_write)

    @staticmethod
    def _reset(_, data):
        return '\x1b[35mDevice reset (%s)\x1b[0m' % (short_tic_to_time(data), )

    @staticmethod
    def _fs_to_chirp(_, data):
        return '\x1b[35mFS to chirp trigger (%s)\x1b[0m' % (
            short_tic_to_time(data),
        )

    @staticmethod
    def _ls_eop(_, data):
        return '\x1b[33mLS EOP (%s)\x1b[0m' % (short_tic_to_time(data), )

    @staticmethod
    def _fs_eop(_, data):
        return '\x1b[33mFS EOP (%s)\x1b[0m' % (short_tic_to_time(data), )

    def _transaction(self, tic, data, incomplete=False):
        if data[0][0] == TOKEN_TYPE_SOF:
            sof_data, = data
            try:
                decoded = decode(sof_data)
            except IndexError:
                return
            if not decoded['crc_error']:
                if self._verbosity < 2:
                    return
                frame = decoded['frame']
                if frame == self._sof_major:
                    self._sof_minor += 1
                else:
                    self._sof_major = frame
                    self._sof_minor = 0
                if self._verbosity <= 3:
                    if not self._sof_count:
                        self._sof_start = (
                            tic,
                            '%i.%i' % (frame, self._sof_minor),
                        )
                    self._sof_count += 1
                    return
        if self._verbosity < 1 and (
                    data[-1][0] == TOKEN_TYPE_NAK or (
                        data[0][0] == TOKEN_TYPE_SSPLIT and
                        len(data) == 2
                    ) or (
                        data[0][0] == TOKEN_TYPE_CSPLIT and
                        data[-1][0] == TOKEN_TYPE_NYET
                    )
                ):
            return
        result = ''
        packet_data = None
        for packet in data:
            try:
                decoded = decode(packet)
            except IndexError:
                break
            try:
                result += TOKEN_COLOR[packet[0]]
            except KeyError:
                assert packet[0] == TOKEN_TYPE_PRE_ERR
                # ERR if part of a SPLIT transaction, PRE otherwise.
                # Color & name appropriately.
                result += COLOR_RED + 'ERR' \
                    if data[0][0] in (TOKEN_TYPE_SSPLIT, TOKEN_TYPE_CSPLIT) \
                    else COLOR_GREEN + 'PRE'
            else:
                result += decoded['name'].ljust(7)
            result += '\x1b[0m '
            if 'endpoint' in decoded:
                result += '@%03i.%02i ' % (
                    decoded['address'],
                    decoded['endpoint'],
                )
            elif 'port' in decoded:
                result += '@%03i:%03i %-11s ' % (
                    decoded['address'],
                    decoded['port'],
                    decoded['endpoint_type'],
                )
                if 'continuation' in decoded:
                    result += '%-9s ' % decoded['continuation']
                else:
                    result += 'LS ' if decoded['speed'] else 'HS '
            elif 'data' in decoded:
                if self._verbosity >= 0:
                    assert packet_data is None
                    packet_data = decoded['data']
                result += '%3iB ' % len(decoded['data'])
            elif 'frame' in decoded:
                frame = decoded['frame']
                result += '%4i%s ' % (
                    frame,
                    ('' if decoded['crc_error'] else '.%i' % self._sof_minor),
                )
            if decoded.get('crc_error'):
                result += '\x1b[1;31mCRC error\x1b[0m '
        if incomplete:
            result += '\x1b[1;31m(incomplete transaction)\x1b[0m'
        if packet_data:
            result += '\n' + hexdump(packet_data)
        return result

    def stop(self):
        if self._sof_count:
            self._printSOFCount()

CHUNK_SIZE = 16 * 1024
def main():
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option(
        '-v', '--verbose', action='count',
        default=0, help='Increase verbosity',
    )
    parser.add_option(
        '-q', '--quiet', action='count',
        default=0, help='Decrease verbosity',
    )
    parser.add_option(
        '-i', '--infile', default='-',
        help='Data source (default: stdin)',
    )
    parser.add_option(
        '-o', '--outfile', default='-',
        help='Data destination (default: stdout)',
    )
    parser.add_option(
        '-t', '--tee', help='Also write raw input to that '
        'file. Useful as tee(1) doesn\'t close its stdin when its stdout '
        'gets closed, so next process (ie, this program) does not know it '
        'should exit.',
    )
    parser.add_option('-f', '--follow', action='store_true',
        help='Ignore SIGINT & SIGTERM so all input is read.')
    (options, args) = parser.parse_args()
    if options.infile == '-':
        infile = sys.stdin
    else:
        try:
            infile = open(options.infile, 'r')
        except IOError:
            print >>sys.stderr, 'Could not open --infile %r' % (
                options.infile,
            )
            sys.exit(1)
    if options.outfile == '-':
        write = sys.stdout.write
    else:
        try:
            write = open(options.outfile, 'w').write
        except IOError:
            print >>sys.stderr, 'Could not open --outfile %r' % (
                options.outfile,
            )
            sys.exit(1)
    if options.tee:
        try:
            raw_write = open(options.tee, 'w').write
        except IOError:
            print >>sys.stderr, 'Could not open --tee %r' % (options.tee, )
            sys.exit(1)
    else:
        raw_write = lambda x: None
    human_readable = HumanReadable(write, sys.stderr.write,
        options.verbose - options.quiet)
    stream = ReorderedStream(
        Packetiser(
            TransactionAggregator(
                human_readable,
                human_readable.push,
            ),
            human_readable.push
        )
    )
    push = stream.push
    if options.follow:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
    fcntl.fcntl(
        infile,
        fcntl.F_SETFL,
        fcntl.fcntl(infile, fcntl.F_GETFL) | os.O_NONBLOCK,
    )
    rlist = [infile]
    wlist = elist = []
    read = infile.read
    try:
        while True:
            try:
                data = read(CHUNK_SIZE)
            except IOError, exc:
                if exc.errno != errno.EAGAIN:
                    raise
                # Using select instead of more recent alternatives, because:
                # - we wait on one file descriptor, which is likely to have a
                #   very low value (1 or 4), so bad performance is not really
                #   an issue.
                # - although this is ITI1480A-*linux*, I do not want to
                #   alienate BSD users by relying on epoll.
                # Ignore return value, error is detected by empty read.
                select.select(rlist, wlist, elist)
                continue
            if not data:
                break
            raw_write(data)
            try:
                push(data)
            except ParsingDone:
                break
        stream.stop()
    except IOError, exc:
        # Happens when output is piped to a pager, and pager exits before stdin
        # is fully parsed.
        if exc.errno != errno.EPIPE:
            raise
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()


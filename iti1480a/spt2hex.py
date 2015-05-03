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
from struct import unpack, calcsize
from cStringIO import StringIO

MAGIC = "CSPT"
UNKNOWN_1 = '\x20\x00\x00\x00\x00\x00\x00\x50'
UNKNOWN_2 = '\xa0\x70'
UNKNOWN_3 = '\x00\x00\x6c\x65'
UNKNOWN_4 = '\x0f\x00\x00\x00'
HEADER_FORMAT = '<4sI8s2sH4s4sI'
HEADER_LEN = calcsize(HEADER_FORMAT)

CPUCS_OFFSET = 0xe600
CPUCS_RUN = '\x00'
CPUCS_PAUSE = '\x01'

def iterSPTCommands(spt_file, stop_on_cpu_start=False):
    """
    Iterate over USB commands stored in SPT file format.

    spt_file (file-ish object)
        File object to iterate in.
        Should be positionned at the beginning of a command.
        Read-only access is sufficient.
    stop_on_cpu_start (bool)
        If False (default), dump all commands in a single generator run.
        If True, accesses to CPUCS register with a 0x00 value will cause the
        generator to stop without yielding that command. Upon such stop, file
        position will be on the command following the 0x00 write to CPUCS.
        Also, this makes the generator skip writes to CPUCS with a 0x01 value
        for convenience.

        This is usefull to extract individual programs from a composite file.

    Generated values:
        2-tuple composed of section offset and section data.
    """
    read = spt_file.read
    while True:
        header = read(HEADER_LEN)
        if not header:
            # End of file
            break
        magic, packet_length, unknown_1, unknown_2, offset, unknown_3, \
            unknown_4, data_length = unpack(HEADER_FORMAT, header)
        # Check header contains expected values
        assert magic == MAGIC, repr(magic)
        assert unknown_1 == UNKNOWN_1, repr(unknown_1)
        assert unknown_2 == UNKNOWN_2, repr(unknown_2)
        assert unknown_3 == UNKNOWN_3, repr(unknown_3)
        assert unknown_4 == UNKNOWN_4, repr(unknown_4)
        assert packet_length - data_length == 32, (packet_length, data_length)
        data = read(data_length)
        assert len(data) == data_length, (len(data), data_length)
        if stop_on_cpu_start and offset == CPUCS_OFFSET:
            if data == CPUCS_RUN:
                break
            elif data == CPUCS_PAUSE:
                continue
            else:
                raise ValueError('Unknown CPUCS register value: %r' % (data, ))
        yield (offset, data)

IHX_MAX_LINE_LENGTH = 0x10

def toIntelHex(spt_file):
    """
    spt_file (file)
        The file to read, in SPT format.

    Return value:
        List of strings representing IHX file data.
        Separation between 2 entries in the list is disctated by the presence
        of an explicit write to 1 at CPUCS address in input file.
    """
    ihx_list = []
    while True:
        memory = {}
        for offset, data in iterSPTCommands(spt_file):
            # Handle writes to CPUCS here, so we can detect end of file.
            if offset == CPUCS_OFFSET:
                if data == CPUCS_RUN:
                    break
                elif data == CPUCS_PAUSE:
                    continue
                else:
                    raise ValueError('Unknown CPUCS register value: %r' % (
                        data, ))
            if offset in memory:
                raise ValueError('Code overwriting itself: 0x%x' % (offset, ))
            memory[offset] = data
        if memory:
            ihx_file = StringIO()
            write = ihx_file.write
            offset_list = memory.keys()
            offset_list.sort()
            next_offset = 0
            for offset in offset_list:
                if offset < next_offset:
                    raise ValueError('Code overwriting itself: 0x%x < 0x%x' % (
                        offset, next_offset))
                data = memory[offset]
                next_offset = offset
                while data:
                    chunk = data[:IHX_MAX_LINE_LENGTH]
                    data = data[IHX_MAX_LINE_LENGTH:]
                    chunk_len = len(chunk)
                    line = [
                        chunk_len,
                        (next_offset >> 8) & 0xff,
                        next_offset & 0xff, 0
                    ] + [ord(x) for x in chunk]
                    line.append((-sum(line)) & 0xff)
                    write(':')
                    write(''.join('%02X' % x for x in line))
                    write('\n')
                    next_offset += chunk_len
            write(':00000001FF') # end of file
            ihx_list.append(ihx_file.getvalue())
        else:
            break
    return ihx_list

def main():
    import sys
    from os.path import basename, splitext
    argv = sys.argv
    if len(argv) != 2:
        print 'Usage: %s <file.spt>\n  Result: <file_0.ihx> [<file_1.ihx> ' \
            '[...]]' % (argv[0], )
        sys.exit(1)

    infile_name = argv[1]
    prefix = splitext(basename(infile_name))[0]
    for index, outfile_data in enumerate(toIntelHex(open(infile_name, 'rb'))):
        open('%s_%i.ihx' % (prefix, index), 'w').write(outfile_data)

if __name__ == '__main__':
    main()


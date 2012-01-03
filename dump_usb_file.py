#!/usr/bin/python
from usb_file_format import TYPE_EVENT, TYPE_DATA, TYPE_RXCMD, eventDecoder, \
    rxcmdDecoder, tic_to_time, HumanReadable, Parser, ReorderedStream
import sys

TYPE_DICT = {
    TYPE_EVENT: ('Event', eventDecoder),
    TYPE_DATA: ('Data ', lambda x, y, z: hex(x)),
    TYPE_RXCMD: ('RxCmd', rxcmdDecoder),
}

class RawOutput(object):
    def __init__(self, write, verbose):
        self._write = write
        self._verbose = verbose

    def __call__(self, tic, packet_type, data):
        type_title, type_decoder = TYPE_DICT[packet_type]
        decoded = type_decoder(data, tic, self._verbose)
        if decoded is not None:
            self._write('%s %s %s\n' % (tic_to_time(tic), type_title, decoded))

CHUNK_SIZE = 16 * 1024
def main(read, write, raw_write, verbose=False, emit_raw=True, follow=False):
    if emit_raw:
        emit = RawOutput(write, verbose)
    else:
        emit = Parser(HumanReadable(write, verbose))
    if raw_write is None:
        raw_write = lambda x: None
    push = ReorderedStream(emit).push
    while True:
        data = read(CHUNK_SIZE)
        push(data)
        if len(data) < CHUNK_SIZE and not follow:
            break

if __name__ == '__main__':
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option('-v', '--verbose', action='store_true',
        help='Increase verbosity')
    parser.add_option('-r', '--raw', action='store_true',
        help='Output low-level usb in human-readable form')
    parser.add_option('-i', '--infile', default='-',
        help='Data source (default: stdin)')
    parser.add_option('-o', '--outfile', default='-',
        help='Data destination (default: stdout)')
    parser.add_option('-t', '--tee', help='Also write raw input to that '
        'file. Useful as tee(1) doesn\'t close its stdin when its stdout '
        'gets closed.')
    parser.add_option('-f', '--follow', action='store_true',
        help='Keep waiting for more data when reaching eof.')
    (options, args) = parser.parse_args()
    if options.infile != '-':
        infile = open(options.infile, 'r')
    else:
        infile = sys.stdin
    if options.outfile != '-':
        outfile = open(options.outfile, 'w')
    else:
        outfile = sys.stdout
    if options.tee:
        raw_write = open(options.tee, 'w').write
    else:
        raw_write = lambda x: None
    try:
        main(infile.read, outfile.write, verbose=options.verbose,
            emit_raw=options.raw, raw_write=raw_write, follow=options.follow)
    except EOFError:
        pass


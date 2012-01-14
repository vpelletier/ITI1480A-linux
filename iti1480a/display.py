#!/usr/bin/python
from iti1480a.parser import MESSAGE_RAW, MESSAGE_RESET, MESSAGE_TRANSACTION, \
    tic_to_time, Parser, ReorderedStream, decode
import sys

class HumanReadable(object):
    def __init__(self, write, verbose):
        self._verbose = verbose
        self._write = write
        self._dispatch = {
            MESSAGE_RAW: lambda _, x: x,
            MESSAGE_RESET: self._reset,
            MESSAGE_TRANSACTION: self._transaction,
        }

    def _print(self, tic, printable):
        self._write(tic_to_time(tic) + ' ' + printable + '\n')

    def __call__(self, tic, message_type, data):
        printable = self._dispatch[message_type](tic, data)
        if printable is not None:
            self._print(tic, printable)

    def _reset(self, _, data):
        return 'Device reset (%s)' % (short_tic_to_time(data), )

    def _transaction(self, tic, data, force=False):
        decoded = [decode(x) for x in data]
        result = ''
        for packet in data:
            if result:
                result += ', '
            decoded = decode(packet)
            result += decoded['name']
            if 'data' in decoded:
                result += '=' + ' '.join('%02x' % (ord(x), ) for x in decoded['data'])
        return result

    def stop(self):
        pass

CHUNK_SIZE = 16 * 1024
def main():
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option('-v', '--verbose', action='store_true',
        help='Increase verbosity')
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
    if options.infile == '-':
        read = sys.stdin.read
    else:
        read = open(options.infile, 'r').read
    if options.outfile == '-':
        write = sys.stdout.write
    else:
        write = open(options.outfile, 'w').write
    if options.tee:
        raw_write = open(options.tee, 'w').write
    else:
        raw_write = lambda x: None
    stream = ReorderedStream(Parser(HumanReadable(write, options.verbose)))
    push = stream.push
    while True:
        data = read(CHUNK_SIZE)
        raw_write(data)
        if push(data):
            break
        if len(data) < CHUNK_SIZE and not options.follow:
            stream.stop()
            break

if __name__ == '__main__':
    main()


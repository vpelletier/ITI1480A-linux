#!/usr/bin/python
from iti1480a.parser import MESSAGE_RAW, MESSAGE_RESET, MESSAGE_TRANSACTION, \
    tic_to_time, ReorderedStream, decode, ParsingDone, Packetiser, \
    TransactionAggregator, MESSAGE_LS_EOP, MESSAGE_FS_EOP, short_tic_to_time, \
    MESSAGE_TRANSACTION_ERROR, TOKEN_TYPE_ACK, TOKEN_TYPE_SOF
import sys
import time
import errno

class HumanReadable(object):
    def __init__(self, write, error, verbosity):
        self._write = write
        self._error_write = error
        self._verbosity = verbosity
        noop = lambda _, __: None
        self._dispatch = {
            MESSAGE_RAW: (lambda _, x: x) if verbosity > -1 else noop,
            MESSAGE_RESET: self._reset if verbosity > -1 else noop,
            MESSAGE_TRANSACTION: self._transaction,
            MESSAGE_TRANSACTION_ERROR: self._error,
            MESSAGE_LS_EOP: self._ls_eop if verbosity > 1 else noop,
            MESSAGE_FS_EOP: self._fs_eop if verbosity > 1 else noop,
        }

    def _print(self, tic, printable, write):
        if tic is None:
            time = '?'
        else:
            time = tic_to_time(tic)
        write(time + ' ' + printable + '\n')

    def push(self, tic, message_type, data):
        printable = self._dispatch[message_type](tic, data)
        if printable is not None:
            self._print(tic, printable, self._write)

    def _error(self, tic, data):
        self._print(tic, repr(data), self._error_write)

    def _reset(self, _, data):
        return 'Device reset (%s)' % (short_tic_to_time(data), )

    def _ls_eop(self, _, data):
        return 'LS EOP (%s)' % (short_tic_to_time(data), )

    def _fs_eop(self, _, data):
        return 'FS EOP (%s)' % (short_tic_to_time(data), )

    def _transaction(self, _, data):
        if (data[0][0] == TOKEN_TYPE_SOF and self._verbosity < 3) or (
                data[-1][0] != TOKEN_TYPE_ACK and self._verbosity < 1):
            return None
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
    parser.add_option('-v', '--verbose', action='count',
        default=0, help='Increase verbosity')
    parser.add_option('-q', '--quiet', action='count',
        default=0, help='Decrease verbosity')
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
    try:
        while True:
            data = read(CHUNK_SIZE)
            raw_write(data)
            try:
                push(data)
            except ParsingDone:
                break
            if len(data) < CHUNK_SIZE:
                if options.follow:
                    time.sleep(1)
                else:
                    break
        stream.stop()
    except IOError, exc:
        if exc.errno != errno.EPIPE:
            raise
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()


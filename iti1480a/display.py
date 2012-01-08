#!/usr/bin/python
from parser import TYPE_EVENT, TYPE_DATA, TYPE_RXCMD, MESSAGE_RAW, \
    MESSAGE_RESET, MESSAGE_TRANSACTION, MESSAGE_SOF, MESSAGE_PING, \
    MESSAGE_SPLIT, \
    tic_to_time, Parser, ReorderedStream
import sys

class HumanReadable(object):
    def __init__(self, write, verbose):
        self._verbose = verbose
        self._write = write
        self._dispatch = {
            MESSAGE_RAW: lambda _, x: x,
            MESSAGE_RESET: self._reset,
            MESSAGE_TRANSACTION: self._transaction,
            MESSAGE_SOF: self._sof,
            MESSAGE_PING: self._ping,
            MESSAGE_SPLIT: lambda _, x: x,
        }
        self._firstSOF = None
        self._latestSOF = None
        self._SOFcount = 1
        self._firstNAK = None
        self._NAKcount = 1

    def _print(self, tic, printable):
        self._write(tic_to_time(tic) + ' ' + printable + '\n')

    def __call__(self, tic, message_type, data):
        if message_type != MESSAGE_SOF and self._firstSOF is not None:
            self._dumpSOF()
        if self._firstNAK is not None and (message_type != MESSAGE_TRANSACTION or (data[0], data[2]) != (self._firstNAK[1][0], self._firstNAK[1][2])):
            self._dumpNAK()
        printable = self._dispatch[message_type](tic, data)
        if printable is not None:
            self._print(tic, printable)

    def _ping(self, tic, data):
        start, _, stop, tic_stop = data
        return 'Ping -> %s' % (stop['name'], )

    def _reset(self, _, data):
        return 'Device reset (%s)' % (short_tic_to_time(data), )

    def _transaction(self, tic, data, force=False):
        start, payload, stop, end_tic = data
        if stop is None:
            # Can be cause by:
            # - isochronous IN/OUT
            # - end of trace in the middle of a transaction
            result = "addr %i ep %i %s (no handshake)" % (start['address'], start['endpoint'], start['name'])
        else:
            if not force and not self._verbose:
                if stop['name'] == 'NAK':
                    if self._firstNAK is None:
                        self._firstNAK = (tic, data)
                    else:
                        self._NAKcount += 1
                    return
            result = "addr %i ep %i %s %s'ed at %s" % (start['address'], start['endpoint'], start['name'], stop['name'], tic_to_time(end_tic))
        if payload is not None:
            payload['data'] = ' '.join('%02x' % (ord(x), ) for x in payload['data'])
            result += ': %(name)s %(data)s (crc 0x%(crc)04x)' % payload
        return result

    def _dumpNAK(self):
        tic, data = self._firstNAK
        self._print(tic, self._transaction(tic, data, force=1) + ' * %i' % (self._NAKcount, ))
        self._firstNAK = None
        self._NAKcount = 1

    def _sof(self, tic, data, force=False):
        if self._verbose or force:
            return 'start of frame %(frame)i (crc 0x%(crc)02x)' % data
        elif self._firstSOF is None:
            self._firstSOF = (tic, data)
        else:
            self._latestSOF = data
            self._SOFcount += 1

    def _dumpSOF(self):
        tic, first = self._firstSOF
        if self._SOFcount == 1:
            self._print(tic, self._sof(tic, first, force=True))
        else:
            self._print(tic, 'start of frame * %i (from %i to %i)' % (self._SOFcount, first['frame'], self._latestSOF['frame']))
        self._firstSOF = None
        self._SOFcount = 1

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


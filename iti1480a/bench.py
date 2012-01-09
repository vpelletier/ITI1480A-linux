#!/usr/bin/python
from iti1480a.parser import Packetiser, TransactionAggregator, \
    _TransactionAggregator, ReorderedStream
import sys
import cProfile

thread_run = _TransactionAggregator.run
def profiling_run(self):
    cProfile.runctx('self._parse(lexer=self)', globals(), locals(),
        'thread_prof.out')
_TransactionAggregator.run = profiling_run

CHUNK_SIZE = 16 * 1024
def main():
    read = sys.stdin.read
    out = lambda tic, ttype, data: None
    aggregator = TransactionAggregator(out, out)
    packetiser = Packetiser(aggregator, out)
    stream = ReorderedStream(packetiser)
    cProfile.runctx("go(read, stream)", globals(), locals(), 'prof.out')

def go(read, stream):
    push = stream.push
    while True:
        data = read(CHUNK_SIZE)
        if push(data):
            break
        if len(data) < CHUNK_SIZE:
            stream.stop()
            break

if __name__ == '__main__':
    main()


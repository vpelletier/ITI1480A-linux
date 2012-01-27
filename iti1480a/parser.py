from struct import unpack, error as struct_error
from cStringIO import StringIO
from ply.yacc import yacc
from ply.lex import LexToken
from collections import deque
from threading import Thread, Lock

# I would like to use a Queue.Queue, but profiling shows they cause bad lock
# contention: out of 1:30s total run, 40s of lock wait are spend on
# producer side and 30s on consumer side.
# Moving to threading.Semaphore doesn't improve the state, as they rely on
# threading.Lock object internally in the same pattern as Queue.Queue.
# Chosen scheme shows great success: total run time when profiled went under
# 40s for same data set.
class SimpleQueue(object):
    """
    Similar to Queue.Queue but with simpler locking scheme, reducing lock
    contention. Also means it becomes just a queue: no reliable way to tell if
    it's empty nor how many objects are in it. As a result, there is no limit
    to queue size.
    """
    def __init__(self):
        self._lock = Lock()
        self._queue = deque()

    def get(self):
        while True:
            try:
                return self._queue.popleft()
            except IndexError:
                self._lock.acquire(False)
                self._lock.acquire()

    def put(self, item):
        self._queue.append(item)
        self._lock.acquire(False)
        self._lock.release()

class ParsingDone(Exception):
    """
    Raised when capture end is found.

    More efficient than testing on each iteration, as there is one end per
    parsing session.
    """
    pass

class BaseAggregator(object):
    """
    Various aggregators/parsers in this file must implement this following API.
    """
    def push(self, *args, **kw):
        """
        Regular production. Invocation details are subclass-dependant.
        """
        raise NotImplementedError

    def stop(self):
        """
        Parsing is over, called to give a final opportunity to push pending
        data to next level.
        """
        pass

class NoopAggregator(BaseAggregator):
    """
    Simple wrapper for a callable aggregator.
    """
    def __init__(self, push):
        self.push = push

# RxCmd: see ISP1505A/ISP1505C datasheet
RXCMD_LIST = (
    (0x01, 'DATA0'),
    (0x02, 'DATA1'),
)

RXCMD_VBUS_MASK = 0x0c
RXCMD_VBUS_DICT = {
    0x00: 'VBUS < VB_SESS_END',
    0x04: 'VB_SESS_END <= VBUS < VB_SESS_VLD',
    0x08: 'VB_SESS_VLD <= VBUS < VA_VBUS_VLD',
    0x0c: 'VA_VBUS_VLD <= VBUS',
}

RXCMD_EVENT_MASK = 0x30
RXCMD_EVENT_DICT = {
    0x10: 'RxActive',
    0x20: 'HostDisconnect',
    0x30: 'RxError RxActive',
}

# Event
EVENT_DICT = {
    0x0b: 'LS device connection',
    0x0f: 'FS device connection',
    0x15: 'Device chirp',
    0x18: 'Host chirp',
    0x24: 'HS idle',
    0x62: 'OTG Session request',
    0x69: 'OTG HNP (Host-role changed)',
    0xd0: 'Capture paused',
    0xd1: 'Capture resumed',
    0xe0: 'Capture started',
    0xf0: 'Capture stopped (fifo)',
    0xf1: 'Capture stopped (user)',
}

# File structure:
#   A serie of "packets" of variable length (1 to 5 bytes).
#   Important note: bytes are stored swapped by 2:
#     "Natural" order: 11 22 33 44
#     File order:      22 11 44 33
#   This description uses the "natural" order
# Packet structure:
#   First byte (bits):
#     7-6: payload type
#          00: No payload (used to increment time only)
#          01: Event, 1 byte of payload
#          10: Data, 1 byte of payload
#          11: RxCmd, 1 byte of payload
#     5-4: packet length in bytes, excluding payload
#     3-0: time increment
#   Following byte(s):
#     To be or-ed to timestamp once shifted (see example below)
#   Payload byte (when present)
#     Well... raw event/data/rxcmd value :) .
# Examples:
#   0x00:
#     7-6: 00 (no payload)
#     5-4: 00 (no other time increment byte)
#     3-0: 0000 (null time increment)
#   0xff 0x3f 0xff 0xff
#     7-6: 00 (no payload)
#     5-4: 11 (3 more time increment bytes)
#     3-0: 1111 (16 tics)
#     -> tic count = 0xf
#     Next byte:
#     (tic count) | (0xff << 4)
#     -> tic count = 0xfff
#     Next byte:
#     (tic count) | (0xff << 12)
#     -> tic count = 0xfffff
#     Next byte:
#     (tic count) | (0xff << 20)
#     -> tic count = 0xfffffff
#   0x09 0xd5 0x?? 0x2c:
#     7-6: 11 (RxCmd)
#     5-4: 01 (1 more time increment byte)
#     3-0: 1001 (5 ticks)
#     -> tic count = 0x5
#     Next byte:
#     (tic count) | (0x09 << 4)
#     -> tic count = 0x95
#     Next byte:
#     "VA_VBUS_VLD <= VBUS HostDisconnect" (RxCmd 0x2c)

# Type occupies the 2 most significant bits in first packet byte
TYPE_SHIFT = 6
TYPE_TIME_DELTA = 0x0 # Dataless packet, not shown in UI (used for big delays)
TYPE_EVENT = 0x1      # "Event" in UI
TYPE_DATA = 0x2       # "Data" in UI
TYPE_RXCMD = 0x3      # "RxCmd" in UI

# Packet length occupies the 2 next bits in first packet byte
LENGTH_SHIFT = 4
LENGTH_MASK = 0x3
# Number of tics contained in first packet byte
TIC_HEAD_MASK = 0xf

TIME_INITIAL_MULTIPLIER = 100.0 / 6 # 16.666...

def tic_to_time(tic):
    """
    Represent a tic count as a human-readable time with full precision, from
    minute to nanoseconds.
    """
    tic = int(tic * TIME_INITIAL_MULTIPLIER)
    tic, nano = divmod(tic, 1000)
    tic, micro = divmod(tic, 1000)
    tic, mili = divmod(tic, 1000)
    minute, sec = divmod(tic, 60)
    return '%03i:%02i.%03i\'%03i"%03in' % (minute, sec, mili, micro, nano)

def short_tic_to_time(tic):
    """
    Represent tic count as a human-readable time with limited precision to the
    most significant value pair:
    - minutes & seconds
    - milli- & microseconds
    - micro- & nanoseconds.
    """
    # XXX: is the seconds & milliseconds needed ? Actually, minutes & seconds
    # should not be of much use here...
    tic = int(tic * TIME_INITIAL_MULTIPLIER)
    tic, nano = divmod(tic, 1000)
    tic, micro = divmod(tic, 1000)
    tic, mili = divmod(tic, 1000)
    if tic:
        return '%03i:%02i' % divmod(tic, 60)
    if mili:
        return '%i ms, %i us' % (mili, micro)
    return '%i us, %i ns' % (micro, nano)

TIC_TO_MICROSECOND = TIME_INITIAL_MULTIPLIER / 1000

def tic_to_us(tic):
    """
    Convert a tic count in a floating-point microsecond value.
    """
    return tic * TIC_TO_MICROSECOND

TIC_TO_SECOND = TIME_INITIAL_MULTIPLIER / 1000000000

def tic_to_s(tic):
    """
    Convert a tic count in a floating-point second value.
    """
    return tic * TIC_TO_SECOND

RXCMD_VBUS_HL_DICT = {
    0x0: 'OTG VBus off',
    0x4: 'OTG Session end',
    0x8: 'OTG Session start',
    0xc: 'OTG VBus on',
}

# Duration of a reset after which a device is supposed to have noticed it (as
# per USB specs).
MIN_RESET_TIC = 2500 / TIME_INITIAL_MULTIPLIER # 2.5 us

# Standard USB PIDs.
PID_OUT = 0x1
PID_ACK = 0x2
PID_DATA0 = 0x3
PID_PING = 0x4
PID_SOF = 0x5
PID_NYET = 0x6
PID_DATA2 = 0x7
PID_SPLIT = 0x8
PID_IN = 0x9
PID_NAK = 0xa
PID_DATA1 = 0xb
PID_PRE = PID_ERR = 0xc
PID_SETUP = 0xd
PID_STALL = 0xe
PID_MDATA = 0xf

TOKEN_NAME = {
    PID_OUT: 'OUT',
    PID_PING: 'PING',
    PID_IN: 'IN',
    PID_SETUP: 'SETUP',
}

def _decodeToken(data):
    assert len(data) == 3, data
    crc = data[2][1]
    addr = data[1][1]
    return {
        'name': TOKEN_NAME[data[0][1] & 0xf],
        'address': addr & 0x7f,
        'endpoint': (addr >> 7) | ((crc & 0x7) << 1),
        'crc': crc >> 3
    }

DATA_NAME = {
    PID_DATA0: 'DATA0',
    PID_DATA1: 'DATA1',
    PID_DATA2: 'DATA2',
    PID_MDATA: 'MDATA',
}

def _decodeDATA(data):
    assert len(data) > 2, data
    return {
        'name': DATA_NAME[data[0][1] & 0xf],
        'data': ''.join(chr(x[1]) for x in data[1:-2]),
        'crc': data[-1][1] | (data[-2][1] << 8),
    }

SPLIT_ENDPOINT_TYPE_ISOCHRONOUS = 0x01 << 1
SPLIT_ENDPOINT_TYPE_NAME = {
    0x00: 'Control',
    SPLIT_ENDPOINT_TYPE_ISOCHRONOUS: 'Isochronous',
    0x02 << 1: 'Bulk',
    0x03 << 1: 'Interrupt',
}

SPLIT_ENDPOINT_CONTINUATION = {
    (0, 0): 'middle',
    (0, 1): 'end',
    (1, 0): 'beginning',
    (1, 1): 'whole',
}

def _decodeSPLIT(data):
    assert len(data) == 4, data
    endpoint_type = data[3][1] & 0x6
    result = {
        'address': data[1][1] & 0x7,
        'port': data[2][1] & 0x7,
        'endpoint_type': SPLIT_ENDPOINT_TYPE_NAME[endpoint_type],
        'crc': data[3][1] >> 3,
    }
    speed = data[2][1] >> 3
    end = data[3][1] & 0x1
    if endpoint_type == SPLIT_ENDPOINT_TYPE_ISOCHRONOUS:
        result['continuation'] = SPLIT_ENDPOINT_CONTINUATION[(speed, end)]
    else:
        result['speed'] = speed
        result['end'] = end
    return result

def _decodeSSPLIT(data):
    result = _decodeSPLIT(data)
    result['name'] = 'SSPLIT'
    return result

def _decodeCSPLIT(data):
    result = _decodeSPLIT(data)
    result['name'] = 'CSPLIT'
    return result

def _decodeSOF(data):
    assert len(data) == 3, data
    crc = data[2][1]
    return {
        'name': 'SOF',
        'frame': data[1][1] | ((crc & 0x7) << 8),
        'crc': crc >> 3,
    }

MESSAGE_RAW = 0
MESSAGE_RESET = 1
MESSAGE_TRANSACTION = 2
MESSAGE_TRANSFER = 3
MESSAGE_TRANSACTION_ERROR = 4
MESSAGE_TRANSFER_ERROR = 5

TOKEN_TYPE_OUT = 'OUT'
TOKEN_TYPE_ACK = 'ACK'
TOKEN_TYPE_DATA0 = 'DATA0'
TOKEN_TYPE_PING = 'PING'
TOKEN_TYPE_SOF = 'SOF'
TOKEN_TYPE_NYET = 'NYET'
TOKEN_TYPE_DATA2 = 'DATA2'
TOKEN_TYPE_IN = 'IN'
TOKEN_TYPE_NAK = 'NAK'
TOKEN_TYPE_DATA1 = 'DATA1'
TOKEN_TYPE_PRE_ERR = 'PRE_ERR'
TOKEN_TYPE_SETUP = 'SETUP'
TOKEN_TYPE_STALL = 'STALL'
TOKEN_TYPE_MDATA = 'MDATA'
TOKEN_TYPE_SSPLIT = 'SSPLIT'
TOKEN_TYPE_CSPLIT = 'CSPLIT'

TRANSACTION_DECODER_DICT = {
    TOKEN_TYPE_OUT: _decodeToken,
    TOKEN_TYPE_ACK: lambda _: {'name': 'ACK'},
    TOKEN_TYPE_DATA0: _decodeDATA,
    TOKEN_TYPE_PING: _decodeToken,
    TOKEN_TYPE_SOF: _decodeSOF,
    TOKEN_TYPE_NYET: lambda _: {'name': 'NYET'},
    TOKEN_TYPE_DATA2: _decodeDATA,
    TOKEN_TYPE_SSPLIT: _decodeSSPLIT,
    TOKEN_TYPE_CSPLIT: _decodeCSPLIT,
    TOKEN_TYPE_IN: _decodeToken,
    TOKEN_TYPE_NAK: lambda _: {'name': 'NAK'},
    TOKEN_TYPE_DATA1: _decodeDATA,
    TOKEN_TYPE_PRE_ERR: lambda _: {'name': 'PRE/ERR'},
    TOKEN_TYPE_SETUP: _decodeToken,
    TOKEN_TYPE_STALL: lambda _: {'name': 'STALL'},
    TOKEN_TYPE_MDATA: _decodeDATA,
}

def decode(data):
    decoded = TRANSACTION_DECODER_DICT[data[0]](data[1])
    decoded['tic'] = data[1][0][0]
    return decoded

class _DummyLogger(object):
    """
    Quick hack to make ply.yacc more quiet, without hiding errors.
    """
    def warning(self, *args, **kw):
        pass

    debug = warning
    info = warning

    def error(self, message, *args, **kw):
        # Actually, raising right away is more aggressive than what ply does...
        # But much simpler too.
        assert bool(args) ^ bool(kw), (args, kw)
        if args or kw:
            message %= args or kw
        raise Exception(message)

    critical = error

# TODO: merge BaseYaccAggregator and _BaseYaccAggregator ? (watch out for more
# API collision...)
class _BaseYaccAggregator(Thread):
    """
    Threaded, so ply.yacc can produce output as input is received.
    If only it had a "push" API...
    """
    __start = None

    def __init__(self, token, to_next, to_top):
        """
        token (callable)
            Returns a single token, to be fed to ply.yacc.
            No parameters.
        to_next (BaseAggregator.push)
        to_top (callable)
            Irregular parser production (ie, errors or warnings). Invocation
            details are subclass-dependant.
        """
        super(_BaseYaccAggregator, self).__init__()
        self.token = token
        self._to_next = to_next
        self._to_top = to_top
        yacc_basename = self.__class__.__name__
        # We need to fool ply.yacc into thinking there is no "start" property
        # on its "module", otherwise it will try to use it as a sting in its
        # grammar signature, failing (it's actually a method from Thread
        # class).
        self.start = None
        try:
            # I wish ply to generate parser.out file (with a name depending on
            # instance's class, for subclassing's sake), because it's just too
            # handy for debugging - and this module is just not stable yet.
            # But I don't want ply to blather on stderr, nor create its
            # parsetab files (our grammars are simple enough to be instantly
            # generated).
            self._parser = parser = yacc(
                module=self,
                start=self.__start,
                debugfile=yacc_basename + '_parser.out',
                errorlog=_DummyLogger(),
                write_tables=False,
            )
        finally:
            # Restore access to class's "start" method
            del self.start
        self._parse = parser.parse

    def run(self):
        self._parse(lexer=self)

    @staticmethod
    def _getTokenTic(token):
        raise NotImplementedError

    def p_error(self, p):
        """
        Default parser error handler. Displays the token causing the error,
        and internal parser state (relying on undocumented yacc internals, so
        might go away/break some day).
        """
        # XXX: relies on undocumented yacc internals.
        parser = self._parser
        error_tokens = [x.value for x in parser.symstack if isinstance(x, LexToken)]
        state = parser.statestack[-1]
        self._to_top(self._getTokenTic(error_tokens[0]), self._error_type,
            ('Expected: %r in yacc state %r, got %r' % (parser.action[state].keys(), state, p), error_tokens))

class BaseYaccAggregator(BaseAggregator):
    """
    Base class for ply.yacc-based aggregators.
    Handles generic threading details.
    """
    _yacc_class = None

    def __init__(self, to_next, to_top):
        """
        to_next (BaseAggregator)
        to_top (callable)
            Irregular parser production (ie, errors or warnings). Invocation
            details are subclass-dependant.
        """
        self._to_next = to_next
        self._to_top = to_top
        self.__to_yacc = to_yacc = SimpleQueue()
        self._thread = thread = self._yacc_class(to_yacc.get, to_next.push,
            to_top)
        thread.daemon = True
        thread.start()

    def _to_yacc(self, token_type, token_data):
        token = LexToken()
        token.type = token_type
        token.value = (token_type, token_data)
        token.lineno = 0 # TODO: file offset
        token.lexpos = 0
        self.__to_yacc.put(token)

    def stop(self):
        assert self._thread.is_alive()
        self.__to_yacc.put(None)
        self._thread.join()
        self._to_next.stop()

ENDPOINT0_TRANSFER_TYPE_DICT = {
    (TOKEN_TYPE_SETUP, 0): 'SETUP_OUT',
    (TOKEN_TYPE_SETUP, 0x80): 'SETUP_IN',
    (TOKEN_TYPE_IN, TOKEN_TYPE_ACK): 'IN_ACK',
    (TOKEN_TYPE_IN, TOKEN_TYPE_NAK): 'IN_NAK',
    (TOKEN_TYPE_IN, TOKEN_TYPE_STALL): 'IN_STALL',
    (TOKEN_TYPE_OUT, TOKEN_TYPE_ACK): 'OUT_ACK',
    (TOKEN_TYPE_OUT, TOKEN_TYPE_NAK): 'OUT_NAK',
    (TOKEN_TYPE_OUT, TOKEN_TYPE_NYET): 'OUT_NYET',
    (TOKEN_TYPE_OUT, TOKEN_TYPE_STALL): 'OUT_STALL',
    (TOKEN_TYPE_PING, TOKEN_TYPE_ACK): 'PING_ACK',
    (TOKEN_TYPE_PING, TOKEN_TYPE_NAK): 'PING_NAK',
}

class _Endpoint0TransferAggregator(_BaseYaccAggregator):
    tokens = ENDPOINT0_TRANSFER_TYPE_DICT.values()
    __start = 'transfers'
    _error_type = MESSAGE_TRANSFER_ERROR

    @staticmethod
    def _getTokenTic(token):
        return int(token[1][0][1][0][0])

    def p_transfers(self, p):
        """transfers : transfer
                     | transfers transfer
        """

    def p_transfer(self, p):
        """transfer : SETUP_OUT out_data in_data
                    | SETUP_OUT in_data
                    | SETUP_IN in_data out_handshake
        """
        data = [p[1]]
        data.extend(p[2])
        if len(p) == 4:
            data.extend(p[3])
        self._to_next(None, MESSAGE_TRANSFER, data)

    def p_out_handshake(self, p):
        """out_handshake : OUT_ACK
                         | OUT_NAK out_handshake
                         | PING_ACK out_handshake
                         | PING_NAK out_handshake
        """
        if len(p) == 2:
            p[0] = [p[1]]
        else:
            data = p[2]
            data.insert(0, p[1])
            p[0] = data

    def p_in_data(self, p):
        """in_data : IN_ACK
                   | IN_STALL
                   | IN_ACK in_data
                   | IN_NAK in_data
        """
        if len(p) == 2:
            p[0] = [p[1]]
        else:
            data = p[2]
            data.insert(0, p[1])
            p[0] = data

    def p_out_data(self, p):
        """out_data : OUT_ACK
                    | OUT_ACK out_data
                    | OUT_STALL
                    | OUT_NAK out_data
                    | PING_ACK OUT_ACK
                    | PING_ACK OUT_NYET
                    | PING_NAK out_data
        """
        if len(p) == 2:
            p[0] = [p[1]]
        else:
            data = p[2]
            data.insert(0, p[1])
            p[0] = data

class Endpoint0TransferAggregator(BaseYaccAggregator):
    """
    Aggregates SETUP transactions.
    """
    _yacc_class = _Endpoint0TransferAggregator
    def __init__(self, *args, **kw):
        super(Endpoint0TransferAggregator, self).__init__(*args, **kw)
        self._token_dispatcher = {
            TOKEN_TYPE_SETUP: (self._setup, self._slowSetup),
            TOKEN_TYPE_IN: (self._data, self._slowData),
            TOKEN_TYPE_OUT: (self._data, self._slowData),
            TOKEN_TYPE_PING: (self._ping, ), # No slow ping
        }

    def push(self, tic, transaction_type, data):
        """
        data (list of 2-tuples)
        - one of _TransactionAggregator.tokens
        - 2-tuple:
            - tic
            - token (in yacc terms) value
        """
        assert transaction_type == MESSAGE_TRANSACTION, transaction_type
        token_type = data[0][0]
        slow = data[0][0] == TOKEN_TYPE_PRE_ERR
        if slow:
            token_type = data[1][0]
        self._token_dispatcher[token_type][slow](data)

    def __setup(self, offset, data):
        self._to_yacc(ENDPOINT0_TRANSFER_TYPE_DICT[(TOKEN_TYPE_SETUP, data[offset][1][1][1] & 0x80)], data)

    def _setup(self, data):
        self.__setup(1, data)

    def _slowSetup(self, data):
        self.__setup(3, data)

    def _ping(self, data):
        self._to_yacc(ENDPOINT0_TRANSFER_TYPE_DICT[(TOKEN_TYPE_PING, data[-1][0])], data)

    def __data(self, offset, data):
        self._to_yacc(ENDPOINT0_TRANSFER_TYPE_DICT[(data[offset][0], data[-1][0])], data)

    def _data(self, data):
        self.__data(0, data)

    def _slowData(self, data):
        self.__data(1, data)


class PipeAggregator(BaseAggregator):
    """
    Separates USB transactions per address and endpoint.
    """
    def __init__(self, to_next, to_top, newHub, newPipe):
        """
        to_next (BaseAggregator)
            Used for address-less transactions (SOF).
        to_top
            (unused)
        newHub (callable)
            Receives device address as parameter.
        newPipe (callable)
            Receives device address and endoint as parameters.
        newHub and newPipe must return a BaseAggregator subclass instance.
        """
        self._to_top = to_top
        self._pipe_dict = {}
        self._hub_dict = {}
        self._to_next = to_next
        self._newPipe = newPipe
        self._newHub = newHub

    def _getHub(self, address):
        """
        Return hub at given address. Create one if none is known.
        """
        try:
            result = self._hub_dict[address]
        except KeyError:
            self._hub_dict[address] = result = self._newHub(address)
        return result

    def _getPipe(self, address, endpoint):
        """
        Return pipe at given address and endpoint. Create one if none is known.
        """
        try:
            device = self._pipe_dict[address]
        except KeyError:
            aggregator = self._newPipe(address, endpoint)
            self._pipe_dict[address] = {
                endpoint: aggregator,
            }
        else:
            try:
                aggregator = device[endpoint]
            except KeyError:
                device[endpoint] = aggregator = self._newPipe(address, endpoint)
        return aggregator

    def push(self, tic, transaction_type, data):
        """
        tic & transaction_type: passed through
        data: list of USB transactions
        Parses the first transaction (and possibly second, if first is a
        low-speed marker) to know destination address and endpoint, and passes
        all parameters to appropriate BaseAggregator instance's "push".
        """
        if data[0][0] == TOKEN_TYPE_PRE_ERR:
            decoded = decode(data[1])
        else:
            decoded = decode(data[0])
        address = decoded.get('address')
        if address is None:
            aggregator = self._to_next
            # XXX: should it be broadcast to all device & endpoint
            # aggregators ?
        else:
            endpoint = decoded.get('endpoint')
            if endpoint is None:
                aggregator = self._getHub(address)
            else:
                aggregator = self._getPipe(address, endpoint)
        aggregator.push(tic, transaction_type, data)

    def stop(self):
        for device in self._pipe_dict.itervalues():
            for aggregator in device.itervalues():
                aggregator.stop()
        self._to_next.stop()

TRANSACTION_TYPE_DICT = {
    PID_OUT: TOKEN_TYPE_OUT,
    PID_ACK: TOKEN_TYPE_ACK,
    PID_DATA0: TOKEN_TYPE_DATA0,
    PID_PING: TOKEN_TYPE_PING,
    PID_SOF: TOKEN_TYPE_SOF,
    PID_NYET: TOKEN_TYPE_NYET,
    PID_DATA2: TOKEN_TYPE_DATA2,
    PID_IN: TOKEN_TYPE_IN,
    PID_NAK: TOKEN_TYPE_NAK,
    PID_DATA1: TOKEN_TYPE_DATA1,
    PID_PRE: TOKEN_TYPE_PRE_ERR,
    PID_SETUP: TOKEN_TYPE_SETUP,
    PID_STALL: TOKEN_TYPE_STALL,
    PID_MDATA: TOKEN_TYPE_MDATA,
}

class _TransactionAggregator(_BaseYaccAggregator):
    tokens = TRANSACTION_TYPE_DICT.values() + [TOKEN_TYPE_SSPLIT, TOKEN_TYPE_CSPLIT]
    __start = 'transactions'
    _error_type = MESSAGE_TRANSACTION_ERROR

    @staticmethod
    def _getTokenTic(token):
        return token[1][0][0]

    def p_transactions(self, p):
        """transactions : transaction
                        | transactions transaction
        """

    def p_transaction(self, p):
        """transaction : SETUP DATA0 ACK
                       | SSPLIT token data handshake
                       | SSPLIT token data
                       | SSPLIT token
                       | SSPLIT token handshake
                       | CSPLIT token data
                       | CSPLIT token
                       | CSPLIT PRE_ERR
                       | CSPLIT token handshake
                       | PRE_ERR SETUP PRE_ERR DATA0 ACK
                       | IN data ACK
                       | IN data
                       | IN NAK
                       | IN STALL
                       | PRE_ERR IN low_speed_data PRE_ERR ACK
                       | PRE_ERR IN NAK
                       | PRE_ERR IN STALL
                       | OUT data handshake
                       | OUT data
                       | PRE_ERR OUT PRE_ERR low_speed_data low_speed_handshake
                       | PING ACK
                       | PING NAK
                       | PING STALL
                       | SOF
        """
        self._to_next(p[1][1][0][0], MESSAGE_TRANSACTION, p[1:])

    def p_token(self, p):
        """token : IN
                 | OUT
                 | SETUP
        """
        p[0] = p[1]

    def p_data(self, p):
        """data : low_speed_data
                | DATA2
                | MDATA
        """
        p[0] = p[1]

    def p_low_speed_data(self, p):
        """low_speed_data : DATA0
                          | DATA1
        """
        p[0] = p[1]

    def p_handshake(self, p):
        """handshake : low_speed_handshake
                     | NYET
        """
        p[0] = p[1]

    def p_low_speed_handshake(self, p):
        """low_speed_handshake : ACK
                               | NAK
                               | STALL
        """
        p[0] = p[1]

class TransactionAggregator(BaseYaccAggregator):
    """
    Aggregates consecutive USB packets into USB transactions.
    Validates PID value of each packet.

    to_next.push receives 3 parameters:
    - first tic in transaction
    - MESSAGE_TRANSACTION
    - list of USB packets
    to_top receives 3 parameters:
    - packet tic
    - MESSAGE_RAW
    - string
    """
    _yacc_class = _TransactionAggregator

    def push(self, packet):
        """
        packet (list of 2-tuples)
            - tic (int)
            - data (int, one byte)
        """
        assert packet
        tic, pid = packet[0]
        cannon_pid = pid & 0xf
        if cannon_pid != pid >> 4 ^ 0xf:
            self._to_top(tic, MESSAGE_RAW, '(bad pid) 0x' + ' 0x'.join('%02x' % (x[1], ) for x in packet))
            return
        try:
            trans_type = TRANSACTION_TYPE_DICT[cannon_pid]
        except KeyError:
            if cannon_pid == PID_SPLIT:
                trans_type = (packet[1][1] & 0x8) and TOKEN_TYPE_CSPLIT or TOKEN_TYPE_SSPLIT
            else:
                raise
        self._to_yacc(trans_type, packet)

class Packetiser(BaseAggregator):
    """
    Aggregates consecutive data bytes with rxActive enabled into USB packets.
    Also, handles data-less bus events (reset, device [dis]connection, bus
    voltage changes...).
    """
    _rxactive = False
    _reset_start_tic = None
    _vbus = None
    _connected = False

    def __init__(self, to_next, to_top):
        """
        to_next (BaseAggregator)
            "push" is called with a list of 2-tuples:
            - tic
            - data byte
        to_top (callable)
            Called with 3 parameters:
            - tic
            - event type (MESSAGE_RAW, MESSAGE_RESET)
            - event
        """
        self._type_dict = {
            TYPE_EVENT: self._event,
            TYPE_DATA: self._data,
            TYPE_RXCMD: self._rxcmd,
        }
        self._to_next = to_next
        self._to_top = to_top
        self._data = []

    def push(self, tic, packet_type, data):
        """
        tic (int)
        packet_type
            One of TYPE_EVENT, TYPE_RXCMD, TYPE_DATA
        data (int)
            Byte.
        """
        # TODO: recognise low-speed keep-alive.
        if self._reset_start_tic is not None and \
                packet_type != TYPE_EVENT and (packet_type != TYPE_RXCMD or
                data & RXCMD_VBUS_MASK != RXCMD_VBUS_MASK):
            if tic >= self._reset_start_tic + MIN_RESET_TIC:
                self._to_top(self._reset_start_tic, MESSAGE_RESET,
                    tic - self._reset_start_tic)
            self._reset_start_tic = None
        self._type_dict[packet_type](tic, data)

    def stop(self):
        # TODO: flush any pending reset ? requires knowing last tic before
        # stop was called
        if self._data:
            self._to_next.push(self._data)
            self._data = []
        self._to_next.stop()

    def _event(self, tic, data):
        try:
            caption = EVENT_DICT[data]
        except KeyError:
            caption = '(unknown event 0x%02x)' % (data, )
        self._to_top(tic, MESSAGE_RAW, caption)
        if data in (0xf, 0xb):
            self._connected = True
        elif data in (0xf0, 0xf1):
            raise ParsingDone

    def _data(self, tic, data):
        assert self._rxactive
        self._data.append((tic, data))

    def _rxcmd(self, tic, data):
        # TODO:
        # - RxError
        # - Data0 & Data1
        rxactive = data & 0x10
        if self._rxactive and not rxactive:
            self._to_next.push(self._data)
            self._data = []
        self._rxactive = rxactive
        if data & 0x20 and self._connected:
            rendered = 'Device disconnected'
            self._connected = False
        else:
            vbus = data & RXCMD_VBUS_MASK
            if data == RXCMD_VBUS_MASK:
                # Maybe a reset, detect on next data
                self._reset_start_tic = tic
            if vbus == self._vbus:
                return
            self._vbus = vbus
            rendered = RXCMD_VBUS_HL_DICT[vbus]
        self._to_top(tic, MESSAGE_RAW, rendered)

class ReorderedStream(BaseAggregator):
    """
    Transfor a serie of data chunks in .usb file order into tic count, type
    and data values.
    Incomplete chunk tail is preserved to be decoded with next data block.
    """
    def __init__(self, out):
        """
        out (BaseAggregator)
            "push" receives 3 parameters:
            - tic count (arbitrarily long integer)
            - type (TYPE_EVENT, TYPE_DATA or TYPE_RXCMD)
            - data (1-byte integer)
        """
        self._remain = ''
        self._out = out
        self._tic = 0

    def push(self, data):
        """
        data (string)
            File chunk to process.
        """
        out = self._out.push
        tic = self._tic
        read = StringIO(self._remain + data).read
        def read16():
            data = read(2)
            # In case we won't have enough data to decode packet entirely
            self._remain += data
            return unpack('<H', data)[0]
        try:
            while True:
                # Clear backlog at packet boundary
                self._remain = ''
                packet = read16()
                head = packet >> 8
                packet_type = head >> TYPE_SHIFT
                packet_len = (head >> LENGTH_SHIFT) & LENGTH_MASK
                tic_count = head & TIC_HEAD_MASK
                if packet_len:
                    tic_count |= (packet & 0xff) << 4
                    if packet_len > 1:
                        packet = read16()
                        tic_count |= (packet & 0xff00) << 4
                        if packet_len > 2:
                            tic_count |= (packet & 0xff) << 20
                            if packet_type == TYPE_TIME_DELTA:
                                tic += tic_count
                                continue
                            packet = read16()
                            assert packet & 0xff == 0, hex(packet)
                            data = packet >> 8
                        else:
                            data = packet & 0xff
                    else:
                        data = read16() >> 8
                else:
                    data = packet & 0xff
                tic += tic_count
                out(tic, packet_type, data)
        except struct_error:
            assert read() == ''
        # XXX: self._tic is not updated if any unhandled exception is raised.
        self._tic = tic

    def stop(self):
        self._out.stop()


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
# So I end up abusing threading.Lock.release, with great success: total run
# time when profiled went under 40s for same data set.
class SimpleQueue(object):
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
        try:
            self._lock.release()
        except:
            pass

class ParsingDone(Exception):
    pass

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
    tic = int(tic * TIME_INITIAL_MULTIPLIER)
    tic, nano = divmod(tic, 1000)
    tic, micro = divmod(tic, 1000)
    tic, mili = divmod(tic, 1000)
    minute, sec = divmod(tic, 60)
    return '%03i:%02i.%03i\'%03i"%03in' % (minute, sec, mili, micro, nano)

def short_tic_to_time(tic):
    tic = int(tic * TIME_INITIAL_MULTIPLIER)
    tic, nano = divmod(tic, 1000)
    tic, micro = divmod(tic, 1000)
    tic, mili = divmod(tic, 1000)
    minute, sec = divmod(tic, 60)
    if minute or sec:
        return '%03i:%02i' % (minute, sec)
    if mili:
        return '%i ms, %i us' % (mili, micro)
    return '%i us, %i ns' % (micro, nano)

TIC_TO_MICROSECOND = TIME_INITIAL_MULTIPLIER / 1000
def tic_to_us(tic):
    return tic * TIC_TO_MICROSECOND

TIC_TO_SECOND = TIME_INITIAL_MULTIPLIER / 1000000000
def tic_to_s(tic):
    return tic * TIC_TO_SECOND

RXCMD_VBUS_HL_DICT = {
    0x0: 'OTG VBus off',
    0x4: 'OTG Session end',
    0x8: 'OTG Session start',
    0xc: 'OTG VBus on',
}
MIN_RESET_TIC = 2.5 * TIME_INITIAL_MULTIPLIER # 2.5 us
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
    crc = data[2]
    addr = data[1]
    return {
        'name': TOKEN_NAME[data[0] & 0xf],
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
    return {
        'name': DATA_NAME[data[0] & 0xf],
        'data': ''.join(chr(x) for x in data[1:-2]),
        'crc': data[-1] | (data[-2] << 8),
    }
SPLIT_NAME = {
    0: 'SSPLIT',
    1: 'CSPLIT',
}
SPLIT_HUB_MASK = 0x7
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
    endpoint_type = data[3] & 0x6
    result = {
        'name': SPLIT_NAME[data[1] >> 7],
        'hub': data[1] & 0x7,
        'port': data[2] & 0x7,
        'endpoint_type': SPLIT_ENDPOINT_TYPE_NAME[endpoint_type],
        'crc': data[3] >> 3,
    }
    speed = data[2] >> 3
    end = data[3] & 0x1
    if endpoint_type == SPLIT_ENDPOINT_TYPE_ISOCHRONOUS:
        result['continuation'] = SPLIT_ENDPOINT_CONTINUATION[(speed, end)]
    else:
        result['speed'] = speed
        result['end'] = end
    return result

MESSAGE_RAW = 0
MESSAGE_RESET = 1
MESSAGE_TRANSACTION = 2
MESSAGE_SOF = 3
MESSAGE_PING = 4
MESSAGE_SPLIT = 5

TRANSACTION_TYPE_DICT = {
    PID_OUT: 'OUT',
    PID_ACK: 'ACK',
    PID_DATA0: 'DATA0',
    PID_PING: 'PING',
    PID_SOF: 'SOF',
    PID_NYET: 'NYET',
    PID_DATA2: 'DATA2',
    PID_IN: 'IN',
    PID_NAK: 'NAK',
    PID_DATA1: 'DATA1',
    PID_PRE: 'PRE_ERR',
    PID_SETUP: 'SETUP',
    PID_STALL: 'STALL',
    PID_MDATA: 'MDATA',
}

class _TransactionAggregator(Thread):
    """
    Threaded, so ply.yacc can produce output as input is received.
    If only it had a "push" API...
    """
    tokens = TRANSACTION_TYPE_DICT.values() + ['SSPLIT', 'CSPLIT']

    def __init__(self, token, to_next, to_top):
        super(_TransactionAggregator, self).__init__()
        self.token = token
        self._to_next = to_next
        self._to_top = to_top
        # Se need to fool ply.yacc into thinking there is no "start" property
        # on its "module", otherwise it will try to use it as a sting in its
        # grammar signature, failing.
        self.start = None
        try:
            self._parser = parser = yacc(module=self, start='transactions')
        finally:
            # Restore access to class's "start" method
            del self.start
        self._parse = parser.parse

    def run(self):
        self._parse(lexer=self)

    def _error(self, p):
        p = p[1:]
        self._to_top(p[0][0], MESSAGE_RAW, 'Short transaction')

    def p_error(self, p):
        if p is not None:
            # XXX: relies on undocumented yacc internals.
            parser = self._parser
            statestack = parser.statestack
            print 'yacc error on', p, 'time=', tic_to_time(p.value[0]), \
                'statestack=', statestack, 'expected:', \
                parser.action[statestack[-1]]

    def p_transactions(self, p):
        """transactions : transaction
                        | transactions transaction"""

    def p_transaction(self, p):
        """transaction : start_split
                       | complete_split
                       | control
                       | in
                       | out
                       | ping
                       | sof"""

    def p_start_split(self, p):
        """start_split : SSPLIT token data handshake
                       | SSPLIT token data
                       | SSPLIT token handshake
                       | SSPLIT token"""
        # TODO

    def p_complete_split(self, p):
        """complete_split : CSPLIT token data
                          | CSPLIT token handshake
                          | CSPLIT token
                          | CSPLIT PRE_ERR"""
        # TODO

    def p_control(self, p):
        """control : normal_control
                   | low_speed_control
        """
        # TODO: new API with low_speed support
        low_speed, ((tic, start), (_, data), (tic_stop, stop)) = p[1]
        self._to_next(tic, MESSAGE_TRANSACTION, (
            _decodeToken(start),
            _decodeDATA(data),
            {'name': TRANSACTION_TYPE_DICT[stop[0] & 0xf]},
            tic_stop,
        ))

    def p_normal_control(self, p):
        """normal_control : SETUP DATA0 ACK
        """
        p[0] = (False, p[1:])

    def p_low_speed_control(self, p):
        """low_speed_control : PRE_ERR SETUP PRE_ERR DATA0 ACK
        """
        p[0] = (True, (p[2], p[4], p[5]))

    def p_in(self, p):
        """in : normal_in
              | handshake_in
              | low_speed_in"""
        # TODO: new API with low_speed support
        low_speed, tic, data = p[1]
        self._to_next(tic, MESSAGE_TRANSACTION, data)

    def p_normal_in(self, p):
        """normal_in : IN data ACK
                     | IN data
        """
        tic, start = p[1]
        _, data = p[2]
        if len(p) == 4:
            tic_stop, stop = p[3]
            stop = {'name': TRANSACTION_TYPE_DICT[stop[0] & 0xf]}
        else:
            tic_stop = stop = None
        p[0] = (False, tic, (
            _decodeToken(start),
            _decodeDATA(data),
            stop,
            tic_stop,
        ))

    def p_handshake_in(self, p):
        # Needed just because ply.yacc doesn't give us the token type.
        """handshake_in : IN NAK
                        | IN STALL"""
        tic, start = p[1]
        tic_stop, stop = p[2]
        p[0] = (False, tic, (
            _decodeToken(start),
            None,
            {'name': TRANSACTION_TYPE_DICT[stop[0] & 0xf]},
            tic_stop,
        ))

    def p_low_speed_in(self, p):
        # Note: no "IN data" equivalent rule, as it's only valid for
        # isochronous transaction, which aren't available in low-speed.
        """low_speed_in : PRE_ERR IN low_speed_data PRE_ERR ACK
                        | PRE_ERR IN NAK
                        | PRE_ERR IN STALL
        """
        tic, start = p[2]
        if len(p) == 4:
            data = None
            tic_stop, stop = p[3]
        else:
            _, data = p[3]
            data = _decodeDATA(data)
            tic_stop, stop = p[5]
        p[0] = (True, tic, (
            _decodeToken(start),
            data,
            {'name': TRANSACTION_TYPE_DICT[stop[0] & 0xf]},
            tic_stop,
        ))

    def p_out(self, p):
        """out : base_out
               | low_speed_out
        """
        # TODO: new API
        low_speed, transaction = p[1]
        tic, start = transaction[0]
        _, data = transaction[1]
        if len(transaction) == 3:
            tic_stop, stop = transaction[2]
            stop = {'name': TRANSACTION_TYPE_DICT[stop[0] & 0xf]}
        else:
            tic_stop = stop = None
        self._to_next(tic, MESSAGE_TRANSACTION, (
            _decodeToken(start),
            _decodeDATA(data),
            stop,
            tic_stop,
        ))

    def p_base_out(self, p):
        """base_out : OUT data handshake
                    | OUT data
        """
        p[0] = (False, p[1:])

    def p_low_speed_out(self, p):
        # Note: no "OUT data" equivalent rule, as it's only valid for
        # isochronous transaction, which aren't available in low-speed.
        """low_speed_out : PRE_ERR OUT low_speed_data PRE_ERR low_speed_handshake
        """
        p[0] = (True, (p[2], p[3], p[5]))

    def p_bulk_ping(self, p):
        """ping : PING ACK
                | PING NAK
                | PING STALL
        """
        # TODO: new API
        tic, start = p[1]
        tic_stop, stop = p[2]
        self._to_next(tic, MESSAGE_PING, (
            _decodeToken(start),
            None,
            {'name': TRANSACTION_TYPE_DICT[stop[0] & 0xf]},
            tic_stop,
        ))

    def p_token(self, p):
        """token : IN
                 | OUT
                 | SETUP"""
        p[0] = p[1]

    def p_data(self, p):
        """data : low_speed_data
                | DATA2
                | MDATA"""
        p[0] = p[1]

    def p_low_speed_data(self, p):
        """low_speed_data : DATA0
                          | DATA1
        """
        p[0] = p[1]

    def p_handshake(self, p):
        """handshake : low_speed_handshake
                     | NYET"""
        p[0] = p[1]

    def p_low_speed_handshake(self, p):
        """low_speed_handshake : ACK
                               | NAK
                               | STALL
        """
        p[0] = p[1]

    def p_sof(self, p):
        """sof : SOF"""
        tic, data = p[1]
        if len(data) != 3:
            self._error(p)
            return
        crc = data[2]
        self._to_next(tic, MESSAGE_SOF, {
            'name': 'SOF',
            'frame': data[1] | ((crc & 0x7) << 8),
            'crc': crc >> 3,
        })

class TransactionAggregator(object):
    def __init__(self, to_next, to_top):
        self._to_next = to_next
        self._to_top = to_top
        self._to_yacc = to_yacc = SimpleQueue()
        self._thread = thread = _TransactionAggregator(to_yacc.get, to_next, to_top)
        thread.daemon = True
        thread.start()

    def __call__(self, tic, packet):
        assert packet
        pid = packet[0]
        cannon_pid = pid & 0xf
        if cannon_pid != pid >> 4 ^ 0xf:
            self._to_top(tic, MESSAGE_RAW, '(bad pid) 0x' + ' 0x'.join('%02x' % x for x in packet))
            return
        # TODO: CRC check
        try:
            trans_type = TRANSACTION_TYPE_DICT[cannon_pid]
        except KeyError:
            if cannon_pid == PID_SPLIT:
                trans_type = (packet[1] & 0x8) and 'CSPLIT' or 'SSPLIT'
            else:
                raise
        token = LexToken()
        token.type = trans_type
        token.value = (tic, packet)
        token.lineno = 0 # TODO: file offset
        token.lexpos = 0
        self._to_yacc.put(token)

    def stop(self):
        assert self._thread.is_alive()
        self._to_yacc.put(None)
        self._thread.join()

class Packetiser(object):
    _rxactive = False
    _reset_start_tic = None
    _vbus = None
    _data_tic = None
    _connected = False

    def __init__(self, to_next, to_top):
        self._type_dict = {
            TYPE_EVENT: self.event,
            TYPE_DATA: self.data,
            TYPE_RXCMD: self.rxcmd,
        }
        self._to_next = to_next
        self._to_top = to_top
        self._data = []

    def __call__(self, tic, packet_type, data):
        # TODO: recognise low-speed keep-alive.
        if self._reset_start_tic is not None and \
                packet_type != TYPE_EVENT and (packet_type != TYPE_RXCMD or
                data & RXCMD_VBUS_MASK != RXCMD_VBUS_MASK):
            if tic >= self._reset_start_tic + MIN_RESET_TIC:
                self._to_top(tic, MESSAGE_RESET, tic - self._reset_start_tic)
            self._reset_start_tic = None
        self._type_dict[packet_type](tic, data)

    def event(self, tic, data):
        try:
            caption = EVENT_DICT[data]
        except KeyError:
            caption = '(unknown event 0x%02x)' % (data, )
        self._to_top(tic, MESSAGE_RAW, caption)
        if data in (0xf, 0xb):
            self._connected = True
        elif data in (0xf0, 0xf1):
            self.stop()
            raise ParsingDone

    def stop(self):
        # TODO: flush any pending reset ? requires knowing last tic before
        # stop was called
        if self._data:
            self._to_next(self._data_tic, self._data)
        self._to_next.stop()

    def data(self, _, data):
        assert self._rxactive
        assert self._data_tic is not None
        self._data.append(data)

    def rxcmd(self, tic, data):
        # TODO:
        # - RxError
        # - Data0 & Data1
        rxactive = data & 0x10
        if self._rxactive ^ rxactive:
            if rxactive:
                assert self._data_tic is None
                self._data_tic = tic
            else:
                self._to_next(self._data_tic, self._data)
                self._data = []
                self._data_tic = None
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

class Parser(object):
    def __init__(self, push):
        self._packetiser = Packetiser(
            TransactionAggregator(
                push,
                self.log
            ),
            self.log
        )

    def __call__(self, *args, **kw):
        try:
            self._packetiser(*args, **kw)
        except ParsingDone, exc:
            return True
        return False

    def stop(self):
        self._packetiser.stop()

    def log(self, tic, _, message):
        # TODO: replace by a push and note that caller might receive event
        # out-of-order (because of yacc thread).
        print 'NewParser.log', tic_to_time(tic), _, message

class ReorderedStream(object):
    def __init__(self, out):
        self._remain = ''
        self._out = out
        self._tic = 0

    def push(self, data):
        out = self._out
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
                if out(tic, packet_type, data):
                    return True
        except struct_error:
            assert read() == ''
        self._tic = tic
        return False

    def stop(self):
        self._out.stop()


from struct import unpack, error as struct_error
from cStringIO import StringIO

class IndexedList(object):
    def __init__(self):
        self._key_list = []
        self._dict = {}

    def getFirstKey(self):
        return self._key_list[0]

    def getFirstValue(self):
        return self._dict[self._key_list[0]]

    def pop(self, key=None):
        if key is None:
            key = self._key_list.pop(0)
        else:
            self._key_list.remove(key)
        return self._dict.pop(key)

    def __setitem__(self, key, value):
        if key not in self._dict:
            self._key_list.append(key)
        self._dict[key] = value

    def __getitem__(self, key):
        return self._dict[key]

    def __nonzero__(self):
        return bool(self._dict)

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

_rxcmd_previous_data = None

def rxcmdDecoder(data, _, __):
    global _rxcmd_previous_data
    if data == _rxcmd_previous_data:
        return None
    _rxcmd_previous_data = data
    result = []
    append = result.append
    for mask, caption in RXCMD_LIST:
        if data & mask:
            append(caption)
    append(RXCMD_VBUS_DICT[data & RXCMD_VBUS_MASK])
    event = data & RXCMD_EVENT_MASK
    if event:
        append(RXCMD_EVENT_DICT[event])
    return ' '.join(result)

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

stop_printing = False
def eventDecoder(data, _, verbose):
    global stop_printing
    stop_printing = data & 0xf0 == 0xf0
    try:
        result = EVENT_DICT[data]
    except KeyError:
        if verbose:
            result = '0x%02x' % (data, )
        else:
            result = None
    return result

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
def _decodeSOF(data):
    assert len(data) == 3, data
    crc = data[2]
    return {
        'name': 'SOF',
        'frame': data[1] | ((crc & 0x7) << 8),
        'crc': crc >> 3,
    }
TOKEN_NAME = {
    PID_OUT: 'OUT',
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
PACKET_DECODER = {
    PID_OUT: _decodeToken,
    PID_ACK: lambda _: {'name': 'ACK'},
    PID_DATA0: _decodeDATA,
    PID_PING: lambda _: {'name': 'PING'},
    PID_SOF: _decodeSOF,
    PID_NYET: lambda _: {'name': 'NYET'},
    PID_DATA2: _decodeDATA,
    PID_SPLIT: lambda _: {'name': 'SPLIT'},
    PID_IN: _decodeToken,
    PID_NAK: lambda _: {'name': 'NAK'},
    PID_DATA1: _decodeDATA,
    PID_PRE: lambda _: {'name': 'PRE/ERR'},
    PID_SETUP: _decodeToken,
    PID_STALL: lambda _: {'name': 'STALL'},
    PID_MDATA: _decodeDATA,
}

MESSAGE_RAW = 0
MESSAGE_RESET = 1
MESSAGE_TRANSACTION = 2
MESSAGE_SOF = 3

class Parser(object):
    # XXX: no brain was harmed in the writing of this class.
    # It's just a stupid first try to get a feeling on how to do it properly.
    def __init__(self, push):
        self._message_queue = IndexedList()
        self._before = []
        self._push = push
        self._type_dict = {
            TYPE_EVENT: self.event,
            TYPE_DATA: self.data,
            TYPE_RXCMD: self.rxcmd,
        }
        self._connected = False
        self._transaction = None
        self._transaction_data = None
        self._last_vbus = False
        self._done = False

    def addBefore(self, tic, func, context):
        self._message_queue[tic] = None
        self._before.append((tic, func, context))

    def __call__(self, tic, packet_type, data):
        new_before = []
        append = new_before.append
        skip = False
        for original_tic, func, context in self._before:
            if skip:
                preserve = True
            else:
                preserve, skip = func(original_tic, context, tic,
                    packet_type, data)
            if preserve:
                append((original_tic, func, context))
        self._before = new_before
        if not skip:
            self._type_dict[packet_type](tic, data)
        return self._done and not self._message_queue

    def _write(self, tic, message_class, message):
        queue = self._message_queue
        pop = queue.pop
        if message is None:
            pop(tic)
        else:
            queue[tic] = (tic, message_class, message)
        push = self._push
        getFirstValue = queue.getFirstValue
        while queue and getFirstValue() is not None:
            push(*pop())

    def event(self, tic, data):
        if data in (0xf0, 0xf1):
            self._done = True
        caption = eventDecoder(data, None, False)
        if data == 0xf:
            self._connected = True
        if caption is not None:
            self._write(tic, MESSAGE_RAW, caption)

    def data(self, tic, data):
        self._write(tic, MESSAGE_RAW, 'Unexpected data: ' + hex(data))

    def rxcmd(self, tic, data):
        if self._connected and data & 0x20:
            rendered = 'Device disconnected'
            self._connected = False
        elif data & 0x10:
            # XXX: Packet timestamp is the timestamp of leading RxActive
            # packet, while ITI's software displays the timestamp of first
            # data packet.
            self.addBefore(tic, self._packetAgregator, [])
            return
        else:
            vbus = data & RXCMD_VBUS_MASK
            if data == RXCMD_VBUS_MASK:
                # Maybe a reset, detect on next packet
                self.addBefore(tic, self._resetDetector, None)
            if vbus == self._last_vbus:
                return
            self._last_vbus = vbus
            rendered = RXCMD_VBUS_HL_DICT[vbus]
        self._write(tic, MESSAGE_RAW, rendered)

    def _resetDetector(self, original_tic, _, tic, packet_type, data):
        if packet_type == TYPE_RXCMD and data & 0xc == 0xc:
            delta = tic - original_tic
            if delta > MIN_RESET_TIC:
                self._write(original_tic, MESSAGE_RESET, delta)
            return False, not self._connected
        return True, False

    def _packetAgregator(self, original_tic, context, tic, packet_type, data):
        if packet_type == TYPE_DATA:
            context.append(data)
            return True, True
        if packet_type == TYPE_EVENT:
            return True, False
        if packet_type == TYPE_RXCMD and not data & 0x10:
            if not context:
                return False, True
            pid = context[0]
            cannon_pid = pid & 0xf
            if cannon_pid != pid >> 4 ^ 0xf:
                self._write(original_tic, MESSAGE_RAW,
                    '(bad pid) 0x' + ' 0x'.join('%02x' % x for x in context))
                return False, True
            try:
                decoder = PACKET_DECODER[cannon_pid]
            except KeyError:
                self._write(original_tic, MESSAGE_RAW,
                    '(unk. data packet) 0x' + ' 0x'.join('%02x' % x
                        for x in context))
                return False, True
            decoded = decoder(context)
            # TODO: handle CRC5/CRC16 checks
            if cannon_pid == PID_SOF:
                self._write(original_tic, MESSAGE_SOF, decoded)
                return False, True
            elif cannon_pid in (PID_OUT, PID_IN, PID_SETUP):
                assert self._transaction is None, self._transaction
                self._transaction = (original_tic, decoded)
                return False, True
            elif cannon_pid in (PID_ACK, PID_NAK, PID_STALL):
                assert self._transaction is not None, tic_to_time(tic)
                transaction_tic, transaction = self._transaction
                self._write(transaction_tic, MESSAGE_TRANSACTION, (
                    transaction, # Start
                    self._transaction_data, # Data (or None)
                    decoded, # Conclusion
                    original_tic, # Conclusion tic
                ))
                self._transaction = None
                self._transaction_data = None
                decoded = None
            elif cannon_pid in (PID_DATA0, PID_DATA1, PID_DATA2, PID_MDATA):
                # TODO: decode data
                assert self._transaction_data is None, \
                    self._transaction_data
                assert self._transaction is not None
                self._transaction_data = decoded
                decoded = None
            else:
                # TODO:
                # - PID_PING
                # - PID_NYET
                # - PID_SPLIT
                # - PID_PRE / PID_ERR
                # In the meantime, emit them as MESSAGE_RAW
                decoded = repr(decoded)
            self._write(original_tic, MESSAGE_RAW, decoded)
        return False, True

class HumanReadable(object):
    def __init__(self, write, verbose):
        self._verbose = verbose
        self._write = write
        self._dispatch = {
            MESSAGE_RAW: lambda _, x: x,
            MESSAGE_RESET: self._reset,
            MESSAGE_TRANSACTION: self._transaction,
            MESSAGE_SOF: self._sof,
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

    def _reset(self, _, data):
        return 'Device reset (%s)' % (short_tic_to_time(data), )

    def _transaction(self, tic, data, force=False):
        start, payload, stop, end_tic = data
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
                    break
        except struct_error:
            assert read() == ''
        self._tic = tic


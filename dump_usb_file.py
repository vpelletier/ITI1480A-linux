#!/usr/bin/python
import sys
import time
from struct import unpack, error as struct_error

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

# Payload type identification
TYPE_DICT = {
    TYPE_EVENT: ('Event', eventDecoder),
    TYPE_DATA: ('Data ', lambda x, y, z: hex(x)),
    TYPE_RXCMD: ('RxCmd', rxcmdDecoder),
}
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

def raw(write, tic, packet_type, data, verbose):
    type_title, type_decoder = TYPE_DICT[packet_type]
    decoded = type_decoder(data, tic, verbose)
    if decoded is not None:
        write('%s %s %s\n' % (tic_to_time(tic), type_title, decoded))

RXCMD_VBUS_HL_DICT = {
    0x0: 'OTG VBus off',
    0x4: 'OTG Session end',
    0x8: 'OTG Session start',
    0xc: 'OTG VBus on',
}
MIN_RESET_TIC = 2.5 * TIME_INITIAL_MULTIPLIER # 2.5 us
def _decodeSOF(data):
    assert len(data) == 3, data
    crc = data[2]
    return {
        'name': 'SOF',
        'frame': data[1] | ((crc & 0x7) << 8),
        'crc': crc >> 3,
    }
def _decodeSETUP(data):
    assert len(data) == 3, data
    crc = data[2]
    addr = data[1]
    return {
        'name': 'SETUP',
        'address': addr & 0x7f,
        'endpoint': (addr >> 7) | ((crc & 0x7) << 1),
        'crc': crc >> 3
    }
DATA_NAME = {
    0xc3: 'DATA0',
    0x4b: 'DATA1',
}
def _decodeDATA(data):
    return {
        'name': DATA_NAME[data[0]],
        'data': ' '.join('0x%02x' % x for x in data[1:-2]),
        'crc': data[-1] | (data[-2] << 8),
    }
PACKET_DECODER = {
    0x1: lambda _: {'name': 'OUT'},
    0x2: lambda _: {'name': 'ACK'},
    0x3: _decodeDATA,
    0x4: lambda _: {'name': 'PING'},
    0x5: _decodeSOF,
    0x6: lambda _: {'name': 'NYET'},
    0x7: lambda _: {'name': 'DATA2'},
    0x8: lambda _: {'name': 'SPLIT'},
    0x9: lambda _: {'name': 'IN'},
    0xa: lambda _: {'name': 'NAK'},
    0xb: _decodeDATA,
    0xc: lambda _: {'name': 'PRE/ERR'},
    0xd: _decodeSETUP,
    0xe: lambda _: {'name': 'STALL'},
    0xf: lambda _: {'name': 'MDATA'},
}
class Parser(object):
    # XXX: no brain was harmed in the writing of this class.
    # It's just a stupid first try to get a feeling on how to do it properly.
    def __init__(self, write, verbose):
        self._message_queue = IndexedList()
        self._before = []
        self.__write = write
        self._verbose = verbose
        self._type_dict = {
            TYPE_EVENT: self.event,
            TYPE_DATA: self.data,
            TYPE_RXCMD: self.rxcmd,
        }
        self._connected = False
        self._firstSOF = None
        self._lastSOF = None
        self._SOFcount = 1
        self._transaction = None
        self._transaction_data = None
        self._last_vbus = False

    def addBefore(self, tic, func, context):
        self._message_queue[tic] = None
        self._before.append((tic, func, context))

    def __call__(self, _, tic, packet_type, data, __):
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

    def _write(self, tic, message):
        queue = self._message_queue
        pop = queue.pop
        if message is None:
            pop(tic)
        else:
            if self._firstSOF is not None:
                sof_tic, first = self._firstSOF
                last = self._lastSOF
                self._lastSOF = self._firstSOF = None
                if last is None:
                    rendered = 'Start of frame %i' % (first, )
                else:
                    rendered = 'Start of frame %i -> %i (%i)' % (first, last,
                        self._SOFcount)
                self._SOFcount = 1
                queue[sof_tic] = tic_to_time(sof_tic) + ' ' + rendered + '\n'
            queue[tic] = tic_to_time(tic) + ' ' + message + '\n'
        write = self.__write
        getFirstValue = queue.getFirstValue
        while queue and getFirstValue() is not None:
            write(pop())

    def event(self, tic, data):
        caption = eventDecoder(data, None, False)
        if data == 0xf:
            self._connected = True
        if caption is not None:
            self._write(tic, caption)

    def data(self, tic, data):
        pass

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
            vbus = data & 0xc
            if data == 0x0c:
                # Maybe a reset, detect on next packet
                self.addBefore(tic, self._resetDetector, None)
            if vbus == self._last_vbus:
                return
            self._last_vbus = vbus
            rendered = RXCMD_VBUS_HL_DICT[vbus]
        self._write(tic, rendered)

    def _resetDetector(self, original_tic, _, tic, packet_type, data):
        if packet_type == TYPE_RXCMD and data & 0xc == 0xc:
            delta = tic - original_tic
            if delta > MIN_RESET_TIC:
                self._write(original_tic,
                    'Device reset (%s)' % (short_tic_to_time(delta), ))
            return False, not self._connected
        return True, False

    def _packetAgregator(self, original_tic, context, _, packet_type, data):
        if packet_type == TYPE_DATA:
            context.append(data)
            return True, False
        if packet_type == TYPE_EVENT:
            return True, False
        if packet_type == TYPE_RXCMD and not data & 0x10:
            pid = context[0]
            cannon_pid = pid & 0xf
            if cannon_pid != pid >> 4 ^ 0xf:
                self._write(original_tic, '(bad pid) 0x' + ' 0x'.join(
                    '%02x' % x for x in context))
                return False, True
            try:
                decoder = PACKET_DECODER[cannon_pid]
            except KeyError:
                self._write(original_tic, '(unk. data packet) 0x' +
                    ' 0x'.join('%02x' % x for x in context))
                return False, True
            decoded = decoder(context)
            # TODO: handle CRC5/CRC16 checks
            if self._verbose:
                decoded['pid'] = pid
                decoded = repr(decoded)
            else:
                if cannon_pid == 0x5:
                    if self._firstSOF is None:
                        self._firstSOF = (original_tic, decoded['frame'])
                        return False, True
                    else:
                        self._lastSOF = decoded['frame']
                        self._SOFcount += 1
                        decoded = None
                elif cannon_pid in (0x1, 0x9, 0xd):
                    assert self._transaction is None, self._transaction
                    self._transaction = (original_tic, decoded['name'])
                    if cannon_pid != 0xd:
                        return False, True
                    decoded = 'SETUP dev %i ep %i' % (decoded['address'],
                        decoded['endpoint'])
                elif cannon_pid in (0x2, 0xa, 0xe):
                    assert self._transaction is not None
                    transaction_tic, transaction_name = self._transaction
                    rendered = decoded['name'] + 'ed ' + transaction_name + \
                        ' transaction'
                    if self._transaction_data is not None:
                        rendered += ': ' + self._transaction_data
                    self._write(transaction_tic, rendered)
                    self._transaction = None
                    self._transaction_data = None
                    decoded = None
                elif cannon_pid in (0x3, 0xb):
                    # TODO: decode data
                    assert self._transaction_data is None, \
                        self._transaction_data
                    assert self._transaction is not None
                    self._transaction_data = decoded['data'] or \
                        '(no data)'
                    decoded = None
                else:
                    decoded = repr(decoded)
            self._write(original_tic, decoded)
        return False, True

def main(read, write, raw_write, verbose=False, emit_raw=True, follow=False):
    if emit_raw:
        emit = raw
    else:
        emit = Parser(write, verbose)
    if raw_write is None:
        raw_write = lambda x: None
    def read16():
        data = ''
        while len(data) < 2:
            data += read(2 - len(data))
            if not follow:
                break
            elif len(data) < 2:
                time.sleep(0.1)
        raw_write(data)
        try:
            return unpack('<H', data)[0]
        except struct_error:
            raise EOFError
    tic = 0
    while True:
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
        emit(write, tic, packet_type, data, verbose)
        if stop_printing:
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


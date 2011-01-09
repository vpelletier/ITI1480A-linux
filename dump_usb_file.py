#!/usr/bin/python
import sys
from struct import unpack, error as struct_error

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

def rxcmdDecoder(data, tic, verbose):
    # TODO: implement quiet (non-verbose) parsing
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

def eventDecoder(data, _, verbose):
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

write = sys.stdout.write
_read = sys.stdin.read

def main(read, write, verbose=False):
    def read16():
        try:
            return unpack('<H', read(2))[0]
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
                        continue
                    packet = read16()
                    assert packet & 0xff == 0, hex(packet)
                    data = packet >> 8
                else:
                    data = packet >> 8
            else:
                data = read16() & 0xff
        else:
            data = packet & 0xff
        tic += tic_count
        type_title, type_decoder = TYPE_DICT[packet_type]
        decoded = type_decoder(data, tic, verbose)
        if decoded is not None:
            write('%s %s %s\n' % (tic_to_time(tic), type_title, decoded))

if __name__ == '__main__':
    try:
        main(sys.stdin.read, sys.stdout.write)
    except EOFError:
        pass


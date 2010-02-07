#!/usr/bin/python
import sys

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

def rxcmdDecoder(data):
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
# TODO: identify present-but-empty values
EVENT_DICT = {
  0x00: '',
  0x01: '',
  0x02: '',
  0x03: '',
  0x04: '',
  0x05: '',
  0x06: '',
  0x07: '',
  0x08: '',
  0x09: '',
  0x0a: '',
  0x0b: 'LS device connection',
  0x0c: '',
  0x0d: '',
  0x0e: '',
  0x0f: 'FS device connection',
  0x12: '',
  0x13: '',
  0x14: '',
  0x15: 'Device chirp',
  0x16: '',
  0x17: '',
  0x18: 'Host chirp',
  0x19: '',
  0x1a: '',
  0x1c: '',
  0x1d: '',
  0x1e: '',
  0x1f: '',
  0x20: '',
  0x21: '',
  0x22: '',
  0x23: '',
  0x24: 'HS idle',
  0x25: '',
  0x26: '',
  0x27: '',
  0x28: '',
  0x29: '',
  0x2a: '',
  0x60: '',
  0x61: '',
  0x62: 'OTG Session request',
  0x63: '',
  0x64: '',
  0x68: '',
  0x69: 'OTG HNP (Host-role changed)',
  0xd0: 'Capture paused',
  0xd1: 'Capture resumed',
  0xe0: 'Capture started',
  0xf0: 'Capture stopped (fifo)',
  0xf1: 'Capture stopped (user)',
}

def eventDecoder(data):
    try:
        return EVENT_DICT[data]
    except KeyError:
        return '(unk. event 0x%02x)' % (data, )

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
    TYPE_DATA: ('Data ', lambda x: ''),
    TYPE_RXCMD: ('RxCmd', rxcmdDecoder),
}
# Packet length occupies the 2 next bits in first packet byte
LENGTH_SHIFT = 4
LENGTH_MASK = 0x3
# Number of tics contained in first packet byte
TIC_HEAD_MASK = 0xf

TIME_INITIAL_MULTIPLIER = 100.0 / 6 # 16.666...
TIME_DIVISOR_LIST = [1000, 1000, 1000, 60]

# XXX: this method is slow (1/3rd of total packet decoding time)
def tic_to_time(tic):
    magnitude_list = []
    append = magnitude_list.append
    tic = int(tic * TIME_INITIAL_MULTIPLIER)
    for divisor in TIME_DIVISOR_LIST:
        tic, value = divmod(tic, divisor)
        append(value)
    append(tic)
    magnitude_list.reverse()
    return '%03i:%02i.%03i\'%03i"%03in' % tuple(magnitude_list)

write = sys.stdout.write
_read = sys.stdin.read

def read_miss():
    global read
    global _read_buf
    try:
        _read_buf, result = _read(2)
    except ValueError:
        return ''
    read = read_hit
    return result

def read_hit():
    global read
    read = read_miss
    return _read_buf

read = read_miss 

tic = 0
while True:
    data = read()
    if not data:
        break
    data = ord(data)
    if not data:
        # Fast path for 0x00 packets (no delay, no data)
        continue
    packet_type = data >> TYPE_SHIFT

    if packet_type:
        write('%02x ' % (data, ))

    packet_len = (data >> LENGTH_SHIFT) & LENGTH_MASK
    if packet_type:
        write('   ' * (3 - packet_len))
    bit_offset = 4
    tic_count = data & TIC_HEAD_MASK
    while packet_len:
        data = ord(read())
        if packet_type:
            write('%02x ' % (data, ))
        tic_count |= data << bit_offset
        packet_len -= 1
        bit_offset += 8

    tic += tic_count

    if packet_type:
        type_title, type_decoder = TYPE_DICT[packet_type]
        data = ord(read())
        write('%s %s 0x%02x %s\n' % (tic_to_time(tic), type_title, data,
            type_decoder(data)
        ))


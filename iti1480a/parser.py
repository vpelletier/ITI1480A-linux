# Copyright (C) 2010-2015  Vincent Pelletier <plr.vincent@gmail.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
import os
from struct import unpack
from cStringIO import StringIO
from ply.yacc import yacc
from ply.lex import LexToken
import itertools
import sys
from ctypes import cast, POINTER, c_ushort
import platform

# Monkey-patch for ply.yacc defining startPush and push methods.
from . import incremental_yacc

PYPY = platform.python_implementation() == 'PyPy'
LITTLE_ENDIAN = sys.byteorder == 'little'
c_ushort_p = POINTER(c_ushort)

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
RXCMD_LINESTATE_MASK = 0x3
RXCMD_LINESTATE_SE0 = 0x0
RXCMD_LINESTATE_FS_J = 0x1
RXCMD_LINESTATE_FS_K = 0x2
RXCMD_LINESTATE_SE1 = 0x3
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
RXCMD_RX_ACTIVE = 0x10
RXCMD_HOST_DISCONNECT = 0x20
RXCMD_RX_ERROR = 0x30
RXCMD_EVENT_DICT = {
    RXCMD_RX_ACTIVE: 'RxActive',
    RXCMD_HOST_DISCONNECT: 'HostDisconnect',
    RXCMD_RX_ERROR: 'RxError RxActive',
}

# Event
EVENT_LS_DEVICE_CONNECTION = 0x0b
EVENT_FS_DEVICE_CONNECTION = 0x0f
EVENT_DEVICE_CHIRP = 0x15
EVENT_HOST_CHIRP = 0x18
EVENT_HS_IDLE = 0x24
EVENT_OTG_REQUEST = 0x62
EVENT_OTG_HNP = 0x69
EVENT_CAPTURE_PAUSED = 0xd0
EVENT_CAPTURE_RESUMED = 0xd1
EVENT_CAPTURE_STARTED = 0xe0
EVENT_CAPTURE_STOPPED_FIFO = 0xf0
EVENT_CAPTURE_STOPPED_USER = 0xf1

EVENT_DICT = {
    EVENT_LS_DEVICE_CONNECTION: 'LS device connection',
    EVENT_FS_DEVICE_CONNECTION: 'FS device connection',
    EVENT_DEVICE_CHIRP: 'Device chirp',
    EVENT_HOST_CHIRP: 'Host chirp',
    EVENT_HS_IDLE: 'HS idle',
    EVENT_OTG_REQUEST: 'OTG Session request',
    EVENT_OTG_HNP: 'OTG HNP (Host-role changed)',
    EVENT_CAPTURE_PAUSED: 'Capture paused',
    EVENT_CAPTURE_RESUMED: 'Capture resumed',
    EVENT_CAPTURE_STARTED: 'Capture started',
    EVENT_CAPTURE_STOPPED_FIFO: 'Capture stopped (fifo)',
    EVENT_CAPTURE_STOPPED_USER: 'Capture stopped (user)',
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
    - seconds & milliseconds
    - milli- & microseconds
    - micro- & nanoseconds
    """
    # XXX: is the seconds & milliseconds needed ? Actually, minutes & seconds
    # should not be of much use here...
    tic = int(tic * TIME_INITIAL_MULTIPLIER)
    tic, nano = divmod(tic, 1000)
    tic, micro = divmod(tic, 1000)
    tic, mili = divmod(tic, 1000)
    if tic > 60:
        return '%i m, %i s' % divmod(tic, 60)
    if tic:
        return '%i.%03i s' % (tic, mili)
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

# Duration of an SE0 state after which a reset is detected.
# See 7.1.7.5
MIN_RESET_TIC = 10000000 / TIME_INITIAL_MULTIPLIER # 10ms
MIN_RESET_FS_TO_CHIRP = 3000000 / TIME_INITIAL_MULTIPLIER # 3ms
MIN_LS_FS_RESET_TIC = 2500 / TIME_INITIAL_MULTIPLIER # 2.5 us
# See 7.1.13.2
MIN_LS_EOP_TIC = 670 / TIME_INITIAL_MULTIPLIER # 670 ns
MIN_FS_EOP_TIC = 82 / TIME_INITIAL_MULTIPLIER # 82 ns

# CRC5 & CRC16 computation: see 8.3.5
# Implementation detail: as python integers are not size-constrined,
# left-shifts are more complex (shift + mask) than right shifts
# (just shift). CRC remainder is right-shifted, and bit-swapped before
# returning. As a result, CRC polynomial is also byte-shifted (but once only,
# at module load).
def _swap5(value):
    return (
        ((value & 0x01) << 4) |
        ((value & 0x02) << 2) |
         (value & 0x04) |
        ((value & 0x08) >> 2) |
        ((value & 0x10) >> 4)
    )

def _swap16(value):
    value = ((value >> 1) & 0x5555) | ((value & 0x5555) << 1)
    value = ((value >> 2) & 0x3333) | ((value & 0x3333) << 2)
    value = ((value >> 4) & 0x0f0f) | ((value & 0x0f0f) << 4)
    value = ((value >> 8) & 0x00ff) | ((value & 0x00ff) << 8)
    return value

CRC5_POLYNOMIAL  = 0b00101
CRC5_RESIDUAL    = 0b01100
_CRC5_POLYNOMIAL = _swap5(CRC5_POLYNOMIAL)
CRC16_POLYNOMIAL = 0b1000000000000101
CRC16_RESIDUAL   = 0b1000000000001101
_CRC16_POLYNOMIAL = _swap16(CRC16_POLYNOMIAL)

def crc5(data):
    remainder = 0x1f
    for _, byte in data:
        for _ in xrange(8):
            xor_poly = (byte ^ remainder) & 1
            remainder >>= 1
            byte >>= 1
            if xor_poly:
                remainder ^= _CRC5_POLYNOMIAL
    return _swap5(remainder)

def crc16(data):
    remainder = 0xffff
    for _, byte in data:
        for _ in xrange(8):
            xor_poly = (byte ^ remainder) & 1
            remainder >>= 1
            byte >>= 1
            if xor_poly:
                remainder ^= _CRC16_POLYNOMIAL
    return _swap16(remainder)

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
    crc = data[2][1]
    addr = data[1][1]
    return {
        'name': TOKEN_NAME[data[0][1] & 0xf],
        'address': addr & 0x7f,
        'endpoint': (addr >> 7) | ((crc & 0x7) << 1),
        'crc': crc >> 3,
        'crc_error': crc5(data[1:]) != CRC5_RESIDUAL,
    }

DATA_NAME = {
    PID_DATA0: 'DATA0',
    PID_DATA1: 'DATA1',
    PID_DATA2: 'DATA2',
    PID_MDATA: 'MDATA',
}

def _decodeDATA(data):
    return {
        'name': DATA_NAME[data[0][1] & 0xf],
        'data': ''.join(chr(x[1]) for x in data[1:-2]),
        'crc': data[-1][1] | (data[-2][1] << 8),
        'crc_error': crc16(data[1:]) != CRC16_RESIDUAL,
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
    endpoint_type = data[3][1] & 0x6
    result = {
        'address': data[1][1] & 0x7,
        'port': data[2][1] & 0x7,
        'endpoint_type': SPLIT_ENDPOINT_TYPE_NAME[endpoint_type],
        'crc': data[3][1] >> 3,
        'crc_error': crc5(data[1:]) != CRC5_RESIDUAL,
    }
    speed = data[2][1] >> 3
    end = data[3][1] & 0x1
    if endpoint_type == SPLIT_ENDPOINT_TYPE_ISOCHRONOUS:
        result['continuation'] = SPLIT_ENDPOINT_CONTINUATION[(speed, end)]
    else:
        result['speed'] = speed
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
    crc = data[2][1]
    return {
        'name': 'SOF',
        'frame': data[1][1] | ((crc & 0x7) << 8),
        'crc': crc >> 3,
        'crc_error': crc5(data[1:]) != CRC5_RESIDUAL,
    }

MESSAGE_RAW = 0
MESSAGE_RESET = 1
MESSAGE_TRANSACTION = 2
MESSAGE_TRANSFER = 3
MESSAGE_TRANSACTION_ERROR = 4
MESSAGE_TRANSFER_ERROR = 5
MESSAGE_LS_EOP = 6
MESSAGE_FS_EOP = 7
MESSAGE_INCOMPLETE = 8
MESSAGE_FS_TO_CHIRP = 9

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

    @staticmethod
    def error(message, *args, **kw):
        # Actually, raising right away is more aggressive than what ply does...
        # But much simpler too.
        assert not (args and kw), (message, args, kw)
        raise Exception(message % (args or kw))

    critical = error

# TODO: merge BaseYaccAggregator and _BaseYaccAggregator ? (watch out for more
# API collision...)
class _BaseYaccAggregator(object):
    _start = None
    _error_type = None

    def __init__(self, to_next, to_top):
        """
        to_next (BaseAggregator.push)
        to_top (callable)
            Irregular parser production (ie, errors or warnings). Invocation
            details are subclass-dependant.
        """
        self._to_next = to_next
        self._to_top = to_top
        self._parser = parser = yacc(
            module=self,
            start=self._start,
            debug=bool(os.environ.get('ITI1480A_DEBUG')),
            debugfile=self.__class__.__name__ + '_parser.out',
            errorlog=_DummyLogger(),
            write_tables=False,
        )
        self.to_yacc = parser.push
        parser.startPush()

    def stop(self):
        self.to_yacc(None)

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
        error_tokens = [
            x.value for x in parser.symstack if isinstance(x, LexToken)
        ]
        if error_tokens:
            token_tic = self._getTokenTic(error_tokens[0])
        elif p is None:
            token_tic = None
        else:
            token_tic = self._getTokenTic(p.value)
        state = parser.statestack[-1]
        self._to_top(
            token_tic,
            self._error_type,
            (
                'Expected: %r in yacc state %r, got %r' % (
                    parser.action[state].keys(),
                    state,
                    p,
                ),
                error_tokens,
            ),
        )
        if error_tokens:
            # Restart parser and try again.
            if hasattr(parser, 'startPush'):
                parser.startPush()
            else:
                parser.restart()
            parser.errok()
            return p

    def p_empty(self, p):
        """empty :"""

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
        self._thread = thread = self._yacc_class(to_next.push, to_top)
        self.__to_yacc = thread.to_yacc

    def _to_yacc(self, token_type, token_data):
        token = LexToken()
        token.type = token_type
        token.value = (token_type, token_data)
        token.lineno = 0 # TODO: file offset
        token.lexpos = 0
        self.__to_yacc(token)

    def stop(self):
        self._thread.stop()
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
    _start = 'transfers'
    _error_type = MESSAGE_TRANSFER_ERROR

    @staticmethod
    def _getTokenTic(token):
        return int(token[1][0][1][0][0])

    @staticmethod
    def p_transfers(p):
        """transfers : transfer
                     | transfers transfer
                     | empty
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
        self._to_next(data[0][1][0][1][0][0], MESSAGE_TRANSFER, data)

    @staticmethod
    def p_out_handshake(p):
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

    @staticmethod
    def p_in_data(p):
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

    @staticmethod
    def p_out_data(p):
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
        self._to_yacc(
            ENDPOINT0_TRANSFER_TYPE_DICT[(
                TOKEN_TYPE_SETUP,
                data[offset][1][1][1] & 0x80,
            )],
            data,
        )

    def _setup(self, data):
        self.__setup(1, data)

    def _slowSetup(self, data):
        self.__setup(3, data)

    def _ping(self, data):
        self._to_yacc(
            ENDPOINT0_TRANSFER_TYPE_DICT[(
                TOKEN_TYPE_PING,
                data[-1][0],
            )],
            data,
        )

    def __data(self, offset, data):
        try:
            token_type = ENDPOINT0_TRANSFER_TYPE_DICT[(
                data[offset][0],
                data[-1][0],
            )]
        except KeyError:
            self._to_top(
                data[offset][1][0][0],
                MESSAGE_RAW,
                ('Unexpected ep0 transfer token', data),
            )
        else:
            self._to_yacc(token_type, data)

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
        try:
            decoded = decode(data[1 if data[0][0] == TOKEN_TYPE_PRE_ERR else 0])
        except IndexError:
            return
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

NEED_HANDSHAKE_LIST = ('SETUP', 'IN', 'OUT')
HANDSHAKE_LIST = ('ACK', 'NAK', 'STALL', 'NYET')

class _TransactionAggregator(_BaseYaccAggregator):
    tokens = TRANSACTION_TYPE_DICT.values() + [TOKEN_TYPE_SSPLIT, TOKEN_TYPE_CSPLIT]
    _start = 'transactions'
    _error_type = MESSAGE_TRANSACTION_ERROR

    @staticmethod
    def _getTokenTic(token):
        return token[1][0][0]

    @staticmethod
    def p_transactions(p):
        """transactions : transaction
                        | transactions transaction
                        | empty
        """

    def p_transaction(self, p):
        """transaction : SETUP DATA0 ACK
                       | SSPLIT token data handshake
                       | SSPLIT token data
                       | SSPLIT token
                       | SSPLIT token handshake
                       | CSPLIT token data
                       | CSPLIT token PRE_ERR
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

    def p_error(self, p):
        # XXX: relying on undocumented properties
        parser = self._parser
        error_tokens = [x.value for x in parser.symstack if isinstance(x, LexToken)]
        if error_tokens:
            self._to_next(error_tokens[0][1][0][0], MESSAGE_INCOMPLETE, error_tokens)
            # Restart parser and try again.
            if hasattr(parser, 'startPush'):
                parser.startPush()
            else:
                parser.restart()
            parser.errok()
            return p

    @staticmethod
    def p_token(p):
        """token : IN
                 | OUT
                 | SETUP
        """
        p[0] = p[1]

    @staticmethod
    def p_data(p):
        """data : low_speed_data
                | DATA2
                | MDATA
        """
        p[0] = p[1]

    @staticmethod
    def p_low_speed_data(p):
        """low_speed_data : DATA0
                          | DATA1
        """
        p[0] = p[1]

    @staticmethod
    def p_handshake(p):
        """handshake : low_speed_handshake
                     | NYET
        """
        p[0] = p[1]

    @staticmethod
    def p_low_speed_handshake(p):
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
                trans_type = (packet[1][1] & 0x80) and TOKEN_TYPE_CSPLIT or TOKEN_TYPE_SSPLIT
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
    _device_chirp = False
    _high_speed = False # Bus operating speed
    _high_speed_device = False # Device operating speed
    _full_speed_device = False # Device connected as FS
    _reset_start_high_speed = False

    def __init__(self, to_next, to_top, verbose=False):
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
        verbose (bool)
        """
        self._type_dict = {
            TYPE_EVENT: self._event,
            TYPE_DATA: self._data,
            TYPE_RXCMD: self._rxcmd,
        }
        self._to_next = to_next
        self._real_to_top = to_top
        self._data_list = []
        self._reset_queue = []
        self._verbose = verbose

    def _to_top(self, *args, **kw):
        if self._reset_start_tic is None:
            self._real_to_top(*args, **kw)
        else:
            self._reset_queue.append((args, kw))

    def push(self, tic, packet_type, data):
        """
        tic (int)
        packet_type
            One of TYPE_EVENT, TYPE_RXCMD, TYPE_DATA
        data (int)
            Byte.
        """
        # TODO: recognise low-speed keep-alive.
        if self._reset_start_tic is not None and packet_type != TYPE_EVENT and (
                    packet_type != TYPE_RXCMD or
                    data & (
                        RXCMD_EVENT_MASK | RXCMD_LINESTATE_MASK
                    ) != RXCMD_LINESTATE_SE0
                ):
            duration = tic - self._reset_start_tic
            if duration >= MIN_RESET_FS_TO_CHIRP and \
                    self._full_speed_device:
                se0_type = MESSAGE_FS_TO_CHIRP
            elif duration >= MIN_RESET_TIC:
                se0_type = MESSAGE_RESET
            elif duration >= MIN_LS_FS_RESET_TIC and \
                    not self._high_speed_device:
                se0_type = MESSAGE_RESET
            elif duration >= MIN_LS_EOP_TIC:
                se0_type = MESSAGE_LS_EOP
            elif duration >= MIN_FS_EOP_TIC:
                se0_type = MESSAGE_FS_EOP
            else:
                se0_type = None
            if se0_type is None:
                pass
            elif se0_type != MESSAGE_RESET or \
                    not self._reset_start_high_speed or \
                    not self._high_speed:
                self._real_to_top(self._reset_start_tic, se0_type, duration)
            if self._reset_queue:
                for args, kw in self._reset_queue:
                    self._real_to_top(*args, **kw)
                del self._reset_queue[:]
            self._reset_start_tic = None
        self._type_dict[packet_type](tic, data)

    def stop(self):
        # TODO: flush any pending reset ? requires knowing last tic before
        # stop was called
        if self._data_list:
            self._to_next.push(self._data_list)
            self._data_list = []
        self._to_next.stop()

    def _event(self, tic, data):
        try:
            caption = EVENT_DICT[data]
        except KeyError:
            if self._verbose:
                self._to_top(
                    tic,
                    MESSAGE_RAW,
                    '(unknown event 0x%02x)' % (data, ),
                )
        else:
            self._to_top(tic, MESSAGE_RAW, caption)
        if data == EVENT_FS_DEVICE_CONNECTION:
            self._full_speed_device = True
            self._connected = True
        elif data == EVENT_LS_DEVICE_CONNECTION:
            self._connected = True
        elif data == EVENT_DEVICE_CHIRP:
            self._device_chirp = True
        elif data == EVENT_HOST_CHIRP and self._device_chirp:
            self._high_speed_device = True
            self._high_speed = True
        elif data == EVENT_HS_IDLE:
            self._high_speed = False
        elif data in (EVENT_CAPTURE_STOPPED_FIFO, EVENT_CAPTURE_STOPPED_USER):
            raise ParsingDone

    def _data(self, tic, data):
        assert self._rxactive
        self._data_list.append((tic, data))

    def _rxcmd(self, tic, data):
        # TODO:
        # - RxError
        rxactive = data & RXCMD_RX_ACTIVE
        if self._rxactive and not rxactive and self._data_list:
            self._to_next.push(self._data_list)
            self._data_list = []
        self._rxactive = rxactive
        if data & RXCMD_HOST_DISCONNECT and self._connected:
            rendered = 'Device disconnected'
            self._connected = False
            self._full_speed_device = False
            self._device_chirp = False
            self._high_speed_device = False
            self._high_speed = False
        else:
            if self._reset_start_tic is None and \
                    data & RXCMD_LINESTATE_MASK == RXCMD_LINESTATE_SE0:
                # Maybe a reset, detect on next data
                self._reset_start_tic = tic
                self._reset_start_high_speed = self._high_speed
            vbus = data & RXCMD_VBUS_MASK
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
        self._remain = ()
        self._out = out
        self._tic = 0

    def push(self, data):
        """
        data (string)
            File chunk to process.
        """
        if len(data) % 2:
            raise ValueError('data len must be even')
        out = self._out.push
        tic = self._tic
        if LITTLE_ENDIAN and not PYPY:
            data_short_list = cast(data, c_ushort_p)
            reader = (data_short_list[x] for x in xrange(len(data) / 2))
        else:
            read = StringIO(data).read
            reader = (
                unpack('<H', read(2))[0]
                for x in xrange(0, len(data) - 1, 2)
            )
        next_data = itertools.chain(self._remain, reader).next
        try:
            while True:
                try:
                    p1 = next_data()
                except StopIteration:
                    self._remain = ()
                    break
                head = p1 >> 8
                packet_type = head >> TYPE_SHIFT
                packet_len = (head >> LENGTH_SHIFT) & LENGTH_MASK
                tic_count = head & TIC_HEAD_MASK
                if packet_len:
                    tic_count |= (p1 & 0xff) << 4
                    if packet_len > 1:
                        try:
                            p2 = next_data()
                        except StopIteration:
                            self._remain = p1,
                            break
                        tic_count |= (p2 & 0xff00) << 4
                        if packet_len > 2:
                            tic_count |= (p2 & 0xff) << 20
                            if packet_type == TYPE_TIME_DELTA:
                                tic += tic_count
                                continue
                            try:
                                p3 = next_data()
                            except StopIteration:
                                self._remain = p1, p2
                                break
                            assert p3 & 0xff == 0, hex(p3)
                            payload = p3 >> 8
                        else:
                            payload = p2 & 0xff
                    else:
                        try:
                            payload = next_data()
                        except StopIteration:
                            self._remain = p1,
                            break
                        assert payload & 0xff == 0, hex(payload)
                        payload >>= 8
                else:
                    payload = p1 & 0xff
                tic += tic_count
                out(tic, packet_type, payload)
        finally:
            self._tic = tic

    def stop(self):
        self._out.stop()


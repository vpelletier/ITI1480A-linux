#!/usr/bin/env python
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
import sys
import os
import usb1
from struct import pack, unpack
import time
import signal
import errno

VENDOR_ID = 0x16C0
DEVICE_ID = 0x07A9

def getDeviceHandle(context, vendor_id, device_id, usb_device=None):
    if usb_device is None:
        return context.openByVendorIDAndProductID(vendor_id, device_id)
    bus_number, device_address = usb_device
    for device in context.getDeviceList():
        if bus_number != device.getBusNumber() \
              or device_address != device.getDeviceAddress():
            continue
        if (device.getVendorID() == vendor_id and
            device.getProductID() == device_id):
            return device.open()
        raise ValueError(
            'Device at %03i.%03i is not of expected type: '
            '%04x.%04x, %04x.%04x expected' %
            usb_device + (vendor_id, device_id),
        )

class BaseUSBAnalyzer(object):
    def __init__(self, usb_handle):
        self._handle = usb_handle

    def sendFirmware(self, firmware_file):
        read = firmware_file.read
        write = self.writeCommand

        write(self.COMMAND_FPGA, self.COMMAND_FPGA_CONFIGURE_START)
        try:
            # Empty device FIFO, discarding data.
            self._handle.bulkRead(2, 2048, 10)
            self._handle.bulkRead(2, 2048, 10)
        except usb1.USBErrorTimeout:
            pass
        else:
            raise Exception('Read 2k, EP2 FIFO still not empty')
        while True:
            conf_data = read(self.COMMAND_DATA_LEN)
            if not conf_data:
                break
            write(
                self.COMMAND_FPGA,
                self.COMMAND_FPGA_CONFIGURE_WRITE,
                conf_data,
            )
        write(self.COMMAND_FPGA, self.COMMAND_FPGA_CONFIGURE_STOP)

    def stopCapture(self):
        self.writeCommand(self.COMMAND_STOP)

    def getStatus(self):
        return ord(self.readCommand(1, self.COMMAND_STATUS))

    def pauseCapture(self):
        self.writeCommand(self.COMMAND_PAUSE, self.COMMAND_PAUSE_PAUSE)

    def continueCapture(self):
        self.writeCommand(self.COMMAND_PAUSE, self.COMMAND_PAUSE_CONTINUE)

class CompatibleUSBAnalyzer(BaseUSBAnalyzer):
    COMMAND_DATA_LEN = 61
    COMMAND_PAUSE = '\x03'
    COMMAND_PAUSE_CONTINUE = '\x00'
    COMMAND_PAUSE_PAUSE = '\x01'
    COMMAND_STATUS = '\x02'
    COMMAND_STOP = '\x01'
    COMMAND_FPGA = '\x00'
    COMMAND_FPGA_CONFIGURE_START = '\x00'
    COMMAND_FPGA_CONFIGURE_WRITE = '\x01'
    COMMAND_FPGA_CONFIGURE_STOP = '\x02'

    def writeCommand(self, command, sub_command='\x00', data=''):
        data_len = len(data)
        if data_len < self.COMMAND_DATA_LEN:
            data = data + '\x00' * (self.COMMAND_DATA_LEN - data_len)
        to_write = ''.join((command, sub_command, data, pack('B', data_len)))
        assert len(to_write) == 64, repr(to_write)
        self._handle.bulkWrite(1, to_write)

    def readCommand(self, length, command, sub_command='\x00'):
        self.writeCommand(command, sub_command)
        return self._handle.bulkRead(1, 64)[:length]

class CompliantUSBAnalyzer(BaseUSBAnalyzer):
    COMMAND_DATA_LEN = 0x1000 # XXX: Why error on larger values ?
    VENDOR_COMMAND = 0x10
    COMMAND_PAUSE = 3
    COMMAND_PAUSE_CONTINUE = 0
    COMMAND_PAUSE_PAUSE = 1
    COMMAND_STATUS = 2
    COMMAND_STOP = 1
    COMMAND_FPGA = 0
    COMMAND_FPGA_CONFIGURE_START = 0
    COMMAND_FPGA_CONFIGURE_WRITE = 1
    COMMAND_FPGA_CONFIGURE_STOP = 2
    COMMAND_MEMORY = 0xff
    COMMAND_MEMORY_INTERNAL = 0
    COMMAND_MEMORY_EXTERNAL = 1
    COMMAND_MEMORY_CODE = 2

    def writeCommand(self, command, sub_command=0, data='', index=0):
        self._handle.controlWrite(
            usb1.TYPE_VENDOR | usb1.RECIPIENT_DEVICE,
            self.VENDOR_COMMAND,
            (command << 8) | sub_command,
            index,
            data,
        )

    def readCommand(self, length, command, sub_command=0, index=0):
        return self._handle.controlRead(
            usb1.TYPE_VENDOR | usb1.RECIPIENT_DEVICE,
            self.VENDOR_COMMAND,
            (command << 8) | sub_command,
            index,
            length,
        )

    def _dumpList(self, offset, name_list):
        for name, value in zip(name_list, self.xpeek(offset, len(name_list))):
            if name:
                print >>sys.stderr,'%20s: %02x' % (name, ord(value))

    def dumpIRQ(self):
        self._dumpList(0xe650, (
            'EP2FIFOIE',
            'EP2FIFOIRQ',
            'EP4FIFOIE',
            'EP4FIFOIRQ',
            'EP6FIFOIE',
            'EP6FIFOIRQ',
            'EP8FIFOIE',
            'EP8FIFOIRQ',
            'IBNIE',
            'IBNIRQ',
            'NAKIE',
            'NAKIRQ',
            'USBIE',
            'USBIRQ',
            'EPIE',
            'EPIRQ',
            'GPIFIE',
            'GPIFIRQ',
            'USBERRIE',
            'USBERRIRQ',
            'ERRCNTLIM',
            'CLRERRCNT',
            'INT2IVEC',
            'INT4IVEC',
            'INTSETUP',
        ))

    def dumpUSBControl(self):
        self._dumpList(0xe680, (
            'USBCS',
            'SUSPEND',
            'WAKEUPCS',
            'TOGCTL',
            'USBFRAMEH',
            'USBFRAMEL',
            'MICROFRAME',
            'FNADDR',
        ))
        self._dumpList(0xe68a, (
            'EP0BCH',
            'EP0BCL',
        ))
        self._dumpList(0xe68d, (
            'EP1OUTBC',
        ))
        self._dumpList(0xe68f, (
            'EP1INBC',
            'EP2BCH',
            'EP2BCL',
        ))
        self._dumpList(0xe694, (
            'EP4BCH',
            'EP4BCL',
        ))
        self._dumpList(0xe698, (
            'EP6BCH',
            'EP6BCL',
        ))
        self._dumpList(0xe69c, (
            'EP8BCH',
            'EP8BCL',
        ))
        self._dumpList(0xe6a0, (
            'EP0CS',
            'EP1OUTCS',
            'EP1INCS',
            'EP2CS',
            'EP4CS',
            'EP6CS',
            'EP8CS',
            'EP2FIFOFLGS',
            'EP4FIFOFLGS',
            'EP6FIFOFLGS',
            'EP8FIFOFLGS',
            'EP2FIFOBCH',
            'EP2FIFOBCL',
            'EP4FIFOBCH',
            'EP4FIFOBCL',
            'EP6FIFOBCH',
            'EP6FIFOBCL',
            'EP8FIFOBCH',
            'EP8FIFOBCL',
            'SUDPTRH',
            'SUDPTRL',
            'SUDPTRCTL',
        ))

    def dumpUSBAV(self):
        av_name_list = [
            'SUDAV_ISR',
            'SOF_ISR',
            'SUTOK_ISR',
            'SUSPEND_ISR',
            'USBRESET_ISR',
            'HISPEED_ISR',
            'EP0ACK_ISR',
            'SPARE_ISR',
            'EP0IN _ISR',
            'EP0OUT_ISR',
            'EP1IN _ISR',
            'EP1OUT_ISR',
            'EP2_ISR',
            'EP4_ISR',
            'EP6_ISR',
            'EP8_ISR',
            'IBN_ISR',
            'SPARE_ISR',
            'EP0PING_ISR',
            'EP1PING_ISR',
            'EP2PING_ISR',
            'EP4PING_ISR',
            'EP6PING_ISR',
            'EP8PING_ISR',
            'ERRLIMIT_ISR',
            'SPARE_ISR',
            'SPARE_ISR',
            'SPARE_ISR',
            'EP2ISOERR_ISR',
            'EP2ISOERR_ISR',
            'EP2ISOERR_ISR',
            'EP2ISOERR_ISR',
        ]
        code_len = len(av_name_list) * 4
        offset = 0x3f00
        data = ''
        while len(data) < code_len:
            data += self.cpeek(offset + len(data), min(code_len - len(data), 64))
        for index, name in enumerate(av_name_list):
            ljmp, addr, pad = unpack('>BHB', data[index * 4:(index + 1) * 4])
            if name is None:
                continue
            assert ljmp == 2, ljmp
            print >>sys.stderr,'%20s=0x%04x' % (name, addr)

    def peek(self, address, length=1):
        return self.readCommand(length, self.COMMAND_MEMORY, self.COMMAND_MEMORY_INTERNAL, address)

    def xpeek(self, address, length=1):
        return self.readCommand(length, self.COMMAND_MEMORY, self.COMMAND_MEMORY_EXTERNAL, address)

    def cpeek(self, address, length=1):
        return self.readCommand(length, self.COMMAND_MEMORY, self.COMMAND_MEMORY_CODE, address)

    def poke(self, address, data):
        self.writeCommand(self.COMMAND_MEMORY, self.COMMAND_MEMORY_INTERNAL, data, address)

    def xpoke(self, address, data):
        self.writeCommand(self.COMMAND_MEMORY, self.COMMAND_MEMORY_EXTERNAL, data, address)

def USBAnalyzer(handle):
    # Free Software firmware exposes an incompatible, standard-compliant
    # configuration.
    if handle.getASCIIStringDescriptor(
                handle.getDevice()[0].getDescriptor()
            ) == 'Standard-Compliant':
        return CompliantUSBAnalyzer(handle)
    return CompatibleUSBAnalyzer(handle)

class TransferDumpCallback(object):
    __slots__ = (
        'write',
        'transfer_end_count',
        'capture_size',
        'next_measure',
        'last_measure',
        'verbose',
        'stop_condition',
        '__call__',
    )

    def __init__(self, stream, verbose=False):
        self.write = stream.write
        self.transfer_end_count = 0
        self.capture_size = 0
        self.next_measure = time.time()
        self.last_measure = (None, None)
        self.verbose = verbose
        self.stop_condition = (
            '\xf0\x41', '\xf1\x41',
            '\x41\xf0', '\x41\xf1',
        )
        self.__call__ = self.real_call

    def noop_call(self, transfer):
        return False

    def real_call(self, transfer):
        size = transfer.getActualLength()
        if not size:
            return True
        data = transfer.getBuffer()[:size]
        if data[-2:] in self.stop_condition:
            self.transfer_end_count += 1
            result = self.transfer_end_count < 2
            if not result:
                self.__call__ = self.noop_call
        else:
            result = True
            self.transfer_end_count = 0
        if self.verbose:
            self.capture_size += size
            cap_size = self.capture_size
            now = time.time()
            if now > self.next_measure:
                self.next_measure = now + 2
                last_time, last_size = self.last_measure
                self.last_measure = (now, cap_size)
                if last_size is not None:
                    sdelta = cap_size - last_size
                    if sdelta:
                        speed = sdelta / (now - last_time)
                        if speed > 1024:
                            speed /= 1024.
                            if speed > 1024:
                                speed /= 1024
                                suffix = 'M'
                            else:
                                suffix = 'k'
                        else:
                            suffix = ''
                        sys.stderr.write('\nSpeed: %.02f %sB/s\n' % (
                            speed, suffix))
                sys.stderr.write('Capture size: %i\r' % (cap_size, ))
        try:
            self.write(data)
        except IOError, exc:
            if exc.errno != errno.EPIPE:
                raise
            result = False
        return result

def main():
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option(
        '-f', '--firmware', default='/lib/firmware/ITI1480A.rbf',
        help='Path to firmware file to upload',
    )
    parser.add_option(
        '-d', '--device',
        help='USB device to use, in "bus.dev" format',
    )
    parser.add_option(
        '-o', '--out',
        help='File to write dump data to. Default: stdout',
    )
    parser.add_option(
        '-v', '--verbose', action='store_true',
        help='Print informative messages to stderr',
    )
    (options, args) = parser.parse_args()
    if options.firmware is None:
        parser.print_help(sys.stderr)
        sys.exit(1)
    if options.device is None:
        usb_device = None
    else:
        usb_device = options.device.split('.')
        assert len(usb_device) == 2
        usb_device = (int(usb_device[0]), int(usb_device[1]))
    if options.out is None:
        out_file = os.fdopen(sys.stdout.fileno(), 'w', 0)
    else:
        out_file = open(options.out, 'wb', 0)
    verbose = options.verbose
    context = usb1.LibUSBContext()
    handle = getDeviceHandle(context, VENDOR_ID, DEVICE_ID, usb_device)
    if handle is None:
        print >>sys.stderr, 'ITI1480A USB Analyzer not found'
        sys.exit(1)
    handle.claimInterface(0)
    analyzer = USBAnalyzer(handle)
    analyzer.sendFirmware(open(options.firmware, 'rb'))

    # Call queue: process received signals synchronously.
    # Asynchronous processing is tricky because capture stop and pause need to
    # communicate with the analyzer, and complex tricks are needed when libusb
    # event handling happens "in parallel" (handleEvents + sighandler triggered
    # at the wrong time).
    call_queue = []
    def exit():
        if verbose:
            sys.stderr.write('\nExiting...\n')
        analyzer.stopCapture()
    def pause():
        analyzer.pauseCapture()
        if verbose:
            sys.stderr.write('\nCapture paused')
        os.kill(os.getpid(), signal.SIGSTOP)
        analyzer.continueCapture()
        if verbose:
            sys.stderr.write('Capture resumed\n')

    # Install signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda sig, stack: call_queue.append(exit))
    signal.signal(signal.SIGTSTP, lambda sig, stack: call_queue.append(pause))

    usb_file_data_reader = usb1.USBTransferHelper()
    transfer_dump_callback = TransferDumpCallback(out_file, verbose=verbose)
    usb_file_data_reader.setEventCallback(
        usb1.TRANSFER_COMPLETED,
        transfer_dump_callback,
    )

    reader_list = []
    append = reader_list.append
    for _ in xrange(64):
        data_reader = handle.getTransfer()
        data_reader.setBulk(
            0x82,
            0x8000,
            callback=usb_file_data_reader,
        )
        data_reader.submit()
        append(data_reader)

    if verbose:
        sys.stderr.write(
            'Capture started\n'
            'SIGTSTP (^Z) to pause capture (signals the pause to analyser)\n'
            'SIGCONT (fg) to unpause\n'
            'SIGINT (^C) / SIGTERM to gracefuly exit\n'
        )

    try:
        while any(x.isSubmitted() for x in reader_list):
            try:
                context.handleEvents()
            except usb1.USBErrorInterrupted:
                pass
            while call_queue:
                call_queue.pop(0)()
    finally:
        handle.releaseInterface(0)

if __name__ == '__main__':
    main()


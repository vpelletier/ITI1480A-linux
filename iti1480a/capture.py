#!/usr/bin/python
import sys
import os
import usb1
import libusb1
from struct import pack
import time
import signal
import errno

VENDOR_ID = 0x16C0
DEVICE_ID = 0x07A9

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

def getDeviceHandle(context, vendor_id, device_id, usb_device=None):
    if usb_device is None:
        handle = context.openByVendorIDAndProductID(vendor_id, device_id)
    else:
        handle = None
        bus_number, device_address = usb_device
        for device in context.getDeviceList():
            if bus_number != device.getBusNumber() \
                  or device_address != device.getDeviceAddress():
                continue
            else:
                if (device.getVendorID(), device.getProductID()) == (
                        vendor_id, device_id):
                    handle = device.open()
                    break
                else:
                    raise ValueError(
                        'Device at %03i.%03i is not of expected type: '
                        '%04x.%04x, %04x.%04x expected' %
                        usb_device + (vendor_id, device_id),
                    )
    return handle

class USBAnalyzer(object):
    def __init__(self, usb_handle):
        self._handle = usb_handle

    def writeCommand(self, command, sub_command='\x00', data=''):
        data_len = len(data)
        if data_len < COMMAND_DATA_LEN:
            data = data + '\x00' * (COMMAND_DATA_LEN - data_len)
        to_write = ''.join((command, sub_command, data, pack('B', data_len)))
        assert len(to_write) == 64, repr(to_write)
        self._handle.bulkWrite(1, to_write)

    def readResult(self, length):
        return self._handle.bulkRead(1, 64)[:length]

    def sendFirmware(self, firmware_file):
        read = firmware_file.read
        write = self.writeCommand

        write(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_START)
        try:
            # Empty device FIFO, discarding data.
            self._handle.bulkRead(2, 2048, 10)
            self._handle.bulkRead(2, 2048, 10)
        except libusb1.USBError, exc:
            if exc.value != libusb1.LIBUSB_ERROR_TIMEOUT:
                raise
        else:
            raise Exception('Read 2k, EP2 FIFO still not empty')
        while True:
            conf_data = read(COMMAND_DATA_LEN)
            if not conf_data:
                break
            write(
                COMMAND_FPGA,
                COMMAND_FPGA_CONFIGURE_WRITE,
                conf_data,
            )
        write(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_STOP)

    def stopCapture(self):
        self.writeCommand(COMMAND_STOP)

    def getStatus(self):
        self.writeCommand(COMMAND_STATUS)
        return ord(self.readResult(1))

    def pauseCapture(self):
        self.writeCommand(COMMAND_PAUSE, COMMAND_PAUSE_PAUSE)

    def continueCapture(self):
        self.writeCommand(COMMAND_PAUSE, COMMAND_PAUSE_CONTINUE)

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

class terminatingSignalHandler(object):
    def __init__(self, analyzer, verbose=False):
        self._analyzer = analyzer
        self._verbose = verbose

    def __call__(self, sig, stack):
        if self._verbose:
            sys.stderr.write('\nExiting...\n')
        self._analyzer.stopCapture()

class pausingSignalHandler(object):
    def __init__(self, analyzer, verbose=False):
        self._analyzer = analyzer
        self._verbose = verbose

    def __call__(self, sig, stack):
        self._analyzer.pauseCapture()
        if self._verbose:
            sys.stderr.write('\nCapture paused')
        os.kill(os.getpid(), signal.SIGSTOP)

class resumingSignalHandler(object):
    def __init__(self, analyzer, verbose=False):
        self._analyzer = analyzer
        self._verbose = verbose

    def __call__(self, sig, stack):
        self._analyzer.continueCapture()
        if self._verbose:
            sys.stderr.write('Capture resumed\n')

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
        print >>sys.stderr, 'Unable to find usb analyzer.'
        sys.exit(1)
    handle.claimInterface(0)
    analyzer = USBAnalyzer(handle)
    analyzer.sendFirmware(open(options.firmware, 'rb'))

    # Install signal handlers
    terminating_handler = terminatingSignalHandler(
        analyzer,
        verbose=verbose,
    )
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, terminating_handler)
    signal.signal(signal.SIGTSTP, pausingSignalHandler(
        analyzer,
        verbose=verbose,
    ))
    signal.signal(signal.SIGCONT, resumingSignalHandler(
        analyzer,
        verbose=verbose,
    ))

    usb_file_data_reader = usb1.USBTransferHelper()
    transfer_dump_callback = TransferDumpCallback(out_file, verbose=verbose)
    usb_file_data_reader.setEventCallback(
        libusb1.LIBUSB_TRANSFER_COMPLETED,
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
            'Capture started\nSIGTSTP (^Z) to pause capture '
            '(signals the pause to analyser)\nSIGCONT (fg) to unpause\n'
            'SIGINT (^C) / SIGTERM to gracefuly exit\n'
        )

    try:
        while any(x.isSubmitted() for x in reader_list):
            try:
                context.handleEvents()
            except libusb1.USBError, exc:
                if exc.value != libusb1.LIBUSB_ERROR_INTERRUPTED:
                    raise
    finally:
        handle.releaseInterface(0)

if __name__ == '__main__':
    main()


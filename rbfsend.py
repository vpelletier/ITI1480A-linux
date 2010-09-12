#!/usr/bin/python
import sys
import os
import usb1
import libusb1
import select
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
          raise ValueError, 'Device at %03i.%03i is not of expected type: ' \
            '%04x.%04x, %04x.%04x expected' % (usb_device + (
              vendor_id, device_id))
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

    self.writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_START)
    while True:
      conf_data = read(COMMAND_DATA_LEN)
      if not conf_data:
        break
      self.writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_WRITE,
        conf_data)
    self.writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_STOP)
    # Experimental duration: measured delay between COMMAND_FPGA_CONFIGURE_STOP
    # and async "read capture data" query, as sent by original software.
    # It seems that accessing the device too early confuses it.
    time.sleep(0.1)

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
  def __init__(self, stream, verbose=False):
    self.stream = stream
    self.transfer_end_count = 0
    self.capture_size = 0
    self.verbose = verbose

  def __call__(self, transfer):
    data = transfer.getBuffer()
    size = transfer.getActualLength()
    if len(data) > size:
      data = data[:size]
    if self.isEndOfTransfer(data):
      self.transfer_end_count += 1
      result = self.transfer_end_count < 2
    else:
      result = True
      self.transfer_end_count = 0
      self.capture_size += size
      if self.verbose:
        sys.stderr.write('Capture size: %i\r' % (self.capture_size, ))
      self.stream.write(data)
    return result

  def isEndOfTransfer(self, data):
    return self.isEndOfTransferMarker(data, 0) or \
           self.isEndOfTransferMarker(data, 1)

  def isEndOfTransferMarker(self, data, offset):
    return ord(data[offset]) & 0xf0 == 0xf0 and ord(data[offset + 1]) == 0x41

def transferTimeoutHandler(transfer):
  return True

class Terminate(Exception):
  pass

def terminatingSignalHandler(sig, stack):
  raise Terminate(sig)

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

def main(
      firmware_path,
      usb_device=None,
      out_file=None,
      verbose=False,
    ):
  context = usb1.LibUSBContext()
  handle = getDeviceHandle(context, VENDOR_ID, DEVICE_ID, usb_device)
  if handle is None:
    raise ValueError, 'Unable to find usb analyzer.'
  handle.claimInterface(0)
  analyzer = USBAnalyzer(handle)
  analyzer.sendFirmware(open(firmware_path, 'rb'))

  # Install signal handlers
  for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, terminatingSignalHandler)
  signal.signal(signal.SIGTSTP, pausingSignalHandler(analyzer,
    verbose=verbose))
  signal.signal(signal.SIGCONT, resumingSignalHandler(analyzer,
    verbose=verbose))

  poller = usb1.USBPoller(context, select.poll())

  data_reader = handle.getTransfer()
  data_reader.setBulk(
    0x82,
    0x200,
  )
  usb_file_data_reader = usb1.USBTransferHelper(data_reader)
  usb_file_data_reader.setEventCallback(libusb1.LIBUSB_TRANSFER_COMPLETED,
    TransferDumpCallback(out_file, verbose=verbose))
  usb_file_data_reader.setEventCallback(libusb1.LIBUSB_TRANSFER_TIMED_OUT,
    transferTimeoutHandler)
  usb_file_data_reader.submit()

  if verbose:
    sys.stderr.write('Capture started\n')

  try:
    try:
      while usb_file_data_reader.isSubmited():
        try:
          poller.poll()
        except select.error, (select_errno, error_text):
          if select_errno != errno.EINTR:
            raise
    finally:
      if verbose:
        sys.stderr.write('\nExiting...\n')
      analyzer.stopCapture()
      while usb_file_data_reader.isSubmited():
          poller.poll()
      handle.releaseInterface(0)
  except Terminate:
    pass

if __name__ == '__main__':
  from optparse import OptionParser

  parser = OptionParser()
  parser.add_option('-f', '--firmware',
    help='Path to firmware file to upload. (required)')
  parser.add_option('-d', '--device',
    help='USB device to use, in "bus.dev" format')
  parser.add_option('-o', '--out',
    help='File to write dump data to. Default: stdout')
  parser.add_option('-v', '--verbose', action='store_true',
    help='Print informative messages to stderr')
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
    out_file = sys.stdout
  else:
    out_file = open(options.out, 'wb')
  main(
    firmware_path=options.firmware,
    usb_device=usb_device,
    out_file=out_file,
    verbose=options.verbose,
  )


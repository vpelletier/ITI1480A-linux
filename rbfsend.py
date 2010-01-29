#!/usr/bin/python
import sys
import os
import usb1
import libusb1
import select
from struct import pack
import time

VENDOR_ID = 0x16C0
DEVICE_ID = 0x07A9

COMMAND_DATA_LEN = 61
COMMAND_STATUS = '\x02'
COMMAND_STOP = '\x01'
COMMAND_FPGA = '\x00'
COMMAND_FPGA_CONFIGURE_START = '\x00'
COMMAND_FPGA_CONFIGURE_WRITE = '\x01'
COMMAND_FPGA_CONFIGURE_STOP = '\x02'

def getDeviceHandle(context, usb_device=None):
  if usb_device is None:
    handle = context.openByVendorIDAndProductID(VENDOR_ID, DEVICE_ID)
  else:
    handle = None
    bus_number, device_address = usb_device
    for device in context.getDeviceList():
      if bus_number != device.getBusNumber() \
         or device_address != device.getDeviceAddress():
        continue
      else:
        if (device.getVendorID(), device.getProductID()) == (
           VENDOR_ID, DEVICE_ID):
          handle = device.open()
          break
        else:
          raise ValueError, 'Device at %03i.%03i is not a known analyzer ' \
            'device.' % usb_device
  if handle is None:
    raise ValueError, 'Unable to find usb analyzer.'
  return handle

def writeCommand(usb_handle, command, sub_command='\x00', data=''):
  data_len = len(data)
  if data_len < COMMAND_DATA_LEN:
    data = data + '\x00' * (COMMAND_DATA_LEN - data_len)
  to_write = ''.join((command, sub_command, data, pack('B', data_len)))
  assert len(to_write) == 64, repr(to_write)
  usb_handle.bulkWrite(1, to_write)

def readResult(usb_handle, length):
  return usb_handle.bulkRead(1, 64)[:length]

def sendFirmware(firmware_file, usb_handle):
  read = firmware_file.read

  writeCommand(usb_handle, COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_START)
  while True:
    conf_data = read(COMMAND_DATA_LEN)
    if not conf_data:
      break
    writeCommand(usb_handle, COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_WRITE,
      conf_data)
  writeCommand(usb_handle, COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_STOP)
  # Experimental duration: measured delay between COMMAND_FPGA_CONFIGURE_STOP
  # and async "read capture data" query, as sent by original software.
  # It seems that accessing the device too early confuses it.
  time.sleep(0.1)

def stopCapture(usb_handle):
  writeCommand(usb_handle, COMMAND_STOP)

def getStatus(usb_handle):
  writeCommand(usb_handle, COMMAND_STATUS)
  return ord(readResult(usb_handle, 1))

class TransferDumpCallback(object):
  def __init__(self, stream):
    self.stream = stream
    self.transfer_end_count = 0
    self.capture_size = 0

  def __call__(self, transfer, data):
    size = transfer.actual_length
    if len(data) > size:
      data = data[:size]
    if self.isEndOfTransfer(data):
      self.transfer_end_count += 1
      result = self.transfer_end_count < 2
    else:
      result = True
      self.transfer_end_count = 0
      self.capture_size += size
      sys.stderr.write('Capture size: %i\r' % (self.capture_size, ))
      self.stream.write(data)
    return result

  def isEndOfTransfer(self, data):
    return self.isEndOfTransferMarker(data, 0) or \
           self.isEndOfTransferMarker(data, 1)

  def isEndOfTransferMarker(self, data, offset):
    return ord(data[offset]) & 0xf0 == 0xf0 and ord(data[offset + 1]) == 0x41

def transferTimeoutHandler(transfer, data):
  return True

def main(
      firmware_path,
      usb_device=None,
      out_file=None,
    ):
  context = usb1.LibUSBContext()
  handle = getDeviceHandle(context, usb_device)
  handle.claimInterface(0)
  sendFirmware(open(firmware_path, 'rb'), handle)

  poller = usb1.USBPoller(context, select.poll())

  usb_file_data_reader = usb1.USBAsyncBulkReader(
    handle,
    0x82,
    0x200,
    timeout=1000,
  )
  usb_file_data_reader.setEventCallback(libusb1.LIBUSB_TRANSFER_COMPLETED,
    TransferDumpCallback(out_file))
  usb_file_data_reader.setEventCallback(libusb1.LIBUSB_TRANSFER_TIMED_OUT,
    transferTimeoutHandler)
  usb_file_data_reader.submit()

  try:
    while usb_file_data_reader.isSubmited():
      poller.poll()
  finally:
    sys.stderr.write('\nExiting...\n')
    stopCapture(handle)
    while usb_file_data_reader.isSubmited():
        poller.poll()
    handle.releaseInterface(0)

if __name__ == '__main__':
  from optparse import OptionParser

  parser = OptionParser()
  parser.add_option('-f', '--firmware',
    help='Path to firmware file to upload. (required)')
  parser.add_option('-d', '--device',
    help='USB device to use, in "bus.dev" format')
  parser.add_option('-o', '--out',
    help='File to write dump data to. Default: stdout')
  (options, args) = parser.parse_args()
  if options.firmware is None:
    parser.print_help()
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
  )


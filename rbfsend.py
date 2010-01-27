#!/usr/bin/python
import sys
import os
import usb1
from struct import pack

VENDOR_ID = 0x16C0
DEVICE_ID = 0x07A9

COMMAND_DATA_LEN = 61
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

def sendFirmware(firmware_file, usb_handle):
  read = firmware_file.read

  def writeCommand(command, sub_command, data=''):
    data_len = len(data)
    if data_len < COMMAND_DATA_LEN:
      data = data + '\x00' * (COMMAND_DATA_LEN - data_len)
    to_write = ''.join((command, sub_command, data, pack('B', data_len)))
    assert len(to_write) == 64, repr(to_write)
    usb_handle.bulkWrite(1, to_write)

  writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_START)
  while True:
    conf_data = read(COMMAND_DATA_LEN)
    if not conf_data:
      break
    writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_WRITE, conf_data)
  writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_STOP)

def main(
      firmware_path,
      usb_device=None,
    ):
  context = usb1.LibUSBContext()
  handle = getDeviceHandle(context, usb_device)
  handle.claimInterface(0)
  sendFirmware(open(firmware_path, 'rb'), handle)

  import pdb; pdb.set_trace()

if __name__ == '__main__':
  from optparse import OptionParser

  parser = OptionParser()
  parser.add_option('-f', '--firmware',
    help='Path to firmware file to upload. (required)')
  parser.add_option('-d', '--device',
    help='USB device to use, in "bus.dev" format')
  (options, args) = parser.parse_args()
  if option.device is None:
  if options.firmware is None:
    parser.print_help()
    sys.exit(1)
    usb_device = None
  else:
    usb_device = option.device.split('.')
    assert len(usb_device) == 2
    usb_device = (int(usb_device[0]), int(usb_device[1]))
  main(
    firmware_path=options.firmware_path,
    usb_device=usb_device,
  )


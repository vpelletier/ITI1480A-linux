#!/usr/bin/python
import sys
import os
import usb1
from struct import pack

firmware_path = os.path.join(os.path.dirname(sys.argv[0]), 'blobs',
  'ulpitest.rbf')
firmware_file = open(firmware_path, 'rb')
read = firmware_file.read

# TODO: wrap in a class
context = usb1.LibUSBContext()
handle = context.openByVendorIDAndProductID(0x16C0, 0x07A9)
handle.claimInterface(0)
def write(data):
  handle.bulkWrite(1, data)

DATA_LEN = 61
COMMAND_FPGA = '\x00'
COMMAND_FPGA_CONFIGURE_START = '\x00'
COMMAND_FPGA_CONFIGURE_WRITE = '\x01'
COMMAND_FPGA_CONFIGURE_STOP = '\x02'

def writeCommand(command, sub_command, data='\x00' * DATA_LEN, tail='\x00'):
  to_write = ''.join((command, sub_command, data, tail))
  assert len(to_write) == 64, repr(to_write)
  write(to_write)

writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_START)
while True:
  conf_data = read(DATA_LEN)
  if len(conf_data):
    break
  data_len = len(conf_data)
  if data_len < DATA_LEN:
    conf_data = conf_data + '\x00' * (DATA_LEN - data_len)
  writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_WRITE, conf_data,
    pack('B', data_len))
writeCommand(COMMAND_FPGA, COMMAND_FPGA_CONFIGURE_STOP)

import pdb; pdb.set_trace()


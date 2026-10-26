"""Independent Ethernet octet reference based on Python zlib, not RTL CRC code."""
import zlib
PREAMBLE=b'\x55'*7+b'\xd5'
def wire_frame(client):
 return PREAMBLE+client+zlib.crc32(client).to_bytes(4,'little')
def check_wire(frame):
 return frame[:8]==PREAMBLE and 64<=len(frame)-8<=1518 and zlib.crc32(frame[8:])==0x2144df1c

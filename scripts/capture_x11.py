"""Capture actual X11 pixels with the Python standard library, including offline QA."""
import argparse
import os
import re
import struct
import subprocess
import zlib


class RGBImage(object):
    """Only the lossless crop/save operations required by the GUI checks."""
    def __init__(self, width, height, pixels):
        self.width, self.height, self.pixels = width, height, pixels
        assert len(pixels) == width * height * 3

    def crop(self, box):
        left, top, right, bottom = box
        width, height = right-left, bottom-top
        if width <= 0 or height <= 0:
            raise ValueError('Empty screenshot crop')
        pixels = bytearray(width*height*3)
        x1, x2 = max(0, left), min(self.width, right)
        if x2 > x1:
            for y in range(max(0, top), min(self.height, bottom)):
                dest = ((y-top)*width+x1-left)*3
                pixels[dest:dest+(x2-x1)*3] = self.pixels[(y*self.width+x1)*3:(y*self.width+x2)*3]
        return RGBImage(width, height, pixels)

    def save(self, path):
        def chunk(kind, data):
            return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data)&0xffffffff)
        rows = b''.join(b'\0'+self.pixels[y*self.width*3:(y+1)*self.width*3] for y in range(self.height))
        with open(str(path), 'wb') as output:
            output.write(b'\x89PNG\r\n\x1a\n')
            output.write(chunk(b'IHDR', struct.pack('>IIBBBBB', self.width, self.height, 8, 2, 0, 0, 0)))
            output.write(chunk(b'IDAT', zlib.compress(rows)))
            output.write(chunk(b'IEND', b''))


def decode_xwd(raw):
    header=struct.unpack('>25I',raw[:100])
    size,version,fmt,depth,w,h,offset,order,unit,bitorder,pad,bpp,stride,visual,red,green,blue,rgb,entries,colors=header[:20]
    if version != 7 or fmt != 2 or bpp!=32 or offset != 0 or (red,green,blue)!=(0xff0000,0xff00,0xff):
        raise RuntimeError('Unsupported X screenshot format: {}'.format(header))
    data=raw[size+colors*12:]
    if len(data) < stride*h:
        raise RuntimeError('Truncated X screenshot')
    pixels=bytearray(w*h*3)
    for y in range(h):
        row=data[y*stride:y*stride+w*4]
        for channel,source in enumerate((2,1,0) if order==0 else (1,2,3)):
            pixels[y*w*3+channel:(y+1)*w*3:3]=row[source::4]
    return RGBImage(w,h,pixels)


def x11_image(window=None):
    command=['/usr/bin/xwd','-silent']+(['-id',str(window)] if window else ['-root'])
    raw=subprocess.check_output(command)
    return decode_xwd(raw)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--title',default='ClockTree7')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    windows=subprocess.check_output(['/usr/bin/xwininfo','-root','-tree'],universal_newlines=True,
                                    encoding='utf-8',env=dict(os.environ,LC_ALL='en_US.UTF-8'))
    found=next((re.match(r'\s*(0x[0-9a-fA-F]+)',line).group(1) for line in windows.splitlines()
                if '"'+args.title in line),None)
    if not found:raise SystemExit('Application window not found: '+args.title)
    x11_image(found).save(args.out)
    print('Captured actual X11 window {} to {}'.format(found,args.out))

"""Capture actual X11 application pixels for UI verification (Pillow is test-only)."""
import argparse
import os
import re
import struct
import subprocess


def x11_image(window=None):
    from PIL import Image
    command=['/usr/bin/xwd','-silent']+(['-id',str(window)] if window else ['-root'])
    raw=subprocess.check_output(command)
    header=struct.unpack('>25I',raw[:100])
    size,version,fmt,depth,w,h,offset,order,unit,bitorder,pad,bpp,stride,visual,red,green,blue,rgb,entries,colors=header[:20]
    if bpp!=32 or (red,green,blue)!=(0xff0000,0xff00,0xff):
        raise RuntimeError('Unsupported X screenshot format: {}'.format(header))
    return Image.frombytes('RGB',(w,h),raw[size+colors*12:],'raw','BGRX' if order==0 else 'XRGB',stride,1)


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

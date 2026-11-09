"""Keep screenshot evidence independent of optional Pillow installation."""
import importlib.util
from pathlib import Path
import struct
import zlib

import pytest

spec=importlib.util.spec_from_file_location('capture_x11',str(Path(__file__).resolve().parents[1]/'scripts/capture_x11.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


@pytest.mark.parametrize('order',[0,1])
def test_xwd_byte_order_padding_crop_and_lossless_png(tmp_path,order):
    header=[0]*25
    header[:20]=[100,7,2,24,2,2,0,order,32,order,32,32,12,4,0xff0000,0xff00,0xff,8,256,0]
    colors=[(255,0,0),(0,255,0),(0,0,255),(1,2,3)]
    data=b''
    for y in range(2):
        for r,g,b in colors[y*2:y*2+2]:data+=bytes((b,g,r,0) if order==0 else (0,r,g,b))
        data+=b'PAD!'
    image=module.decode_xwd(struct.pack('>25I',*header)+data)
    assert image.pixels==bytes(v for color in colors for v in color)
    path=tmp_path/'screenshot.png';image.crop((1,0,3,2)).save(path)
    raw=path.read_bytes();assert raw[:8]==b'\x89PNG\r\n\x1a\n'
    offset=8;chunks={}
    while offset<len(raw):
        size=struct.unpack('>I',raw[offset:offset+4])[0];kind=raw[offset+4:offset+8]
        body=raw[offset+8:offset+8+size]
        assert struct.unpack('>I',raw[offset+8+size:offset+12+size])[0]==zlib.crc32(kind+body)&0xffffffff
        chunks[kind]=body;offset+=size+12
    assert struct.unpack('>IIBBBBB',chunks[b'IHDR'])==(2,2,8,2,0,0,0)
    assert zlib.decompress(chunks[b'IDAT'])==bytes((0,0,255,0,0,0,0,0,1,2,3,0,0,0))


def test_truncated_or_unsupported_xwd_does_not_produce_evidence():
    with pytest.raises(RuntimeError,match='Unsupported'):
        module.decode_xwd(struct.pack('>25I',*[0]*25))

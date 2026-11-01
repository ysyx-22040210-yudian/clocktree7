"""Render the repository GUI manual to a self-contained, offline-readable HTML page."""
from pathlib import Path
import re
import markdown


def main():
    root=Path(__file__).resolve().parents[1]
    source=root/'docs/GUI_USER_MANUAL.zh-CN.md'
    body=markdown.markdown(source.read_text(encoding='utf-8'),extensions=['tables','fenced_code','toc','sane_lists'])
    body=re.sub(r'(<img[^>]+src="([^"]+)"[^>]*>)',lambda m:'<a class="figure" href="{}" target="_blank">{}</a>'.format(m[2],m[1]),body)
    css='''
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#eef2f8;color:#25354c;font-family:"Microsoft YaHei","WenQuanYi Micro Hei",system-ui,sans-serif;font-size:15px;line-height:1.85}
    main{max-width:1100px;margin:30px auto;background:white;padding:48px 58px;box-shadow:0 8px 36px #18305212;border-radius:10px}
    .edition{font-size:12px;letter-spacing:1px;color:#607a9c;margin-bottom:18px}h1{font-size:32px;line-height:1.4;color:#132b4a}h2{font-size:23px;color:#1d497f;border-bottom:2px solid #e2ebf6;padding-bottom:9px;margin-top:46px}h3{font-size:18px;color:#31597f;margin-top:28px}
    a{color:#2168c1;text-decoration:none}a:hover{text-decoration:underline}p{margin:13px 0}li{margin:7px 0}table{width:100%;border-collapse:collapse;margin:22px 0;font-size:14px}th,td{padding:10px 12px;border:1px solid #dce4ef;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#1a3559;color:white}tr:nth-child(even){background:#f6f8fc}
    code{font-family:Consolas,"DejaVu Sans Mono",monospace;font-size:.9em;background:#eff3f9;padding:2px 4px;border-radius:3px;overflow-wrap:anywhere}pre{padding:17px 20px;background:#12243b;color:#e5edf9;overflow:auto;border-radius:7px;line-height:1.65}pre code{padding:0;background:transparent;color:inherit;overflow-wrap:normal}
    img{max-width:100%;height:auto;border:1px solid #dae3ef;border-radius:6px;box-shadow:0 5px 22px #172b4810}a.figure{display:block;margin:22px 0}blockquote{border-left:4px solid #6ca7e9;margin:20px 0;padding:4px 18px;background:#f4f8fe}a[id]{scroll-margin-top:18px}
    footer{border-top:1px solid #e1e8f2;padding-top:18px;margin-top:40px;color:#7d8fa7;font-size:12px}
    @media(max-width:800px){main{margin:0;padding:25px 18px;border-radius:0}body{font-size:14px}h1{font-size:26px}h2{font-size:21px}table{font-size:12px}th,td{padding:7px}}
    @media print{@page{size:A4;margin:16mm}body{background:white;font-size:10pt}main{margin:0;padding:0;box-shadow:none;max-width:none}h1{font-size:22pt}h2{font-size:16pt;break-after:avoid}h3{font-size:12pt;break-after:avoid}pre{white-space:pre-wrap;font-size:8pt;background:#edf1f7;color:#17263c}pre code{color:inherit}table{font-size:8.5pt}tr,img{break-inside:avoid}img{box-shadow:none;max-height:210mm;object-fit:contain}a{color:inherit}a.figure{break-inside:avoid}footer{font-size:8pt}}
    '''
    html='<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ClockTree7 GUI 操作手册</title><style>'+css+'</style></head><body><main><div class="edition">CLOCKTREE7  /  GUI USER MANUAL  /  OFFLINE EDITION</div>'+body+'<footer>由 GUI_USER_MANUAL.zh-CN.md 生成。图片可点击以原始分辨率查看；此页面无需联网。</footer></main></body></html>\n'
    output=source.with_suffix('.html')
    output.write_text(html,encoding='utf-8')
    print(str(output))


if __name__=='__main__':main()

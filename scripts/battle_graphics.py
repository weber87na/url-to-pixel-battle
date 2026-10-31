"""Small Pillow helpers shared by the portable pixel-battle command-line tools."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import ImageFont

INK='#172419'; PAPER='#f7f7e8'; LIGHT='#e0e7c8'; MID='#a7ba73'
GREEN='#65ad36'; GOLD='#e0b639'; RED='#c85735'


def clamp(value, low=0.0, high=1.0):
    return max(low,min(high,value))


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def probe(path):
    return json.loads(subprocess.check_output(
        ['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],encoding='utf-8'))


class PixelGraphics:
    """Requires cfg, base, characters and fonts (a cache) on the owning object."""
    def resolve(self,value):
        path=Path(value).expanduser()
        return path if path.is_absolute() else self.base/path

    def font(self,size,latin=False,regular=False):
        key=(size,latin,regular)
        if key not in self.fonts:
            field='font_latin' if latin else 'font_regular' if regular else 'font'
            configured=self.cfg.get(field,self.cfg.get('font'))
            if configured:
                path=self.resolve(configured)
                if not path.is_file():
                    raise FileNotFoundError(f'Configured {field} does not exist: {path}')
                self.fonts[key]=ImageFont.truetype(str(path),size)
            else:
                # These are portable font *names*, not a workspace dependency.
                # Set explicit CJK font paths in the project for Chinese text.
                for candidate in (['consola.ttf','DejaVuSansMono.ttf'] if latin else
                                  ['msjhbd.ttc','NotoSansCJK-Regular.ttc','DejaVuSans.ttf']):
                    try:
                        self.fonts[key]=ImageFont.truetype(candidate,size)
                        break
                    except OSError:
                        pass
                else:
                    raise RuntimeError(f'No usable font found. Set project.{field} to a font file.')
        return self.fonts[key]

    def txt(self,d,xy,text,size=12,fill=INK,latin=False,regular=False,anchor=None):
        d.fontmode='1'
        d.text(xy,str(text),font=self.font(size,latin,regular),fill=fill,anchor=anchor,stroke_width=0)

    def box(self,d,box,fill=PAPER,width=2,inner=True):
        x0,y0,x1,y1=box
        d.rectangle((x0+2,y0,x1-2,y1),fill=fill); d.rectangle((x0,y0+2,x1,y1-2),fill=fill)
        d.line([(x0+3,y0),(x1-3,y0),(x1,y0+3),(x1,y1-3),(x1-3,y1),
                (x0+3,y1),(x0,y1-3),(x0,y0+3),(x0+3,y0)],fill=INK,width=width)
        if inner: d.rectangle((x0+4,y0+4,x1-4,y1-4),outline=INK,width=1)

    def wrap(self,text,width,size):
        out=[]; line=''; font=self.font(size)
        for c in text:
            if c=='\n': out.append(line); line=''
            elif font.getlength(line+c)>width and line: out.append(line); line=c
            else: line+=c
        if line: out.append(line)
        if len(out)>1 and len(out[-1])<4:
            combined=out[-2]+out[-1]
            candidates=[i+1 for i,c in enumerate(out[-2]) if c in '，、。！？；' and i+1>=len(out[-2])*.5]
            cut=candidates[-1] if candidates else max(1,len(combined)//2)
            if font.getlength(combined[cut:])<=width: out[-2:]=[combined[:cut],combined[cut:]]
        return out

    def event_at(self,t):
        index=0
        for i,event in enumerate(self.events):
            if event['start']<=t: index=i
            else: break
        return index,self.events[index]

    def hp(self,d,x,y,who,value,full=100,status=''):
        spec=self.characters[who]
        name=spec['name']; size=13
        while self.font(size).getlength(name)>75 and size>9: size-=1
        self.txt(d,(x,y),name,size)
        self.txt(d,(x+76,y+3),f'Lv.{spec.get("level",99)}',9,latin=True)
        self.txt(d,(x+1,y+20),'HP',8,latin=True)
        d.rectangle((x+18,y+21,x+112,y+27),fill=PAPER,outline=INK,width=1)
        amount=round(clamp(value/full)*91)
        color=GREEN if value/full>.5 else GOLD if value/full>.2 else RED
        if amount: d.rectangle((x+20,y+23,x+19+amount,y+25),fill=color)
        d.line((x+1,y+17,x+1,y+34,x+117,y+34),fill=INK,width=2)
        self.txt(d,(x+66,y+36),status or f'{round(value):3d}/{full}',9,latin=not bool(status))

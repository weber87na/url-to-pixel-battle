#!/usr/bin/env python3
"""Portable eight-pose pixel-battle renderer. Every path is project-relative.

Character IDs, names, cast size and chapter names are supplied by project and
timeline JSON. Source images stay unchanged; only registered poses and game
effects are composited. Requires Pillow and ffmpeg/ffprobe on PATH.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from PIL import Image, ImageDraw
from battle_graphics import PixelGraphics, INK, PAPER, LIGHT, MID, GREEN, GOLD, RED, clamp, load_json, probe

EFFECT_LABELS={'water':'水系','electric':'電系','moon':'月光','leaves':'草系','ball':'投擲','fire':'火系','none':'一般'}
EFFECT_COLORS={'water':'#70bdd3','electric':GOLD,'moon':'#d5cc83','leaves':'#729b3d','ball':'#a77750','fire':'#e18336','none':LIGHT}

class PixelBattle(PixelGraphics):
    def __init__(self, project_path, timeline=None, draft=False):
        self.project_path=Path(project_path).resolve(); self.base=self.project_path.parent
        self.cfg=load_json(self.project_path); self.draft=draft
        if str(self.cfg.get('status','')).startswith('needs_') and not draft:
            raise ValueError('Project preparation is incomplete. Fill cast/edit inputs and set status=ready_to_render.')
        self.characters=self.cfg.get('characters',{})
        if len(self.characters)<2: raise ValueError('At least two named characters are required; an empty template cannot render.')
        self.player_id=self.cfg.get('player_id')
        if self.player_id not in self.characters: raise ValueError('player_id must identify one configured character.')
        self.opponents=[key for key in self.characters if key!=self.player_id]
        self.names={key:spec['name'] for key,spec in self.characters.items()}
        for key,spec in self.characters.items():
            if not spec.get('name') or not spec.get('sheet'): raise ValueError(f'{key}: name and sheet are required.')
            if spec.get('role') not in ('player','opponent'): raise ValueError(f'{key}: role must be player or opponent.')
            if (spec['role']=='player') != (key==self.player_id): raise ValueError('Exactly player_id must have role=player.')
            poses=spec.get('reaction_poses',[6])
            if not poses or any(not isinstance(v,int) or not 0<=v<8 for v in poses): raise ValueError(f'{key}: invalid reaction_poses.')
        fmt={'width':1080,'height':1920,'fps':30,'logical_width':270,'logical_height':480,**self.cfg.get('format',{})}
        self.fps=int(fmt['fps']); self.w=int(fmt['logical_width']); self.h=int(fmt['logical_height'])
        self.output_size=(int(fmt['width']),int(fmt['height']))
        if (self.w,self.h)!=(270,480): raise ValueError('The authored logical layout is 270x480.')
        scale=self.output_size[0]/self.w
        if scale<1 or scale!=int(scale) or self.output_size[1]!=self.h*int(scale) or self.fps<=0:
            raise ValueError('Output must be a positive integer enlargement of 270x480, with positive fps.')
        self.fonts={}; self.prepared={}
        self.timeline_path=self.resolve(timeline or self.cfg['timeline_path'])
        self.timeline=load_json(self.timeline_path); self.events=self.timeline.get('events',[])
        if not self.events: raise ValueError('A source-timed nonempty events list is required.')
        self.duration=float(self.timeline.get('total_duration',self.timeline.get('duration',max(e['end'] for e in self.events))))
        if not math.isfinite(self.duration) or self.duration<=0: raise ValueError('Invalid total_duration.')
        chapter_data=self.timeline.get('chapters',self.cfg.get('chapters',[]))
        if isinstance(chapter_data,dict): chapter_data=[{'id':k,'title':v} if isinstance(v,str) else {'id':k,**v} for k,v in chapter_data.items()]
        if not chapter_data:
            ids=list(dict.fromkeys(str(e.get('chapter','battle')) for e in self.events))
            chapter_data=[{'id':key,'title':self.cfg.get('title','像素對戰') if len(ids)==1 else key} for key in ids]
        self.chapter_data={str(c['id']):c for c in chapter_data}
        self.chapter_order=list(self.chapter_data)
        self.audio_path=self.resolve(self.timeline.get('audio_path',self.cfg.get('audio_path','audio/dialogue.wav')))
        reg_path=self.resolve(self.cfg.get('registration_path','assets/sprite_registration.json'))
        registration=load_json(reg_path).get('characters',{}) if reg_path.exists() else {}
        self.assets={}; self.asset_audit={}; self.pose_anchors={}; self.standing_heights={}
        for key,spec in self.characters.items():
            path=self.resolve(spec['sheet'])
            if not path.exists() or key not in registration:
                if draft: continue
                raise FileNotFoundError(f'{key}: sheet and complete registered bounds are required. Run register_sprites.py --project first.')
            reg=registration[key]; sheet=Image.open(path).convert('RGBA')
            if 'reaction_poses' not in spec:
                spec['reaction_poses']=reg.get('reaction_poses',[6])
                if not spec['reaction_poses'] or any(not isinstance(v,int) or not 0<=v<8 for v in spec['reaction_poses']):
                    raise ValueError(f'{key}: invalid registered reaction_poses.')
            if sheet.getchannel('A').getextrema()[0]==255: raise ValueError(f'{key}: sheet is not transparent.')
            if reg.get('size')!=list(sheet.size): raise ValueError(f'{key}: registration size is stale.')
            if reg.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest()!=reg['sha256']:
                raise ValueError(f'{key}: sheet changed after registration; rerun register_sprites.py.')
            if len(reg.get('frames',[]))!=8: raise ValueError(f'{key}: exactly eight registered poses are required.')
            self.assets[key]=[]; self.pose_anchors[key]=[]
            for frame in reg['frames']:
                rect=frame['bounds']; anchor=frame['anchor_sheet']
                if not (0<=rect[0]<rect[2]<=sheet.width and 0<=rect[1]<rect[3]<=sheet.height):
                    raise ValueError(f'{key}: registered bounds exceed the sheet.')
                self.assets[key].append(sheet.crop(rect)); self.pose_anchors[key].append((anchor[0]-rect[0],anchor[1]-rect[1]))
            self.standing_heights[key]=float(reg['standing_height'])
            if self.standing_heights[key]<=0: raise ValueError('standing_height must be positive.')
            self.asset_audit[key]={'path':str(path),'size':list(sheet.size),'registration':reg,
                                   'method':'Complete registered bounds, fixed standing-height scale and per-pose foot anchor'}
        previous=-1; chapter=None; state={key:100 for key in self.characters}; self.hp_before=[]
        for e in self.events:
            e['speaker']=e.get('speaker',e.get('actor'))
            if e['speaker'] not in self.characters: raise ValueError(f'Unknown event speaker: {e["speaker"]}')
            e['opponent']=e.get('opponent',e['speaker'] if e['speaker']!=self.player_id else self.opponents[0])
            if e['opponent'] not in self.opponents: raise ValueError('opponent must be a non-player character.')
            e['target']=e.get('target',e['opponent'] if e['speaker']==self.player_id else self.player_id)
            if e['target'] not in self.characters: raise ValueError('Unknown target.')
            if e['speaker']!=self.player_id and e['speaker']!=e['opponent']: raise ValueError('The active non-player speaker must equal opponent.')
            if e['target'] not in (self.player_id,e['opponent']): raise ValueError('Target must be one of the two visible characters.')
            if not all(math.isfinite(float(e[k])) for k in ('start','end')) or not 0<=e['start']<e['end']<=self.duration+.001 or e['start']<previous-.001:
                raise ValueError(f'Invalid event range/order: {e}')
            previous=e['end']; e['effect']=str(e.get('effect','none')).lower()
            if e['effect'] not in EFFECT_LABELS: raise ValueError(f'Unknown effect: {e["effect"]}')
            e['action']=str(e.get('action','idle')).lower(); e['chapter']=str(e.get('chapter',self.chapter_order[0]))
            if e['action'] not in ('idle','drink','heal','attack','counter','throw','cast','react','scratch','confusion','defeat','exasperated'):
                raise ValueError(f'Unknown action: {e["action"]}')
            if e.get('kind','dialogue') not in ('dialogue','reaction'):
                raise ValueError('kind must be dialogue or reaction.')
            for amount in ('damage','heal'):
                value=float(e.get(amount,0))
                if not math.isfinite(value) or not 0<=value<=100: raise ValueError(f'{amount} must be within 0..100.')
            if e['chapter'] not in self.chapter_data: raise ValueError(f'Unknown chapter: {e["chapter"]}')
            span=e['end']-e['start']
            impact_at=float(e.get('impact_at',min(1.1,span*.45)))
            if not math.isfinite(impact_at) or not 0<=impact_at<=span+1e-9:
                raise ValueError('impact_at must be finite and relative to a time within its event.')
            # Validate both aliases when supplied, even if action_at takes
            # precedence, so an invalid unused field cannot silently survive.
            for field in ('attack_at','action_at'):
                if field in e:
                    value=float(e[field])
                    if not math.isfinite(value) or not 0<=value<=span+1e-9 or value>impact_at+1e-9:
                        raise ValueError(f'{field} must be finite, within its event, and no later than impact_at.')
            e['impact_at']=impact_at
            e['action_at']=float(e.get('action_at',e.get('attack_at',max(0,impact_at-.55))))
            if chapter!=e['chapter']:
                chapter=e['chapter']; initial=self.chapter_data[chapter].get('initial_hp',self.cfg.get('initial_hp',{}))
                state={key:float(initial.get(key,self.characters[key].get('initial_hp',100))) for key in self.characters}
                if any(not 0<=value<=100 for value in state.values()): raise ValueError('Initial HP must be within 0..100.')
            self.hp_before.append(state.copy())
            if self.is_action(e,'drink','heal'): state[e['speaker']]=min(100,state[e['speaker']]+float(e.get('heal',24)))
            elif self.is_action(e,'attack','counter','throw','cast') and e['effect']!='none': state[e['target']]=max(10,state[e['target']]-float(e.get('damage',22)))
        for c in self.timeline.get('captions',[]):
            if not 0<=c['start']<c['end']<=self.duration+.001: raise ValueError('Invalid caption timing.')
            if len(self.wrap(c['text'],215,12))>3: raise ValueError('Caption exceeds three readable lines; split it at the transcript stage.')
        if not draft:
            if str(self.timeline.get('status','')).startswith(('layout','draft')): raise ValueError('A draft timeline cannot be a finished video.')
            if not self.audio_path.exists(): raise FileNotFoundError(self.audio_path)
            audio_duration=float(probe(self.audio_path)['format']['duration'])
            audio_tolerance=max(1/self.fps,.1)
            if not math.isfinite(audio_duration) or abs(audio_duration-self.duration)>audio_tolerance+1e-9:
                raise ValueError(f'Audio duration {audio_duration:.6f}s differs from timeline {self.duration:.6f}s; tolerance is {audio_tolerance:.6f}s. Supply the complete edited dialogue, including intentional silence.')
            sfx_value=self.timeline.get('sfx_path',self.cfg.get('sfx_path'))
            if sfx_value and not self.resolve(sfx_value).is_file(): raise FileNotFoundError(f'Configured SFX file is missing: {self.resolve(sfx_value)}')

    @staticmethod
    def is_action(e,*names):
        return any(name in e['action'] for name in names)

    def pose(self,e,local,key):
        actor=e['speaker']; target=e['target']
        hit=float(e.get('impact_at',min(1.1,(e['end']-e['start'])*.45)))
        action_at=float(e.get('action_at',e.get('attack_at',max(0,hit-.55))))
        if key==target and self.is_action(e,'attack','counter','throw','cast') and hit<=local<hit+.85:
            return 0 if hit+.26<=local<hit+.36 else 5
        if key!=actor: return 0
        if self.is_action(e,'drink','heal'):
            q=local-action_at
            if q<0: return 0
            if q<.38: return 1
            if q<1.22: return 2
            if q<1.56: return 1
            return 0
        if self.is_action(e,'react','scratch','confus'):
            poses=self.characters[key].get('reaction_poses',[6])
            if len(poses)>1: return poses[int(local/.16)%len(poses)] if .1<local<e['end']-e['start']-.08 else 0
            return poses[0] if int(local/.75)%2==0 else 0
        if e['action']=='idle' and e.get('kind')=='reaction': return 0
        if self.is_action(e,'defeat','exasperat'): return 7 if local>.18 else 5
        if self.is_action(e,'attack','counter','throw','cast'):
            if local<action_at: return 6 if int(local/1.15)%3==1 else 0
            if local<hit: return 3
            if local<hit+.65: return 4
            return 6 if int((local-hit-.65)/1.1)%3==1 else 0
        return 6 if local>.4 and local<1.1 else 0

    def actor(self,layer,key,pose,box,foreground=False,offset=(0,0),visible=True):
        if not visible: return
        x0,y0,x1,y1=box
        if key not in self.assets:
            d=ImageDraw.Draw(layer)
            d.rectangle((x0+8,y0+8,x1-8,y1),outline=MID)
            self.txt(d,((x0+x1)//2,y0+35),f'{self.names[key]}',12,anchor='mm')
            self.txt(d,((x0+x1)//2,y0+54),f'POSE {pose}',10,latin=True,anchor='mm')
            return
        cache=(key,pose,x1-x0,y1-y0,foreground)
        if cache not in self.prepared:
            original=self.assets[key][pose]
            scale=(190 if foreground else y1-y0)/self.standing_heights[key]
            self.prepared[cache]=original.resize((max(1,round(original.width*scale)),max(1,round(original.height*scale))),Image.Resampling.NEAREST)
        img=self.prepared[cache]
        scale=(190 if foreground else y1-y0)/self.standing_heights[key]
        anchor=self.pose_anchors[key][pose]
        target_x=74 if foreground else (x0+x1)//2
        target_y=y0+190 if foreground else y1
        x=round(target_x-anchor[0]*scale+offset[0])
        y=round(target_y-anchor[1]*scale+offset[1])
        if foreground:
            # Wide hand gestures can extend past the old sprite rectangle.
            # Clip only at the bottom stage edge, not at the grid cell boundary.
            crop=Image.new('RGBA',(270,y1),(0,0,0,0))
            crop.alpha_composite(img,(x,y)); layer.alpha_composite(crop,(0,0))
        else: layer.alpha_composite(img,(x,y))

    def star(self,d,cx,cy,r=5,color=GOLD):
        d.polygon([(cx,cy-r),(cx+2,cy-2),(cx+r,cy),(cx+2,cy+2),(cx,cy+r),(cx-2,cy+2),(cx-r,cy),(cx-2,cy-2)],fill=color)

    def effect(self,layer,e,local):
        fx=e['effect']; hit=float(e.get('impact_at',min(1.1,(e['end']-e['start'])*.45)))
        age=local-hit
        d=ImageDraw.Draw(layer)
        src=(88,184) if e['speaker']==self.player_id else (199,104)
        dst=(84,178) if e['target']==self.player_id else (199,104)
        if self.is_action(e,'drink','heal'):
            if .0<age<1.2:
                for k in range(5):
                    u=(age*.8+k*.23)%1
                    x=src[0]+(-22,-9,8,22,0)[k]; y=round(src[1]-38*u)
                    d.rectangle((x,y,x+2,y+5),fill='#70bdd3')
                    d.point((x+1,y-1),fill='#70bdd3')
                if age>.55:
                    self.txt(d,(src[0],src[1]-44),'+HP',11,fill=GREEN,latin=True,anchor='mm')
            return
        if self.is_action(e,'scratch','confus','react'):
            if local>.15 and e.get('reaction_symbol',self.characters[e['speaker']].get('reaction_symbol','???' if len(self.characters[e['speaker']].get('reaction_poses',[6]))>1 else '')):
                center=(199,22) if e['speaker']!=self.player_id else (75,112)
                self.txt(d,center,e.get('reaction_symbol',self.characters[e['speaker']].get('reaction_symbol','???')),15,latin=True,anchor='mm')
                for k in range(3):
                    a=local*2+k*2.094
                    self.star(d,round(center[0]+27*math.cos(a)),round(center[1]+12*math.sin(a)),2,MID)
            return
        effect_tail=2.0 if fx=='moon' else 1.55 if fx=='leaves' else 1.35
        if fx=='none' or not -.55<age<effect_tail: return
        color=EFFECT_COLORS[fx]
        # A brief darkened arena accent once per hit, never a full-screen white flash.
        if 0<age<.3:
            d.rectangle((0,0,269,263),fill=(23,36,25,32))
        travel=clamp((age+.55)/.55)
        px=round(src[0]+(dst[0]-src[0])*travel)
        py=round(src[1]+(dst[1]-src[1])*travel)
        if age<.05:
            for k in range(13):
                yy=67+k*10
                slide=round((local*155+k*29)%300)-20
                xx=slide if e['speaker']==self.player_id else 270-slide
                direction=1 if e['speaker']==self.player_id else -1
                d.line((xx,yy,xx+direction*(14+(k%4)*6),yy),fill=MID,width=1)
        if fx=='water':
            if age<.1:
                for k in range(7):
                    x=px-round((dst[0]-src[0])*.04*k)
                    y=py+round(math.sin(k+local*9)*4)
                    d.rectangle((x-3,y-3,x+4,y+4),fill=color)
                    d.line((x-2,y-2,x+2,y-2),fill=PAPER,width=1)
            else:
                h=round(22+18*math.sin(min(age,.6)/.6*math.pi))
                for k in range(4):
                    x=dst[0]-18+k*11
                    d.rectangle((x,dst[1]-h+(k%2)*8,x+6,dst[1]+22),fill=color)
                    d.line((x+2,dst[1]-h+6,x+2,dst[1]+16),fill=PAPER,width=1)
                for k in range(8):
                    a=k*math.pi/4; r=12+age*22
                    x=round(dst[0]+math.cos(a)*r); y=round(dst[1]+math.sin(a)*r)
                    d.rectangle((x,y,x+2,y+3),fill=color)
        elif fx=='electric':
            if age>=0:
                pulse=int(age*9)%2==0
                if 0<=age<.18:
                    burst=[]
                    for k in range(24):
                        angle=k*math.pi/12
                        radius=1 if k%2==0 else .48
                        burst.append((round(dst[0]+math.cos(angle)*38*radius),round(dst[1]+math.sin(angle)*47*radius)))
                    d.polygon(burst,fill=GOLD,outline=INK,width=2)
                if pulse: d.ellipse((dst[0]-18,dst[1]-18,dst[0]+18,dst[1]+18),fill=INK)
                for k in range(8):
                    a=k*math.pi/4+.1*math.sin(local*12); points=[]
                    for j in range(4):
                        r=17+j*10
                        aa=a+(.2 if j%2 else -.2)
                        points.append((round(dst[0]+math.cos(aa)*r*.82),round(dst[1]+math.sin(aa)*r)))
                    d.line(points,fill=INK,width=5)
                    d.line(points,fill=GOLD,width=3)
                for sign in (-1,1):
                    z=[(dst[0]+sign*29,dst[1]-43),(dst[0]+sign*14,dst[1]-13),
                       (dst[0]+sign*35,dst[1]-18),(dst[0]+sign*16,dst[1]+20),
                       (dst[0]+sign*36,dst[1]+14),(dst[0]+sign*23,dst[1]+43)]
                    d.line(z,fill=INK,width=6)
                    d.line(z,fill=GOLD,width=3)
            else:
                self.star(d,px,py,7,GOLD)
        elif fx=='moon':
            cx,cy=122,92
            d.ellipse((cx-28,cy-28,cx+28,cy+28),fill='#f3eaaa',outline=INK,width=2)
            d.ellipse((cx-25,cy-25,cx+25,cy+25),outline=GOLD,width=2)
            for ox,oy,r in [(-10,-12,6),(9,-6,4),(-3,10,7),(13,14,3),(-15,8,3)]:
                d.ellipse((cx+ox-r,cy+oy-r,cx+ox+r,cy+oy+r),fill='#d4cc8b')
                d.arc((cx+ox-r,cy+oy-r,cx+ox+r,cy+oy+r),10,170,fill=GOLD,width=1)
            if age>=0:
                d.line((cx,cy+28,dst[0],dst[1]),fill=INK,width=8)
                d.line((cx,cy+28,dst[0],dst[1]),fill=GOLD,width=6)
                d.line((cx,cy+28,dst[0],dst[1]),fill=PAPER,width=2)
                for k in range(6):
                    a=k*math.pi/3+age*2
                    self.star(d,round(dst[0]+math.cos(a)*30),round(dst[1]+math.sin(a)*24),4,GOLD)
        elif fx=='leaves':
            for k in range(12):
                u=clamp(travel-k*.06) if age<0 else 1
                x=round(src[0]+(dst[0]-src[0])*u+math.sin(k*1.9+local*5)*29)
                y=round(src[1]+(dst[1]-src[1])*u+math.cos(k*1.4+local*4)*34)
                d.polygon([(x-8,y),(x,y-5),(x+8,y),(x,y+5)],fill=color,outline=INK)
                d.line((x-4,y,x+4,y),fill=LIGHT,width=1)
            for worm in range(3):
                u=clamp((age+.5-worm*.07)/.6)
                bx=src[0]+(dst[0]-src[0])*u+(worm-1)*12
                by=src[1]+(dst[1]-src[1])*u+(worm-1)*22
                for k in range(6):
                    x=round(bx-14+k*5); y=round(by+math.sin(k*.8+age*9+worm)*4)
                    d.rectangle((x,y,x+5,y+5),fill=GREEN,outline=INK,width=1)
                    d.point((x+1,y+1),fill=LIGHT)
                d.point((x+4,y+1),fill=INK)
        elif fx=='ball':
            if age<.1:
                cy=py-round(math.sin(travel*math.pi)*32); cx=px
            else:
                u=clamp(age/1.1)
                cx=round(dst[0]+(src[0]-dst[0])*u*.45)
                cy=round(dst[1]-abs(math.sin(u*math.pi*2))*30+u*24)
            d.ellipse((cx-8,cy-8,cx+8,cy+8),fill=GOLD,outline=INK,width=2)
            d.line((cx-4,cy-4,cx+4,cy+4),fill=PAPER,width=2)
            if .05<age<.4:
                for k in range(4): self.star(d,dst[0]+(-18,18,0,0)[k],dst[1]+(0,0,-18,18)[k],3,GOLD)
        elif fx=='fire':
            for k in range(9):
                x=(px if age<0 else dst[0])+round(math.sin(k*2+local*9)*20)
                y=(py if age<0 else dst[1])+round(math.cos(k+local*7)*20)
                d.polygon([(x-4,y+8),(x-6,y),(x,y-12-(k%3)*4),(x+5,y),(x+4,y+8)],fill=RED)
                d.polygon([(x-2,y+5),(x,y-6),(x+2,y+5)],fill=GOLD)
        if 0<=age<.2:
            for k in range(8):
                a=k*math.pi/4
                r0=12; r1=28
                d.line((round(dst[0]+r0*math.cos(a)),round(dst[1]+r0*math.sin(a)),round(dst[0]+r1*math.cos(a)),round(dst[1]+r1*math.sin(a))),fill=INK,width=2)

    def frame(self,t,all_text=False):
        idx,e=self.event_at(t); local=max(0,t-e['start']); span=e['end']-e['start']
        chapter=e['chapter']; chapter_name=self.chapter_data[chapter]['title']; chapter_no=f'{self.chapter_order.index(chapter)+1:02d}'
        who=e['speaker']; opponent=e['opponent']
        if opponent==self.player_id: opponent=who if who!=self.player_id else self.opponents[0]
        hit=float(e.get('impact_at',min(1.1,span*.45)))
        p=clamp((local-hit)/.6)
        hp=self.hp_before[idx].copy()
        if self.is_action(e,'drink','heal'): hp[who]=min(100,hp[who]+float(e.get('heal',24))*p)
        elif self.is_action(e,'attack','counter','throw','cast') and e['effect']!='none': hp[e['target']]=max(10,hp[e['target']]-float(e.get('damage',22))*p)
        im=Image.new('RGBA',(270,480),PAPER); d=ImageDraw.Draw(im)
        d.rectangle((0,0,269,3),fill=INK)
        self.txt(d,(14,10),'GEN-I PIXEL BATTLE',8,latin=True)
        self.txt(d,(255,10),f'{chapter_no}/{len(self.chapter_order):02d}',8,latin=True,anchor='rt')
        self.txt(d,(14,24),chapter_name,20)
        self.txt(d,(255,33),self.cfg.get('title_short',self.cfg.get('title','像素對戰')),12,anchor='rm')
        d.line((14,51,255,51),fill=INK,width=1)
        arena=Image.new('RGBA',(270,266),(0,0,0,0)); a=ImageDraw.Draw(arena)
        self.hp(a,13,9,opponent,hp[opponent])
        self.hp(a,142,211,self.player_id,hp[self.player_id])
        a.line((153,174,247,174),fill=LIGHT,width=1); a.line((22,249,133,249),fill=LIGHT,width=1)
        poses={key:self.pose(e,local,key) for key in (self.player_id,opponent)}
        enemy_offset=(0,0); player_offset=(0,0)
        if idx==0 and t<.4:
            enemy_offset=(round(60*(1-t/.4)),0); player_offset=(-round(55*(1-t/.4)),0)
        is_hit=self.is_action(e,'attack','counter','throw','cast') and hit<=local<hit+.85
        jitter=2 if int((local-hit)*12)%2 else -2
        enemy_visible=player_visible=True
        if is_hit:
            if e['target']==self.player_id: player_offset=(jitter,0)
            else: enemy_offset=(jitter,0)
            # One short blink after the actual recoil pose has been visible.
            if hit+.24<=local<hit+.34:
                if e['target']==self.player_id: player_visible=False
                else: enemy_visible=False
        self.actor(arena,opponent,poses[opponent],(145,42,252,174),offset=enemy_offset,visible=enemy_visible)
        self.actor(arena,self.player_id,poses[self.player_id],(17,126,133,251),foreground=True,offset=player_offset,visible=player_visible)
        fx=Image.new('RGBA',arena.size,(0,0,0,0)); self.effect(fx,e,local); arena.alpha_composite(fx)
        default_response='體力回復！' if self.is_action(e,'drink','heal') else '效果命中！' if e['effect']!='none' else ''
        response=e.get('response',default_response)
        if local>hit+.6 and response: self.txt(ImageDraw.Draw(arena),(135,257),response,11,anchor='mm')
        shake=(2 if int(local*20)%2 else -2) if is_hit and local<hit+.28 else 0
        im.alpha_composite(arena,(shake,56)); d=ImageDraw.Draw(im)
        self.box(d,(8,326,261,408))
        move=str(e.get('move','準備出招'))
        if e.get('move_before_impact') and local<hit: move=e['move_before_impact']
        heading=f'{self.names[who]} 使出了「{move}」' if e.get('kind')!='reaction' else move if e['action']=='idle' else f'{self.names[who]} 的反應'
        size=11
        while self.font(size).getlength(heading)>215 and size>8: size-=1
        self.txt(d,(20,335),heading,size)
        text=e.get('text','').strip(); cap_local=local; rate=26
        if self.timeline.get('captions') and not all_text:
            captions=[c for c in self.timeline['captions'] if e['start']-.01<=c['start']<e['end']-.001 and c['start']<=t]
            if captions:
                c=captions[-1]; text=c['text'].strip(); cap_local=t-c['start']
                rate=min(40,max(16,len(text)/max(.1,(c['end']-c['start'])*.8)))
            else: text=e.get('display_text','') if e.get('kind')=='reaction' else ''
        if t<self.events[0]['start']:
            heading='對戰即將開始！'
            d.rectangle((18,332,250,349),fill=PAPER)
            self.txt(d,(20,335),heading,11)
            text=f'{self.names[self.player_id]} VS {self.names[opponent]}'; cap_local=10
        if e.get('kind')=='reaction' and not text: text='???'
        reveal=len(text) if all_text else min(len(text),max(1,int(cap_local*rate)+1))
        shown=text[:reveal]; size=13
        lines=self.wrap(shown,215,size)
        if len(lines)>3: size=12; lines=self.wrap(shown,215,size)
        if len(lines)>3: raise ValueError(f'Caption too long for readable layout: {shown}')
        for n,line in enumerate(lines): self.txt(d,(20,353+n*16),line,size)
        if shown and reveal==len(text) and int(t*2)%2==0: d.polygon([(247,394),(253,394),(250,398)],fill=INK)
        self.box(d,(8,416,261,465)); d.line((169,421,169,460),fill=INK,width=1)
        d.polygon([(19,428),(24,432),(19,436)],fill=INK)
        self.txt(d,(30,425),move,11)
        self.txt(d,(30,443),'陷入困惑' if len(self.characters[who].get('reaction_poses',[6]))>1 and self.is_action(e,'react','scratch','confus') else '等待對方',10,regular=True)
        self.txt(d,(181,425),EFFECT_LABELS[e['effect']]+'招式',11)
        self.txt(d,(181,443),'PP  0/1' if local>=hit else 'PP  1/1',10,latin=True)
        self.txt(d,(14,467),self.cfg.get('credit','像素戲仿'),9)
        self.txt(d,(255,467),'原音節選',9,anchor='rt')
        if self.draft: self.txt(d,(255,10),'版型預覽',8,anchor='rt')
        return im.convert('RGB').resize(self.output_size,Image.Resampling.NEAREST)


def render(battle,path,start=0.0,end=None):
    end=battle.duration if end is None else min(end,battle.duration)
    if start<0: raise ValueError('start cannot be negative')
    start_frame=round(start*battle.fps); end_frame=math.ceil(end*battle.fps-1e-9)
    frames=end_frame-start_frame; duration=frames/battle.fps
    if frames<=0: raise ValueError('Empty render interval')
    path.parent.mkdir(parents=True,exist_ok=True)
    sfx_value=battle.timeline.get('sfx_path',battle.cfg.get('sfx_path'))
    sfx=battle.resolve(sfx_value) if sfx_value else None
    if sfx is not None and not sfx.exists(): raise FileNotFoundError(f'Missing original effect audio: {sfx}')
    command=['ffmpeg','-y','-hide_banner','-loglevel','warning','-f','rawvideo','-pix_fmt','rgb24',
             '-s',f'{battle.output_size[0]}x{battle.output_size[1]}','-r',str(battle.fps),'-i','pipe:0',
             '-ss',str(start_frame/battle.fps),'-i',str(battle.audio_path)]
    if sfx is not None:
        command+=['-ss',str(start_frame/battle.fps),'-i',str(sfx),'-filter_complex',
                  '[1:a]apad[dialogue];[dialogue][2:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.98:level=0[mix]',
                  '-map','0:v:0','-map','[mix]']
    else: command+=['-map','0:v:0','-map','1:a:0','-af','apad']
    command+=['-c:v','libx264','-preset','fast','-crf','17','-pix_fmt','yuv420p',
             '-c:a','aac','-b:a','192k','-ar','48000','-t',str(duration),'-movflags','+faststart',str(path)]
    proc=subprocess.Popen(command,stdin=subprocess.PIPE)
    try:
        for n,f in enumerate(range(start_frame,end_frame)):
            proc.stdin.write(battle.frame(f/battle.fps).tobytes())
            if n%300==0: print(f'Rendered {n}/{frames} frames',flush=True)
        proc.stdin.close()
        if proc.wait()!=0: raise RuntimeError('ffmpeg encoding failed')
    except BaseException:
        proc.kill(); raise
    info=probe(path); video=next(s for s in info['streams'] if s['codec_type']=='video')
    if int(video.get('nb_frames',-1))!=frames or (video['width'],video['height'])!=battle.output_size:
        raise RuntimeError('Encoded frame count or dimensions differ from timeline')
    if len([s for s in info['streams'] if s['codec_type']=='audio'])!=1: raise RuntimeError('Expected one final audio track')
    subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],check=True)
    audit={'status':'encoded_and_full_decode_verified','output':str(path),'fps':battle.fps,'frames':frames,'duration':duration,
           'render_start':start_frame/battle.fps,'resolution':list(battle.output_size),'audio':str(battle.audio_path),'sfx':str(sfx) if sfx is not None else None,
           'timeline':str(battle.timeline_path),'character_sheets':battle.asset_audit,
           'animation':'Eight ImageGen body poses per character plus procedural game effects; no AI video or synthesized voice',
           'flash_policy':'No full-screen white flashing. One brief dark arena accent per hit; localized effects and one sprite blink.',
           'art_review':'Requires a separate encoded-frame visual review.'}
    path.with_suffix('.audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(path),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True); p.add_argument('--timeline'); p.add_argument('--output')
    p.add_argument('--ui-preview',action='store_true'); p.add_argument('--stills',action='store_true'); p.add_argument('--check',action='store_true')
    p.add_argument('--start',type=float,default=0); p.add_argument('--end',type=float)
    args=p.parse_args(); battle=PixelBattle(args.project,args.timeline,args.ui_preview)
    path=battle.resolve(args.output or battle.cfg.get('output','output/pixel_battle_short.mp4'))
    path.parent.mkdir(parents=True,exist_ok=True)
    if args.check:
        print(json.dumps({'ready':not args.ui_preview,'duration':battle.duration,'events':len(battle.events),'sheets':battle.asset_audit},ensure_ascii=False))
        return
    if args.stills or args.ui_preview:
        thumbs=[]
        for i,e in enumerate(battle.events):
            if e['end']<args.start or e['start']>(args.end if args.end is not None else battle.duration): continue
            hit=float(e.get('impact_at',min(1.1,(e['end']-e['start'])*.45)))
            t=min(e['end']-.05,e['start']+hit+.12)
            im=battle.frame(t,all_text=False); im.save(path.parent/f'event_{i+1:02d}.png')
            thumbs.append(im.resize((216,384),Image.Resampling.NEAREST))
        if not thumbs: raise ValueError('No events in the requested still interval')
        cols=min(5,len(thumbs)); rows=math.ceil(len(thumbs)/cols)
        sheet=Image.new('RGB',(cols*216,rows*384),PAPER)
        for i,im in enumerate(thumbs): sheet.paste(im,((i%cols)*216,(i//cols)*384))
        sheet.save(path.parent/'storyboard.png'); print(path.parent/'storyboard.png')
        return
    if battle.draft: raise ValueError('Draft mode cannot encode a final video')
    render(battle,path,args.start,args.end)

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}',file=sys.stderr); sys.exit(1)

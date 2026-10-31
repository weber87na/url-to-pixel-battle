#!/usr/bin/env python3
"""Register complete eight-pose transparent sheets without editing their pixels.

Connected components can cross nominal cell boundaries. The renderer uses the
measured full bounds, one standing-height scale and per-pose foot anchors.
Ambiguous or touching figures are rejected; review the sheets before rendering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from battle_graphics import load_json


def register(project_path,threshold=128,padding=3,min_area_fraction=.01):
    project_path=Path(project_path).resolve(); base=project_path.parent
    cfg=load_json(project_path); characters=cfg['characters']
    if len(characters)<2: raise ValueError('At least two named characters are required.')
    if cfg.get('player_id') not in characters: raise ValueError('player_id must name an existing character.')
    if not 0<=threshold<255 or padding<0 or not 0<min_area_fraction<1:
        raise ValueError('Invalid threshold, padding or minimum-area fraction.')
    report={'schema_version':1,'method':f'Connected components on alpha >{threshold}; source pixels unchanged.',
            'characters':{}}
    for key,spec in characters.items():
        path=Path(spec['sheet']).expanduser()
        if not path.is_absolute(): path=base/path
        im=Image.open(path)
        if 'A' not in im.getbands(): raise ValueError(f'{key}: transparent RGBA PNG is required.')
        alpha=np.asarray(im.getchannel('A'))
        if int(alpha.min())==255: raise ValueError(f'{key}: sheet has no transparent background.')
        cols,rows=spec.get('grid',[4,2])
        if cols*rows!=8 or min(cols,rows)<1: raise ValueError(f'{key}: grid must contain exactly eight poses.')
        cellw,cellh=im.width/cols,im.height/rows
        count,labels,stats,centroids=cv2.connectedComponentsWithStats((alpha>threshold).astype('uint8'),8)
        poses={}
        for index in range(1,count):
            x,y,w,h,area=map(int,stats[index])
            if area<cellw*cellh*min_area_fraction: continue
            col=min(cols-1,int(centroids[index][0]/cellw)); row=min(rows-1,int(centroids[index][1]/cellh))
            pose=row*cols+col
            if pose in poses:
                raise ValueError(f'{key}: multiple large components in pose {pose}; review the sheet or use explicit registration metadata.')
            poses[pose]={'bounds':[max(0,x-padding),max(0,y-padding),min(im.width,x+w+padding),min(im.height,y+h+padding)],
                         'anchor_sheet':[round((col+.5)*cellw),y+h],'body_height':h,'area':area}
        if len(poses)!=8: raise ValueError(f'{key}: expected eight separate body components; found {len(poses)}.')
        height=float(spec.get('standing_height',poses[0]['body_height']))
        if height<=0: raise ValueError(f'{key}: invalid standing_height')
        reaction=spec.get('reaction_poses',[6])
        if not reaction or any(not isinstance(p,int) or not 0<=p<8 for p in reaction):
            raise ValueError(f'{key}: reaction_poses must contain pose indexes 0..7.')
        report['characters'][key]={'path':spec['sheet'],'name':spec['name'],'size':list(im.size),'grid':[cols,rows],
                                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'standing_height':height,
                                  'reaction_poses':reaction,'frames':[poses[i] for i in range(8)],
                                  'alpha_range':[int(alpha.min()),int(alpha.max())],
                                  'transparent_fraction':float((alpha==0).mean())}
    dest=Path(cfg.get('registration_path','assets/sheet_registration.json'))
    if not dest.is_absolute(): dest=base/dest
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return dest,report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project',required=True); p.add_argument('--alpha-threshold',type=int,default=128)
    p.add_argument('--padding',type=int,default=3); p.add_argument('--min-area-fraction',type=float,default=.01)
    a=p.parse_args(); path,report=register(a.project,a.alpha_threshold,a.padding,a.min_area_fraction)
    print(json.dumps({'registration_path':str(path),'characters':list(report['characters']),
                      'pose_count':8*len(report['characters'])},ensure_ascii=False))


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'ERROR: {exc}',file=sys.stderr); sys.exit(1)

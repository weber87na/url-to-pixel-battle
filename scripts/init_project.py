"""Initialize a portable project without inventing cast, captions or footage."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs


def project_key(url):
    parts=urlparse(url)
    if parts.scheme not in ('http','https') or not parts.netloc:
        raise ValueError('Expected an http(s) video URL')
    host=parts.hostname or ''
    candidate=parse_qs(parts.query).get('v',[''])[0] if host in ('youtube.com','www.youtube.com','m.youtube.com') else ''
    if host=='youtu.be': candidate=parts.path.strip('/').split('/')[0]
    if not candidate: candidate=parts.path.rstrip('/').split('/')[-1]
    safe=''.join(c for c in candidate if c.isascii() and (c.isalnum() or c in '_-'))[:48]
    return safe or hashlib.sha256(url.encode()).hexdigest()[:12]


def fonts():
    candidates=[
        ('C:/Windows/Fonts/msjhbd.ttc','C:/Windows/Fonts/msjh.ttc','C:/Windows/Fonts/consola.ttf'),
        ('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'),
        ('/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/Menlo.ttc'),
    ]
    return next((f for f in candidates if all(Path(p).is_file() for p in f)),('','',''))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',required=True)
    p.add_argument('--out',help='New project folder; default ./projects/<video-id>')
    args=p.parse_args()
    key=project_key(args.url)
    root=Path(args.out or Path('projects')/key).resolve()
    config_path=root/'project.json'
    if config_path.exists():
        existing=json.loads(config_path.read_text(encoding='utf-8-sig'))
        if existing.get('source_url')!=args.url:
            raise SystemExit('Existing project has another source URL; choose another --out folder.')
        print(json.dumps({'status':'reused_without_changes','project':str(config_path)},ensure_ascii=False)); return
    if root.exists() and any(root.iterdir()):
        raise SystemExit('Existing nonempty folder has no project.json; choose a new folder.')
    for sub in ('source','analysis','assets','audio','output'):
        (root/sub).mkdir(parents=True,exist_ok=True)
    bold,regular,latin=fonts()
    cfg={'status':'needs_cast_and_edit','title':'像素對戰','source_url':args.url,'source_path':'source/video.mp4',
         'format':{'width':1080,'height':1920,'fps':30,'logical_width':270,'logical_height':480},
         'player_id':'player','characters':{},'timeline_path':'analysis/timeline.json',
         'audio_path':'audio/dialogue.wav','sfx_path':'audio/sfx.wav',
         'registration_path':'assets/sprite_registration.json','output':'output/pixel_battle_short.mp4',
         'font':bold,'font_regular':regular,'font_latin':latin,'credit':'像素戲仿'}
    config_path.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':cfg['status'],'project':str(config_path),'font_setup_required':not bool(bold)},ensure_ascii=False))


if __name__=='__main__':
    main()

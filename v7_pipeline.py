"""Validated, non-publishing V7 reel renderer for Metropol Erfolg."""
from __future__ import annotations

import json, math, os, shutil, subprocess, tempfile, urllib.request
import re
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schemas" / "content-item.schema.json"
OUTPUT_DIR = Path(os.environ.get("V7_OUTPUT_DIR", BASE_DIR / "output"))
NAVY, IVORY, GOLD, MUTED = (5, 8, 30), (242, 237, 225), (196, 163, 99), (177, 181, 191)

class V7Error(ValueError): pass

def _ffmpeg():
    configured=os.environ.get("FFMPEG_BINARY")
    if configured: return configured
    found=shutil.which("ffmpeg")
    if found: return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise V7Error("ffmpeg is not installed or configured") from exc

def _font(size: int, bold=False, serif=False):
    names = (["georgiab.ttf", "PlayfairDisplay-Bold.ttf"] if bold else ["georgia.ttf", "PlayfairDisplay-Regular.ttf"]) if serif else (["segoeuib.ttf", "DejaVuSans-Bold.ttf"] if bold else ["segoeui.ttf", "DejaVuSans.ttf"])
    paths = [Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/n for n in names] + [BASE_DIR/"fonts"/n for n in names] + [Path("/usr/share/fonts/truetype/dejavu")/n for n in names]
    for path in paths:
        if path.exists(): return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()

def validate_content(content: dict[str, Any], require_publish_gate=True) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = [f"{'.'.join(map(str,e.absolute_path)) or '$'}: {e.message}" for e in validator.iter_errors(content)]
    scenes = content.get("scenes", [])
    for i, scene in enumerate(scenes):
        if scene.get("end_seconds",0) <= scene.get("start_seconds",0): errors.append(f"scenes.{i}: end_seconds must be after start_seconds")
        if i and scene.get("start_seconds",0) < scenes[i-1].get("end_seconds",0): errors.append(f"scenes.{i}: overlaps previous scene")
    if scenes and scenes[0].get("start_seconds") != 0: errors.append("scenes.0: first scene must start at 0")
    if not any(s.get("is_primary") for s in content.get("sources",[])): errors.append("sources: at least one primary source is required")
    if require_publish_gate:
        if content.get("fact_check_status") != "verified": errors.append("fact_check_status: must be verified")
        if content.get("approval_status") not in {"approved","ready","published"}: errors.append("approval_status: must be approved, ready, or published")
        if not str(content.get("renderer_version","")).startswith("premium-v7"): errors.append("renderer_version: must target premium-v7")
    return sorted(set(errors))

def _asset(value: str, tmp: Path) -> Path:
    if value.startswith(("https://","http://")):
        target = tmp/("download"+Path(value.split("?",1)[0]).suffix); urllib.request.urlretrieve(value,target); return target
    path=Path(value).expanduser().resolve()
    if not path.is_file(): raise V7Error(f"Asset not found: {value}")
    return path

def _wrap(draw,text,font,max_width):
    lines=[]; current=""
    for word in text.split():
        trial=f"{current} {word}".strip()
        if current and draw.textbbox((0,0),trial,font=font)[2] > max_width: lines.append(current); current=word
        else: current=trial
    if current: lines.append(current)
    return lines

def _captions(path: Path):
    text=path.read_text(encoding="utf-8-sig")
    result=[]
    for block in re.split(r"\r?\n\r?\n",text):
        match=re.search(r"(\d\d):(\d\d):(\d\d)[,.](\d+)\s+-->\s+(\d\d):(\d\d):(\d\d)[,.](\d+)",block)
        if not match: continue
        v=list(map(int,match.groups())); start=v[0]*3600+v[1]*60+v[2]+v[3]/1000; end=v[4]*3600+v[5]*60+v[6]+v[7]/1000
        lines=[line.strip() for line in block.splitlines() if line.strip() and "-->" not in line and not line.strip().isdigit() and line.strip()!="WEBVTT"]
        if lines: result.append((start,end," ".join(lines)))
    return result

def _background(path,width,height,p):
    src=Image.open(path).convert("RGB"); zoom=1.04+.07*p; ratio=width/height
    crop_h=int(src.height/zoom); crop_w=int(crop_h*ratio)
    if crop_w>src.width: crop_w=int(src.width/zoom); crop_h=int(crop_w/ratio)
    left=max(0,int((src.width-crop_w)*(.25+.45*p))); top=max(0,(src.height-crop_h)//2)
    im=src.crop((left,top,left+crop_w,top+crop_h)).resize((width,height),Image.Resampling.LANCZOS)
    im=ImageEnhance.Color(im).enhance(.56); im=ImageEnhance.Contrast(im).enhance(1.14); im=Image.blend(im,Image.new("RGB",im.size,NAVY),.36).convert("RGBA")
    shade=Image.new("RGBA",im.size,(0,0,0,0)); d=ImageDraw.Draw(shade); d.rectangle((0,0,width,int(height*.22)),fill=(2,4,18,105)); d.rectangle((0,int(height*.72),width,height),fill=(2,4,18,135)); im.alpha_composite(shade)
    return im

def _frame(content, backgrounds, captions, t, width, height):
    scenes=content["scenes"]; idx=next((i for i,s in enumerate(scenes) if s["start_seconds"]<=t<s["end_seconds"]),len(scenes)-1); scene=scenes[idx]
    p=min(1,max(0,(t-scene["start_seconds"])/max(.01,scene["end_seconds"]-scene["start_seconds"])))
    im=_background(backgrounds[idx%len(backgrounds)],width,height,p)
    # A short photographic dissolve makes scene changes feel edited rather than
    # like consecutive template cards. The first scene intentionally starts clean.
    if idx and t-scene["start_seconds"] < .45:
        dissolve=max(0,min(1,(t-scene["start_seconds"])/.45))
        previous=_background(backgrounds[(idx-1)%len(backgrounds)],width,height,1)
        im=Image.blend(previous,im,dissolve)
    d=ImageDraw.Draw(im); scale=width/1080; margin=int(72*scale)
    kicker=_font(max(15,int(24*scale)),True); body=_font(max(31,int(67*scale)),True,True); small=_font(max(14,int(22*scale)))
    purpose_labels={"hook":"KERNFRAGE","strategy":"EINORDNUNG","calculation":"RECHNUNG","condition":"BEDINGUNG","risk":"WICHTIG","cta":"NÄCHSTER SCHRITT"}
    d.text((margin,int(height*.19)),purpose_labels.get(scene["purpose"],scene["purpose"].upper()),font=kicker,fill=GOLD)
    d.multiline_text((margin,int(height*.28)),"\n".join(_wrap(d,scene["on_screen_text"],body,width-2*margin)[:4]),font=body,fill=IVORY,spacing=int(10*scale),stroke_width=1,stroke_fill=NAVY)
    y=int(height*.57); d.line((margin,y,margin+int((width-2*margin)*min(1,p*1.8)),y),fill=GOLD,width=max(2,int(3*scale)))
    d.text((margin,int(height*.925)),content["advice_disclaimer"].upper()[:88],font=small,fill=MUTED)
    y=int(height*.895); duration=scenes[-1]["end_seconds"]; d.line((margin,y,width-margin,y),fill=GOLD,width=max(1,int(2*scale))); d.line((margin,y,margin+int((width-2*margin)*t/duration),y),fill=GOLD,width=max(2,int(4*scale)))
    caption=next((text for start,end,text in captions if start<=t<end),None)
    if caption:
        sub_font=_font(max(18,int(31*scale)),True); lines=_wrap(d,caption,sub_font,width-2*margin-int(30*scale))[-2:]; box_y=int(height*.765); line_h=max(28,int(45*scale)); box_h=line_h*len(lines)+int(34*scale)
        d.rounded_rectangle((margin,box_y-box_h//2,width-margin,box_y+box_h//2),radius=max(4,int(7*scale)),fill=(3,6,24,218))
        d.multiline_text((width//2,box_y),"\n".join(lines),font=sub_font,fill=IVORY,anchor="mm",align="center",spacing=max(4,int(8*scale)))
    return im.convert("RGB")

def render(content: dict[str,Any], assets: dict[str,Any], options=None):
    options=options or {}; errors=validate_content(content,True)
    if errors: raise V7Error("Content gate failed: "+" | ".join(errors))
    if not assets.get("backgrounds"): raise V7Error("assets.backgrounds must contain at least one image")
    if not assets.get("voiceover") and not options.get("silent_preview"): raise V7Error("assets.voiceover is required for a final V7 render")
    if assets.get("voiceover") and not assets.get("captions"): raise V7Error("assets.captions (VTT) is required when voiceover is present")
    width=int(options.get("width",720)); height=int(options.get("height",round(width*16/9))); fps=int(options.get("fps",30))
    if (width,height) not in {(720,1280),(1080,1920)} or fps not in {25,30}: raise V7Error("Only 720x1280 or 1080x1920 at 25/30 fps are supported")
    duration=float(content["scenes"][-1]["end_seconds"]); OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
    output_name=str(options.get("output_name") or content["content_id"])
    if not re.fullmatch(r"[A-Za-z0-9._-]+",output_name): raise V7Error("output_name contains invalid characters")
    output=OUTPUT_DIR/f"{output_name}.mp4"
    work_root=OUTPUT_DIR/".work"; work_root.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix="metropol-v7-",dir=work_root))
    try:
        backgrounds=[_asset(v,tmp) for v in assets["backgrounds"]]; captions=_captions(_asset(assets["captions"],tmp)) if assets.get("captions") else []; frames=tmp/"frames"; frames.mkdir()
        # Ten authored frames per second are sufficient for the restrained
        # Ken-Burns motion. ffmpeg delivers the required 25/30-fps stream.
        # This cuts local render time substantially without changing duration.
        authored_fps=min(fps,10)
        for i in range(math.ceil(duration*authored_fps)): _frame(content,backgrounds,captions,i/authored_fps,width,height).save(frames/f"{i:05d}.jpg",quality=91,subsampling=0)
        ffmpeg=_ffmpeg(); silent=tmp/"silent.mp4"; subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-y","-framerate",str(authored_fps),"-i",str(frames/"%05d.jpg"),"-r",str(fps),"-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p",str(silent)],check=True)
        if assets.get("voiceover"):
            voice=_asset(assets["voiceover"],tmp); subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-y","-i",str(silent),"-i",str(voice),"-filter:a","loudnorm=I=-16:LRA=7:TP=-1.5","-map","0:v","-map","1:a","-c:v","copy","-c:a","aac","-b:a","192k","-shortest","-movflags","+faststart",str(output)],check=True)
        else: shutil.copy2(silent,output)
        manifest={"status":"rendered_local","content_id":content["content_id"],"path":str(output),"published":False,"qa":{"duration_seconds":duration,"width":width,"height":height,"fps":fps,"has_voiceover":bool(assets.get("voiceover")),"caption_cues":len(captions),"file_size_bytes":output.stat().st_size}}
        (OUTPUT_DIR/f"{output_name}.manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8"); return manifest
    finally:
        shutil.rmtree(tmp,ignore_errors=True)
        try: work_root.rmdir()
        except OSError: pass

#!/usr/bin/env python3
"""설교컷편집 (sermon-cut): a full worship-service recording -> one section per FCP project + H.264 files.

  python3 scripts/sermon_cut.py scan <service.mp4> [--job DIR] [--every 20]
      Evidence for finding the sections: audio class per 10 s (music / speech / quiet, by low-energy-frame ratio),
      a frame every N s on contact sheets (10 min per sheet, time + class under each frame), and overview.md
      (one line per minute: class bar + transcript words when transcript/<stem>.words.json exists).
      Claude reads overview.md + sheets and writes <job>/sections.json (see the skill).
  python3 scripts/sermon_cut.py loud <job>/sections.json [--only 설교,광고]
      Measures each section (ffmpeg loudnorm, audio only) -> sections.json "loud" (cached).
  python3 scripts/sermon_cut.py build <job>/sections.json [--only …]
      One FCPXML: a library with the date event (whole source + keyword ranges per section) and the "프로젝트" event
      holding one project per section, linked in place, with a static gain toward -14 LUFS.
  python3 scripts/sermon_cut.py export <job>/sections.json [--only …] [--height 1080] [--out DIR]
      H.264 MP4 per section, no review step: frame-exact cut, two-pass loudnorm to YouTube's -14 LUFS / -1 dBTP,
      AAC 384k 48 kHz, faststart. Bitrate per YouTube's SDR table (2160p60 60 Mbps, 1080p60 12 Mbps …).
  python3 scripts/sermon_cut.py thumbs <job>/sections.json [--kind 설교|찬양|성가대] [--every 8] [--top 12]
      Full-resolution stills of the preacher (or the singers) ranked by Apple Vision face capture quality (scripts/face_quality.swift)
      -> <job>/thumbs/<kind>/best_*.jpg + best_sheet.jpg for Claude to pick from.
  python3 scripts/sermon_cut.py thumbset <job>/sections.json [--only 설교,찬양,성가대]
      Fills scripts/thumb_templates/*.html (A bold, B editorial, C quote) from each section's "thumb" block
      -> <job>/thumbs/<kind>/style_*.html + .png, for the user to pick one per section.
  python3 scripts/sermon_cut.py render <page.html> [<page.html> …]
      Thumbnail HTML (1280×720) -> PNG via headless Chrome; JPEG instead if the PNG is over YouTube's 2 MB.
"""
import argparse, json, math, os, re, subprocess, sys, unicodedata, wave
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kit_paths  # noqa: E402

KIT = kit_paths.KIT
TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.0, 11.0      # YouTube's playback reference (loudness normalisation)
FACE = Path.home() / ".cache/video-editor/face_quality"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"



KIND_DIR = {"설교": "sermon", "찬양": "worship", "성가대": "choir", "광고": "announcements", "기도": "prayer"}


def kind_dir(kind):
    """Folder names are English (user, 2026-11-26): thumbs/<english kind>."""
    k = nfc(kind)
    return KIND_DIR.get(k) or (re.sub(r"[^a-z0-9]+", "-", k.lower()).strip("-") or "section-%03d" % (__import__("zlib").crc32(k.encode()) % 1000))

def nfc(s):
    return unicodedata.normalize("NFC", s)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def probe(src):
    j = json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,width,height,r_frame_rate:stream_tags=timecode",
                        "-of", "json", str(src)]).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    tc = next((s.get("tags", {}).get("timecode") for s in j["streams"] if s.get("tags", {}).get("timecode")), None)
    return {"duration": float(j["format"]["duration"]), "w": v["width"], "h": v["height"],
            "fps": Fraction(v["r_frame_rate"]), "timecode": tc}


def hms(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


# ---------------------------------------------------------------- scan
def audio_classes(wav, step=10):
    """Per `step` s: level (dBFS) and low-energy-frame ratio (LEF). Speech pauses between syllables, so many 20 ms
    frames sit below half the local mean (LEF ≳ 0.3); sung/played music is sustained (LEF ≲ 0.15)."""
    import numpy as np
    with wave.open(str(wav)) as w:
        sr, n = w.getframerate(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    fr = sr // 50                                      # 20 ms frames
    rms = np.sqrt(np.mean(x[: len(x) // fr * fr].reshape(-1, fr) ** 2, axis=1)) + 1e-9
    per_s = 50
    out = []
    for k in range(0, len(rms) // (per_s * step) + 1):
        seg = rms[k * per_s * step:(k + 1) * per_s * step]
        if len(seg) < per_s:
            break
        lef = []
        for j in range(0, len(seg) - per_s + 1, per_s):  # 1 s windows
            win = seg[j:j + per_s]
            lef.append(float(np.mean(win < 0.5 * win.mean())))
        db = 20 * math.log10(float(np.sqrt(np.mean(seg ** 2))))
        m = float(np.median(lef))
        cls = "quiet" if db < -45 else "music" if m < 0.16 else "speech" if m > 0.28 else "mixed"
        out.append({"t": k * step, "db": round(db, 1), "lef": round(m, 3), "cls": cls})
    return out


def grab(src, t, out, width=480):
    if not out.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-q:v", "4", "-y", str(out)], check=False)
    return out


def scan(a):
    src = Path(a.src)
    stem = nfc(src.stem)
    job = Path(a.job or KIT / "transcript/sermon" / stem)
    (job / "frames").mkdir(parents=True, exist_ok=True)
    info = probe(src)
    wav = KIT / "transcript" / f"{src.stem}.wav"          # stt_words.py leaves this next to the words.json
    if not wav.exists():
        wav = job / "audio16k.wav"
        if not wav.exists():
            run(["ffmpeg", "-v", "error", "-i", str(src), "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-y", str(wav)])
    cls = audio_classes(wav)
    words = []
    wj = KIT / "transcript" / f"{src.stem}.words.json"
    if wj.exists():
        for s in json.loads(wj.read_text())["segments"]:
            words += s["words"]
    # frames
    times = [t for t in range(0, int(info["duration"]), a.every)]
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda t: grab(src, t, job / "frames" / f"f{t:05d}.jpg"), times))
    sheets(job, times, cls, a.every)
    # overview.md: one line per minute
    lines = [f"# {stem}", f"{hms(info['duration'])} · {info['w']}×{info['h']} · {float(info['fps']):.3f} fps"
             f"{' · tc ' + info['timecode'] if info['timecode'] else ''}",
             "", "class per 10 s: M music · S speech · x mixed · . quiet;  words from STT (if run)", ""]
    sym = {"music": "M", "speech": "S", "mixed": "x", "quiet": "."}
    for m in range(0, int(info["duration"] // 60) + 1):
        bar = "".join(sym[c["cls"]] for c in cls if m * 60 <= c["t"] < m * 60 + 60)
        txt = " ".join(w["w"] for w in words if m * 60 <= w["s"] < m * 60 + 60)
        lines.append(f"{hms(m * 60)} {bar:6} {txt[:160]}")
    (job / "overview.md").write_text("\n".join(lines) + "\n")
    (job / "audio.json").write_text(json.dumps(cls))
    if not (job / "sections.json").exists():
        (job / "sections.json").write_text(json.dumps({
            "source": str(src), "date": "", "service": "", "library": "", "sections": []}, ensure_ascii=False, indent=1))
    print(f"{job}: overview.md, {len(list(job.glob('sheet_*.jpg')))} sheets, audio.json"
          f"{'' if words else '  (no STT yet: run stt_words.py for words in overview.md)'}")


def sheets(job, times, cls, every, cols=6, rows=5):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 18)
    colour = {"music": (220, 120, 40), "speech": (60, 140, 220), "mixed": (150, 150, 150), "quiet": (40, 40, 40)}
    per = cols * rows
    for k in range(0, len(times), per):
        tile_w, tile_h, lab = 480, 270, 26
        img = Image.new("RGB", (cols * tile_w, rows * (tile_h + lab)), "black")
        d = ImageDraw.Draw(img)
        for i, t in enumerate(times[k:k + per]):
            x, y = i % cols * tile_w, i // cols * (tile_h + lab)
            f = job / "frames" / f"f{t:05d}.jpg"
            if f.exists():
                img.paste(Image.open(f).resize((tile_w, tile_h)), (x, y))
            c = min(cls, key=lambda c: abs(c["t"] - t)) if cls else {"cls": "quiet"}
            d.rectangle([x, y + tile_h, x + tile_w, y + tile_h + lab], fill=colour[c["cls"]])
            d.text((x + 6, y + tile_h + 3), f"{hms(t)}  {c['cls']}", fill="white", font=font)
        img.save(job / f"sheet_{k // per:02d}.jpg", quality=80)


# ---------------------------------------------------------------- sections
def load(path, only):
    p = Path(path)
    d = json.loads(p.read_text())
    secs = d["sections"]
    if only:
        keep = [nfc(x) for x in only.split(",")]
        secs = [s for s in secs if nfc(s["kind"]) in keep or nfc(s.get("title", "")) in keep]
    if not secs:
        sys.exit("no sections to work on (empty sections.json or --only matched nothing)")
    return p, d, secs


def name_of(d, s):
    base = f"{d.get('date', '').replace('-', '')} {d.get('service', '')} {s['kind']}".strip()
    return f"{base} - {s['title']}" if s.get("title") and s["title"] != s["kind"] else base


def loud(a, quiet=False):
    p, d, secs = load(a.sections, a.only)
    src = d["source"]
    for s in secs:
        key = f"{s['start']:.3f}-{s['end']:.3f}"
        if s.get("loud", {}).get("key") == key:
            continue
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{s['start']:.3f}", "-to", f"{s['end']:.3f}",
                            "-i", src, "-map", "0:a:0", "-vn", "-af",
                            f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True)
        m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
        s["loud"] = {"key": key, "I": float(m["input_i"]), "TP": float(m["input_tp"]), "LRA": float(m["input_lra"]),
                     "thresh": float(m["input_thresh"]), "offset": float(m["target_offset"])}
        if not quiet:
            print(f"{s['kind']:6} {hms(s['start'])}–{hms(s['end'])}  {s['loud']['I']:6.1f} LUFS  {s['loud']['TP']:5.1f} dBTP")
    full = json.loads(p.read_text())
    by = {(x["start"], x["end"]): x for x in secs}
    full["sections"] = [by.get((x["start"], x["end"]), x) for x in full["sections"]]
    p.write_text(json.dumps(full, ensure_ascii=False, indent=1))
    return p, full, secs


# ---------------------------------------------------------------- FCP library
def build(a):
    import xml.etree.ElementTree as ET
    p, d, secs = loud(a, quiet=True)
    src = Path(d["source"])
    info = probe(src)
    fps = info["fps"]
    fd = 1 / fps                                             # frame duration
    tc0 = 0
    if info["timecode"]:
        hh, mm, ss, ff = map(int, re.split("[:;]", info["timecode"]))
        tc0 = Fraction((hh * 3600 + mm * 60 + ss) * round(float(fps)) + ff) * fd   # NDF frame count

    def t(sec):                                              # seconds -> frame-aligned rational
        x = Fraction(round(Fraction(sec) / fd)) * fd
        return "0s" if x == 0 else f"{x.numerator}/{x.denominator}s"

    rate = {Fraction(60000, 1001): "5994", Fraction(30000, 1001): "2997", Fraction(24000, 1001): "2398",
            Fraction(60): "60", Fraction(30): "30", Fraction(25): "25", Fraction(50): "50", Fraction(24): "24"}.get(fps)
    size = {2160: "2160p", 1080: "1080p", 720: "720p"}.get(info["h"])
    fmt_name = f"FFVideoFormat{size}{rate}" if rate and size and info["w"] in (3840, 1920, 1280) else None
    ver = kit_paths.FCPXML_VERSION
    root = ET.Element("fcpxml", version=ver)
    io = ET.SubElement(root, "import-options")      # FCP honours this; the library@location alone was ignored
    lib_path = Path(d.get("library") or kit_paths.library_path(p.parent,
                                                                 f"{d.get('date', '')} {d.get('service', '')}".strip()))
    for k, v in (("library location", lib_path.resolve().as_uri()), ("copy assets", "0"), ("suppress warnings", "1")):
        ET.SubElement(io, "option", key=k, value=v)
    res = ET.SubElement(root, "resources")
    fattr = {"id": "r1", "frameDuration": f"{fd.numerator}/{fd.denominator}s", "width": str(info["w"]),
             "height": str(info["h"]), "colorSpace": "1-1-1 (Rec. 709)"}
    if fmt_name:
        fattr["name"] = fmt_name
    ET.SubElement(res, "format", **fattr)
    asset = ET.SubElement(res, "asset", id="r2", name=src.stem, start=t(tc0), duration=t(info["duration"]),
                          hasVideo="1", hasAudio="1", format="r1", audioSources="1", audioChannels="2", audioRate="48000")
    ET.SubElement(asset, "media-rep", kind="original-media", src="file://" + quote(str(src)))
    lib = ET.SubElement(root, "library", location="file://" + quote(str(lib_path.resolve())) + "/")
    ev = ET.SubElement(lib, "event", name=f"{d.get('date', '')} {d.get('service', '')}".strip() or src.stem)
    clip = ET.SubElement(ev, "asset-clip", ref="r2", name=src.stem, start=t(tc0), duration=t(info["duration"]),
                         format="r1", tcFormat="NDF", audioRole="dialogue")
    chosen = {(s["start"], s["end"]) for s in secs}
    for s in d["sections"]:     # favorite = in/out of each project's section (named), for later edits in FCP
        if (s["start"], s["end"]) in chosen:
            ET.SubElement(clip, "rating", name=name_of(d, s), start=t(tc0 + Fraction(s["start"])),
                          duration=t(Fraction(s["end"]) - Fraction(s["start"])), value="favorite")
    for s in d["sections"]:     # keyword range for every section found, also ones left out by --only
        ET.SubElement(clip, "keyword", start=t(tc0 + Fraction(s["start"])),
                      duration=t(Fraction(s["end"]) - Fraction(s["start"])), value=s["kind"])
    pe = ET.SubElement(lib, "event", name="프로젝트")
    for s in secs:
        dur = t(Fraction(s["end"]) - Fraction(s["start"]))
        proj = ET.SubElement(pe, "project", name=name_of(d, s))
        seq = ET.SubElement(proj, "sequence", format="r1", duration=dur, tcStart="0s", tcFormat="NDF",
                            audioLayout="stereo", audioRate="48k")
        spine = ET.SubElement(seq, "spine")
        ac = ET.SubElement(spine, "asset-clip", ref="r2", name=s.get("title") or s["kind"], offset="0s",
                           start=t(tc0 + Fraction(s["start"])), duration=dur, format="r1", tcFormat="NDF",
                           audioRole="dialogue")
        L = s.get("loud")
        if L:
            gain = TARGET_I - L["I"]
            if gain > 0:      # boost only as far as -1 dBTP allows; a source already over it is left at 0 dB
                gain = max(0.0, min(gain, TARGET_TP - L["TP"]))
                if L["TP"] > TARGET_TP:
                    print(f"  {s['kind']}: source peaks at {L['TP']:+.1f} dBTP — FCP gain 0 dB; add a Limiter in FCP "
                          f"(the H.264 export limits it)")
            if abs(gain) >= 0.1:
                ET.SubElement(ac, "adjust-volume", amount=f"{gain:+.1f}dB")
    ET.indent(root)
    out = p.parent / f"{lib_path.stem}.fcpxml"
    out.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode"))
    run(["xmllint", "--noout", "--dtdvalid", str(kit_paths.fcpxml_dtd()), str(out)])
    print(f"{out} (FCPXML {ver} DTD valid): {len(secs)} projects in 프로젝트, library {lib_path}")
    print(f'open: open -a "{kit_paths.FCP_APP}" "{out}"')


# ---------------------------------------------------------------- H.264 export
def bitrate(h, fps):
    hi = fps > 31
    table = {2160: (45, 60), 1440: (16, 24), 1080: (8, 12), 720: (5, 7.5)}   # YouTube SDR upload table, Mbps
    k = min(table, key=lambda x: abs(x - h))
    return table[k][1 if hi else 0]


def export(a):
    p, d, secs = loud(a, quiet=True)
    src = d["source"]
    info = probe(src)
    h = a.height or info["h"]
    br = bitrate(h, float(info["fps"]))
    out = Path(a.out or kit_paths.output_dir(p.parent / "export"))
    out.mkdir(parents=True, exist_ok=True)
    for s in secs:
        L = s["loud"]
        dst = out / (re.sub(r'[/:\\?*"<>|]', "_", name_of(d, s)) + ".mp4")
        af = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:measured_I={L['I']}:measured_TP={L['TP']}:"
              f"measured_LRA={L['LRA']}:measured_thresh={L['thresh']}:offset={L['offset']}:linear=true,"
              f"aresample=48000")
        vf = ["-vf", f"scale=-2:{h}:flags=lanczos"] if h != info["h"] else []
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-stats", "-ss", f"{s['start']:.3f}", "-to", f"{s['end']:.3f}",
               "-i", src, "-map", "0:v:0", "-map", "0:a:0", *vf,
               "-c:v", "h264_videotoolbox", "-profile:v", "high", "-b:v", f"{br}M", "-maxrate", f"{br * 1.5}M",
               "-bufsize", f"{br * 2}M", "-pix_fmt", "yuv420p", "-g", str(round(float(info["fps"]) / 2)),
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
               "-af", af, "-c:a", "aac", "-b:a", "384k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", "-y", str(dst)]
        print(f"→ {dst.name}  ({hms(s['end'] - s['start'])}, {h}p, {br} Mbps)")
        subprocess.run(cmd, check=True)
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(dst), "-map", "0:a:0", "-af",
                            "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
        I = re.findall(r"I:\s+(-?[\d.]+) LUFS", r)[-1]
        tp = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r)[-1]
        print(f"  {I} LUFS integrated, true peak {tp} dBTP")


# ---------------------------------------------------------------- thumbnails
def thumbs(a):
    p, d, secs = load(a.sections, a.kind)
    src = d["source"]
    s = secs[0]
    group = s["kind"] != "설교"                             # 찬양/성가대: several faces, not one preacher
    tdir = p.parent / "thumbs" / kind_dir(s["kind"])
    (tdir / "raw").mkdir(parents=True, exist_ok=True)
    if not FACE.exists():
        run(["swiftc", "-O", str(KIT / "scripts/face_quality.swift"), "-o", str(FACE)])
    times = list(range(int(s["start"]) + 30, int(s["end"]) - 30, a.every))
    with ThreadPoolExecutor(4) as ex:
        files = list(ex.map(lambda t: grab(src, t, tdir / "raw" / f"t{t:05d}.jpg", width=1920), times))
    files = [f for f in files if f.exists()]
    rows = []
    for k in range(0, len(files), 50):
        rows += [json.loads(x) for x in run([str(FACE), *map(str, files[k:k + 50])]).stdout.splitlines()]
    # quality first; a face too small to fill a thumbnail is worth less
    for r in rows:
        r["t"] = int(Path(r["file"]).stem[1:])
        if not r["faces"]:
            r["score"] = 0
        elif group:      # a well-lit group: the lead face sharp, and many faces in frame
            r["score"] = r["quality"] * (0.4 + 0.6 * min(1.0, r["faces"] / 8))
        else:
            r["score"] = r["quality"] * min(1.0, r["area"] / 0.02)
    best, picked = sorted(rows, key=lambda r: -r["score"]), []
    for r in best:
        if r["score"] and all(abs(r["t"] - q["t"]) >= 45 for q in picked):
            picked.append(r)
        if len(picked) == a.top:
            break
    for f in tdir.glob("best_*.jpg"):
        f.unlink()
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
    sheet = Image.new("RGB", (4 * 480, math.ceil(len(picked) / 4) * 300), "black")
    dr = ImageDraw.Draw(sheet)
    for i, r in enumerate(picked):
        name = tdir / f"best_{i + 1:02d}_{hms(r['t']).replace(':', '')}.jpg"
        grab(src, r["t"], name, width=3840 if probe(src)["w"] >= 3840 else 1920)
        r["still"] = str(name)
        x, y = i % 4 * 480, i // 4 * 300
        sheet.paste(Image.open(r["file"]).resize((480, 270)), (x, y))
        dr.text((x + 6, y + 272), f"#{i + 1} {hms(r['t'])} q{r['quality']:.2f}", fill="white", font=font)
    sheet.save(tdir / "best_sheet.jpg", quality=85)
    (tdir / "best.json").write_text(json.dumps(picked, ensure_ascii=False, indent=1))
    print(f"{len(rows)} frames scored, {len(picked)} best -> {tdir}/best_sheet.jpg (full-res best_*.jpg)")


TEMPLATES = KIT / "scripts/thumb_templates"


def thumbset(a):
    """Fill the three style templates for every chosen section that has a "thumb" block, then render them."""
    import html as H
    p, d, secs = load(a.sections, a.only)
    pages = []
    for s in secs:
        th = s.get("thumb")
        if not th:
            print(f"{s['kind']}: no \"thumb\" block in sections.json — skipped")
            continue
        tdir = p.parent / "thumbs" / kind_dir(s["kind"])
        best = json.loads((tdir / "best.json").read_text()) if (tdir / "best.json").exists() else []
        photo = Path(th.get("photo") or (best[0]["still"] if best else ""))
        if not photo.is_file():
            print(f"{s['kind']}: no photo (run thumbs --kind {s['kind']} or set thumb.photo) — skipped")
            continue
        hit = next((b for b in best if Path(b["still"]).name == photo.name), None)
        fx, fy = th.get("focus") or ([round(hit["cx"] * 100), round(hit["cy"] * 100)] if hit else [50, 35])
        rich = lambda t: re.sub(r"\*(.+?)\*", r"<em>\1</em>", H.escape(t or ""))
        val = {"photo": photo.resolve().as_uri(), "fx": fx, "fy": fy,
               "line1": rich(th.get("line1")), "line2": rich(th.get("line2")), "quote": rich(th.get("quote")),
               "title": H.escape(th.get("title") or s.get("title", "")), "name": H.escape(th.get("name", "")),
               "scripture": H.escape(th.get("scripture") or s.get("scripture", "")),
               "eyebrow": H.escape(th.get("eyebrow") or f"{d.get('service', '')} · {d.get('date', '').replace('-', '.')}")}
        for t in sorted(TEMPLATES.glob("*.html")):
            page = re.sub(r"\{\{(\w+)\}\}", lambda m: str(val.get(m[1], "")), t.read_text(encoding="utf-8"))
            out = tdir / f"style_{t.stem}.html"
            out.write_text(page, encoding="utf-8")
            pages.append(out)
    if pages:
        render(argparse.Namespace(pages=pages))


def render(a):
    for page in map(Path, a.pages):
        png = page.with_suffix(".png")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--window-size=1280,720", f"--screenshot={png.resolve()}", page.resolve().as_uri()],
                       check=True, capture_output=True)
        if png.stat().st_size > 2_000_000:                   # YouTube's custom thumbnail limit
            jpg = png.with_suffix(".jpg")
            run(["ffmpeg", "-v", "error", "-i", str(png), "-q:v", "3", "-y", str(jpg)])
            png.unlink()
            png = jpg
        print(f"{png} ({png.stat().st_size // 1024} KB)")


ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
x = sub.add_parser("scan"); x.add_argument("src"); x.add_argument("--job"); x.add_argument("--every", type=int, default=20)
for c in ("loud", "build", "export"):
    x = sub.add_parser(c); x.add_argument("sections"); x.add_argument("--only")
    if c == "export":
        x.add_argument("--height", type=int); x.add_argument("--out")
x = sub.add_parser("thumbs"); x.add_argument("sections"); x.add_argument("--kind", default="설교")
x.add_argument("--every", type=int, default=8); x.add_argument("--top", type=int, default=12)
x = sub.add_parser("render"); x.add_argument("pages", nargs="+")
x = sub.add_parser("thumbset"); x.add_argument("sections"); x.add_argument("--only")
a = ap.parse_args()
{"scan": scan, "loud": loud, "build": build, "export": export, "thumbs": thumbs, "render": render, "thumbset": thumbset}[a.cmd](a)

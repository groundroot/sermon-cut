#!/usr/bin/env python3
"""Upload a finished video to YouTube with the YouTube Data API v3 (the channel owner's own Google Cloud project).

  PY=~/.cache/video-editor/youtube-venv/bin/python
  $PY scripts/youtube_upload.py auth                  # once: browser sign-in by the channel owner -> token.json
  $PY scripts/youtube_upload.py check <upload.md>     # offline: validates title/description/tags/thumbnail/file
  $PY scripts/youtube_upload.py upload <upload.md> --yes
      resumable upload -> custom thumbnail -> playlist (optional); writes <upload>.uploaded.json with the video id/url.

Credentials live outside the kit: ~/.config/claude-video-kit/youtube/client_secret.json (downloaded by the owner from
Google Cloud Console, OAuth client type "Desktop app") and token.json (written by `auth`). Never commit or ship them.

upload.md = front matter + description:
  ---
  video: /abs/path/20260104 주일2부예배 설교 - 제목.mp4
  thumbnail: /abs/path/thumb_A.png
  title: 제목 | 본문 | 설교자
  tags: 설교, 주일예배, 교회이름
  privacy: private            # private | unlisted | public
  publish_at:                 # optional, ISO 8601 (e.g. 2026-11-29T10:00:00+09:00), needs privacy private
  category: 29                # 29 Nonprofits & Activism, 22 People & Blogs
  playlist:                   # optional playlist id (PL...)
  made_for_kids: false
  language: ko
  ---
  description text (≤ 5,000 bytes) …
"""
import json, sys, time
from pathlib import Path

CONF = Path.home() / ".config/claude-video-kit/youtube"
SECRET, TOKEN = CONF / "client_secret.json", CONF / "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


def parse(md):
    text = Path(md).read_text(encoding="utf-8")
    if not text.startswith("---"):
        sys.exit("upload.md must start with a --- front matter block")
    head, body = text[3:].split("\n---", 1)
    meta = {}
    for line in head.strip().splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.split("  #")[0].strip()
    meta["description"] = body.strip("\n")
    return meta


def problems(m):
    out = []
    title = m.get("title", "")
    if not title or len(title) > 100 or "<" in title or ">" in title:
        out.append(f"title must be 1–100 characters without < > ({len(title)} now)")
    d = m["description"]
    if len(d.encode()) > 5000 or "<" in d or ">" in d:
        out.append(f"description must be ≤ 5,000 bytes without < > ({len(d.encode())} now)")
    tags = [t.strip() for t in m.get("tags", "").split(",") if t.strip()]
    if sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1) > 500:
        out.append("tags are over YouTube's 500-character total")
    v = Path(m.get("video", ""))
    if not v.is_file():
        out.append(f"video not found: {v}")
    th = m.get("thumbnail")
    if th:
        t = Path(th)
        if not t.is_file():
            out.append(f"thumbnail not found: {t}")
        elif t.stat().st_size > 2_000_000 or t.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            out.append("thumbnail must be PNG/JPEG ≤ 2 MB")
    if m.get("privacy", "private") not in ("private", "unlisted", "public"):
        out.append("privacy must be private, unlisted or public")
    if m.get("publish_at") and m.get("privacy", "private") != "private":
        out.append("publish_at needs privacy: private (YouTube publishes it at that time)")
    return out, tags


def creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not TOKEN.exists():
        sys.exit(f"no {TOKEN}: run `auth` first")
    c = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not c.valid and c.refresh_token:
        c.refresh(Request())
        TOKEN.write_text(c.to_json())
    return c


def auth():
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not SECRET.exists():
        CONF.mkdir(parents=True, exist_ok=True)
        sys.exit(f"put the OAuth client file (Desktop app) at {SECRET} first — see the sermon-cut skill § YouTube")
    c = InstalledAppFlow.from_client_secrets_file(str(SECRET), SCOPES).run_local_server(port=0, prompt="consent")
    TOKEN.write_text(c.to_json())
    TOKEN.chmod(0o600)
    print(f"signed in; token saved to {TOKEN}")


def upload(md):
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    m = parse(md)
    bad, tags = problems(m)
    if bad:
        sys.exit("not uploaded:\n  " + "\n  ".join(bad))
    yt = build("youtube", "v3", credentials=creds())
    status = {"privacyStatus": m.get("privacy", "private"),
              "selfDeclaredMadeForKids": m.get("made_for_kids", "false").lower() == "true"}
    if m.get("publish_at"):
        status["publishAt"] = m["publish_at"]
    body = {"snippet": {"title": m["title"], "description": m["description"], "tags": tags,
                        "categoryId": m.get("category", "29"), "defaultLanguage": m.get("language", "ko"),
                        "defaultAudioLanguage": m.get("language", "ko")}, "status": status}
    media = MediaFileUpload(m["video"], chunksize=32 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp, tries = None, 0
    while resp is None:
        try:
            st, resp = req.next_chunk()
            if st:
                print(f"  {st.progress() * 100:5.1f} %", flush=True)
        except HttpError as e:
            if e.resp.status not in (500, 502, 503, 504) or tries >= 8:
                raise
            tries += 1
            time.sleep(min(60, 2 ** tries))
    vid = resp["id"]
    print(f"uploaded: https://youtu.be/{vid} ({status['privacyStatus']})")
    done = {"id": vid, "url": f"https://youtu.be/{vid}", "privacy": status["privacyStatus"]}
    if m.get("thumbnail"):
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(m["thumbnail"])).execute()
            done["thumbnail"] = "set"
        except HttpError as e:     # custom thumbnails need a verified channel (phone verification)
            done["thumbnail"] = f"failed: {e.reason}"
        print(f"thumbnail: {done['thumbnail']}")
    if m.get("playlist"):
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": m["playlist"], "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
        done["playlist"] = m["playlist"]
    Path(md).with_suffix(".uploaded.json").write_text(json.dumps(done, ensure_ascii=False, indent=1))


a = sys.argv[1:]
if a[:1] == ["auth"]:
    auth()
elif a[:1] == ["check"] and len(a) == 2:
    meta = parse(a[1])
    bad, tg = problems(meta)
    print("\n".join(bad) if bad else f"ok: {meta['title']} · {meta.get('privacy', 'private')} · {len(tg)} tags · "
          f"{len(meta['description'].encode())} bytes description")
elif a[:1] == ["upload"] and len(a) >= 2:
    if "--yes" not in a:
        sys.exit("uploading publishes to the channel: run with --yes only after the user approved this upload")
    upload(a[1])
else:
    sys.exit(__doc__)

---
name: sermon-cut
description: 설교컷편집 — cut a full worship-service recording (talking-head sermon service from the broadcast room) into its order of service — 찬양, 대표기도, 성가대, 광고, 설교, 봉헌, 축도 … — one Final Cut project per section in one library, plus H.264 MP4s exported straight away at YouTube loudness (−14 LUFS), a preacher thumbnail in three styles for approval, title/description markdown from the sermon manuscript, and YouTube upload through the YouTube Data API. Cut only: no subtitles, no music, no review step. Use whenever the user says 설교컷편집, 설교 컷, 예배 영상 잘라, 예배 순서대로 잘라, 설교만 뽑아, 찬양만 뽑아, 성가대 영상, or points at a PGM/예배 녹화 file or a broadcast-room PGM folder.
---

# 설교컷편집 (sermon-cut)

**Standalone install (Sermon Cut folder):** run every command from this folder; `PY=~/.cache/video-editor/stt-venv/bin/python` (sermon_cut.py, stt_words.py), `YT=~/.cache/video-editor/youtube-venv/bin/python` (youtube_upload.py).

Source: the broadcast room's program (PGM) recordings, e.g. `<NAS>/<broadcast>/PGM/<YYYY>/<YYYYMM>/<YYYYMMDD>_주일<N>부예배_pgm.mov`
(4K 59.94 ProRes or H.264, stereo, ~1 h 45 min, 150–300 GB). Folder names on the NAS are NFD: resolve paths by
comparing `unicodedata.normalize("NFC", …)` names, never by typing them (ffmpeg says "No such file").

This skill needs no general interview: its first question is **what to export** (step 2). If the user already said
("설교만 컷편집해"), do exactly that and nothing else — no question, no other sections.

## 1. Find the sections (evidence, then Claude decides)
```bash
SRC=<exact path>; W="transcript/<stem>.wav"
# extract 16 kHz audio once (reads the whole file over the NAS; minutes to tens of minutes for 300 GB)
ffmpeg -v error -i "$SRC" -map 0:a:0 -ac 1 -ar 16000 -y "$W.part.wav" && mv "$W.part.wav" "$W"
$PY scripts/stt_words.py "$SRC"      # reuses that wav; ~25 min per hour
$PY scripts/sermon_cut.py scan "$SRC"     # -> transcript/sermon/<stem>/overview.md, sheet_NN.jpg, audio.json
```
- `scan` gives a frame every 20 s on contact sheets (time + audio class under each frame: orange music, blue speech,
  grey mixed, black quiet) and `overview.md`: one line per minute with the 10-s class bar (M S x .) and the words.
- Read overview.md fully and look at every sheet. Mark boundaries from both: music runs = 찬양/성가대/봉헌송 (choir
  robes, praise team on stage, lyrics on screen), speech at the pulpit with long runs of words = 설교 (scripture
  reading before it is usually its own 성경봉독), a lay leader praying = 대표기도, slides/MC = 광고, raised hands at the
  end = 축도. Use the words for exact starts and ends ("설교 말씀을 전하시겠습니다", "기도하겠습니다", "아멘",
  "광고를 드리겠습니다"); refine a boundary by grabbing frames around it (`ffmpeg -ss t -i SRC -frames:v 1`).
- Start each section on the first frame of its content and end it after the last word or chord ring-out
  (+0.5–1 s); never cut into a word. Don't include the countdown/black at the start of the recording.
- Write `transcript/sermon/<stem>/sections.json`:
```json
{"source": "<exact path>", "date": "2026-01-04", "service": "주일2부예배", "library": "",
 "sections": [{"kind": "찬양", "title": "경배와 찬양", "start": 120.5, "end": 1012.0},
              {"kind": "설교", "title": "<sermon title>", "preacher": "<name>", "scripture": "<본문>",
               "start": 2405.0, "end": 4810.2}]}
```
  `kind` from: 찬양, 성경봉독, 대표기도, 성가대, 광고, 설교, 봉헌, 축도, 기타. `library` empty = next to sections.json.

## 2. Ask what to export
Show the table of found sections (kind, title, start–end, length) and ask with AskUserQuestion (multiSelect) which
to export; recommend 설교 (+ 성가대/찬양 if the church posts them). Skip when the user already said which.

## 3. Cut, FCP library and H.264 — no review step
```bash
$PY scripts/sermon_cut.py loud   <job>/sections.json --only 설교,성가대    # measures, caches in sections.json
$PY scripts/sermon_cut.py build  <job>/sections.json --only 설교,성가대    # one FCPXML: library + projects
$PY scripts/sermon_cut.py export <job>/sections.json --only 설교,성가대    # H.264 MP4s in <job>/export/
open -a "<FCP_APP>" "<job>/<library>.fcpxml"
```
- **Library:** one per service (`<date> <service>.fcpbundle`): the date event holds the whole source linked in place
  with a keyword range per section (찬양, 설교 …) and a **Favorite on each chosen section's in/out**, named like its
  project (user, 2026-11-25: "클립은 반드시 in/out point에 favorite해서 구간 설정" — for later edits); the 프로젝트 event holds one project per exported section named
  `<YYYYMMDD> <service> <kind> - <title>`, with a static gain toward −14 LUFS (capped at −1 dBTP). Nothing is copied
  or transcoded on import.
- **Export:** frame-exact cut from the source, two-pass `loudnorm` to **−14 LUFS integrated, −1 dBTP, LRA 11**
  (YouTube normalises playback to about −14 LUFS; louder uploads are turned down, quieter ones are not turned up),
  H.264 High (VideoToolbox), source resolution and frame rate, bitrate from YouTube's SDR table (2160p60 60 Mbps,
  1080p60 12 Mbps), AAC 384 kbps 48 kHz stereo, BT.709 tags, faststart. `--height 1080` for a smaller file.
  The export re-measures each file and prints LUFS / true peak: report them.
- Order (user, 2026-11-25 test): build the library first and let the user open it; export when they say so
  ("바로 뽑지 말고"). The original brief was export without review — ask once per job if unclear.

## 4. Thumbnails — 설교, 찬양, 성가대 each, three styles, approval first
```bash
$PY scripts/sermon_cut.py thumbs   <job>/sections.json --kind 설교     # then --kind 찬양, --kind 성가대
$PY scripts/sermon_cut.py thumbset <job>/sections.json --only 설교,찬양,성가대
```
- `thumbs` (needs the source) scores a frame every 8 s with Apple Vision face capture quality and keeps the 12 best,
  ≥ 45 s apart, full size -> `<job>/thumbs/<kind>/best_*.jpg`, `best_sheet.jpg`, `best.json`. 설교: the largest face
  (the preacher). 찬양/성가대: a sharp lead face and many faces in frame. Look at best_sheet.jpg and pick yourself
  (eyes open, mouth not distorted, expressive gesture, no lyric subtitle over the face); say why.
- Each section gets a `"thumb"` block in sections.json; `*word*` is the highlight word:
```json
"thumb": {"photo": "<job>/thumbs/설교/best_01_<time>.jpg", "focus": [50, 30],
          "line1": "<제목 앞줄 *강조*>", "line2": "<제목 뒷줄>", "name": "<설교자>",
          "quote": "<설교의 한 문장, *강조*>", "eyebrow": "", "scripture": ""}
```
  `photo` empty = the top still of that kind; `focus` empty = the face centre from best.json; `eyebrow` empty =
  "<service> · <date>"; `scripture` empty = the section's. 찬양/성가대: line1/line2 = song title or "주일2부예배 찬양",
  name = team/choir name (e.g. 시온 찬양대), quote = one lyric line.
- `thumbset` fills the three templates in `scripts/thumb_templates/` for every section with a thumb block and renders
  them (headless Chrome, 1280×720, JPEG if over 2 MB) -> `<job>/thumbs/<kind>/style_*.png`:
  A `a_bold` (photo right, deep navy, Pretendard Black, amber highlight), B `b_editorial` (ivory panel, Nanum
  Myeongjo title, red edge, photo right), C `c_quote` (darkened full photo, one quote line). Title lines never wrap:
  each line shrinks to fit its box, so keep a line ≤ ~12 Korean characters for a phone-readable size.
- Show the 3 × 3 renders (SendUserFile or one Artifact page) and ask which style per section (or one style for
  all); adjust the template or the thumb block and re-run until approved. Only the approved PNG goes into the
  upload md. Fonts used are OFL/free (Pretendard, Nanum Myeongjo); for a church font, install it and change font-family in the template.

## 5. Title and description (`<job>/upload/<name>.md`)
If the user gives the sermon manuscript (원고), read it and write the front matter + description:
- title ≤ 100 characters (shown ~60): sermon title | scripture | preacher (the church's own pattern if they have one);
- description: 2–3 sentence summary in the manuscript's own words, scripture reference, preacher, service and date,
  3–5 chapter timestamps (`00:00` first, ≥ 10 s apart) from the transcript, church links if given, 3 hashtags;
- tags: preacher, church, scripture book, theme words. Without a manuscript, use the transcript and say so.
Format and fields: `scripts/youtube_upload.py` docstring. Check offline: `$YT scripts/youtube_upload.py check <md>`.

## 6. YouTube upload (YouTube Data API v3)
Uploading publishes on the church's channel: each upload needs the user's explicit yes in chat (title, file,
privacy). Default privacy `private`; the user switches it to public after a look. Never type the user's Google
password or 2-step codes, and never commit `client_secret.json` / `token.json`.

**One-time setup — guide the user through it (they click; Claude explains):**
1. console.cloud.google.com → new project (e.g. "church-youtube-upload").
2. APIs & Services → Library → **YouTube Data API v3** → Enable.
3. OAuth consent screen (Google Auth Platform): app name, support email; audience **External**; add the channel
   owner's Google account under **Test users** (while the app is in Testing, only test users can sign in, and the
   sign-in lasts 7 days before `auth` has to be run again; publishing the app to Production removes that limit).
4. Credentials → Create credentials → **OAuth client ID** → Application type **Desktop app** → download the JSON and
   save it as `~/.config/claude-video-kit/youtube/client_secret.json`.
5. `$YT scripts/youtube_upload.py auth` → the browser opens; the
   owner signs in with the account that owns (or manages) the channel and allows access → `token.json`.
Then per video: `check` → show the user title/privacy/file → on their yes `upload <md> --yes`.

Limits to tell the user (Google's pages disagree in places; check the project's Quotas page):
- Default quota 10,000 units/day; an upload costs about 1,600 units in the current calculator → ~6 uploads a day.
- Videos uploaded from an **unverified** API project (created after 2020-07-28) are locked to private until the
  project passes Google's API audit (YouTube API Services audit form). For a church channel it is usually simpler to
  upload private and make it public in YouTube Studio.
- Custom thumbnails need a verified channel (phone verification at youtube.com/verify); otherwise `thumbnails.set`
  fails and the script says so — set it in Studio.

venvs: `./install.sh` in this folder makes both (`--check` to verify).

## Traps
- 300 GB ProRes over the NAS: extract the audio once (stt_words reuses `transcript/<stem>.wav` if present); frame
  grabs use input seeking and are fast. A broken wav from an interrupted run must be deleted by hand.
- Speech/music class is a heuristic (low-energy-frame ratio): a sung prayer or a sermon with background pad can read
  "mixed"; decide by words + frames, not by the class alone.
- FCP 13 (2026-11-25 test): the import may create the library somewhere else than the XML's `library location`
  (it landed in ~/Movies under another name) — check where it landed and tell the user.
- STT on a 1 h 46 min service took 6.5 min and the audio extraction ~2 min over a 10 GbE NAS. ASR gets names wrong:
  take the preacher's and choir's names from the bulletin or ask, never from ASR alone.
- Sources peaking above −1 dBTP get 0 dB in FCP (a Limiter is needed there); export's loudnorm limits them.

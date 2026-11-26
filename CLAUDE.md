# 설교컷편집 (Sermon Cut) — 단독 설치본

This folder is the sermon-cut skill of 클로드 비디오 킷 on its own: a whole worship-service recording → one Final Cut
project per section, H.264 at YouTube loudness, thumbnails, upload notes, YouTube upload. Start with the
`sermon-cut` skill (or the user says 설교컷편집, 설교만 뽑아, 예배 영상 잘라).

- Run every command from this folder (`cd` here first): scripts write into `transcript/` here.
- Python: `PY=~/.cache/video-editor/stt-venv/bin/python` for sermon_cut.py and stt_words.py,
  `YT=~/.cache/video-editor/youtube-venv/bin/python` for youtube_upload.py. `./install.sh` makes both.
- Setup check: `./install.sh --check`. If something is missing, run `./install.sh` (Homebrew must be installed by the
  user first: it needs their admin password).
- Sources stay where they are (NAS, external disk): the user gives the path (Finder: select the file, ⌥⌘C). Nothing
  is copied or re-encoded on import; one library per service.
- Each YouTube upload needs the user's yes in chat. Never type the user's Google password or codes.
- Answer the user in Korean.

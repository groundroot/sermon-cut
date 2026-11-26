# 설교컷편집 시작하기

예배 녹화 파일 하나를 넣으면 예배 순서(찬양, 기도, 성가대, 설교 …)대로 나눠
파이널컷 프로젝트와 유튜브용 MP4, 썸네일, 업로드 글을 만들어 주는 도구입니다.

## 1. 설치 (한 번만)

1. 이 폴더를 `동영상`(Movies) 폴더 안에 둡니다. → `~/Movies/Sermon Cut`
2. Homebrew가 없다면 https://brew.sh 의 한 줄 명령을 터미널에 붙여 넣어 먼저 설치합니다 (맥 비밀번호를 묻습니다).
3. 터미널에서:

   ```bash
   cd ~/Movies/"Sermon Cut" && ./install.sh
   ```

   ffmpeg, Python 3.11, 음성 인식(Qwen3-ASR), 유튜브 업로드 도구, 글꼴(Pretendard, 나눔명조)을 설치합니다.
   처음 한 번은 20–40분 걸립니다. 음성 인식 모델은 처음 쓸 때 받습니다.
4. 확인: `./install.sh --check` — 모두 ✓이면 끝입니다.

필요한 것: Apple Silicon 맥, Final Cut Pro (12.4 또는 13), Google Chrome (썸네일), Claude Code.

## 2. 쓰기

```bash
cd ~/Movies/"Sermon Cut" && claude
```

그리고 이렇게 말합니다.

- "설교컷편집 해줘" + 녹화 파일 경로 (Finder에서 파일 선택 후 ⌥⌘C로 경로 복사)
- "설교만 뽑아줘", "설교랑 성가대만"
- "썸네일 만들어줘", "유튜브에 올려줘"

순서: 구간 찾기 → 무엇을 내보낼지 묻기 → 파이널컷 보관함 → MP4 (−14 LUFS) → 썸네일 3가지 중 고르기 →
설교 원고로 제목·설명 → 유튜브 업로드 (올릴 때마다 확인, 기본은 비공개).

## 3. 유튜브 업로드 준비 (처음 한 번)

Google Cloud에서 프로젝트를 만들고 YouTube Data API를 켠 뒤, OAuth 클라이언트(데스크톱 앱) JSON을
`~/.config/claude-video-kit/youtube/client_secret.json`에 둡니다. Claude에게 "유튜브 업로드 설정 도와줘"라고 하면
한 단계씩 안내합니다.

## 파일이 생기는 곳

- `transcript/sermon/<녹화 파일 이름>/` — 구간 표(sections.json), 확인용 사진, 파이널컷 XML, `export/`(MP4), `thumbs/`, `upload/`
- 파이널컷 보관함 — 같은 폴더의 `<날짜> <예배>.fcpbundle`

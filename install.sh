#!/usr/bin/env bash
# 설교컷편집 단독 설치. 다시 실행해도 안전하다: 이미 있는 것은 건너뛴다.
#   ./install.sh           설치
#   ./install.sh --check   아무것도 바꾸지 않고 상태만 본다 (빠진 것이 있으면 종료 코드 1)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$HOME/.cache/video-editor"
FONTS="$HOME/Library/Fonts"
PRETENDARD_URL="https://github.com/orioncactus/pretendard/releases/download/v1.3.9/Pretendard-1.3.9.zip"
NANUM_URL="https://github.com/google/fonts/raw/main/ofl/nanummyeongjo"
MODE=install; [ "${1:-}" = --check ] && MODE=check
FCP_APP="${FCP_APP:-$(find /Applications -maxdepth 3 -name 'Final Cut Pro Creator Studio.app' -print -quit 2>/dev/null)}"
[ -n "$FCP_APP" ] || FCP_APP="/Applications/Final Cut Pro.app"

FAIL=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=1; }
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
run()  { local what="$1"; shift
  if [ "$MODE" = check ]; then bad "$what (설치 필요)"; return 1; fi
  echo "  → $what"; "$@" || { bad "실패: $what"; return 1; }; }
venv() {  # venv <이름> <requirements> <확인할 모듈들>
  local dir="$CACHE/$1" req="$HERE/requirements/$2" mods="$3"
  local probe="import importlib.util as u, sys; sys.exit(any(u.find_spec(x) is None for x in '$mods'.split()))"
  if [ -x "$dir/bin/python" ] && "$dir/bin/python" -c "$probe" 2>/dev/null; then ok "$1 ($dir)"; return; fi
  run "$1 만들기 (Python 3.11, $2)" bash -c "{ [ -x '$dir/bin/python' ] || uv venv --python 3.11 '$dir'; } && uv pip install --python '$dir/bin/python' -r '$req' && '$dir/bin/python' -c \"$probe\""
}

echo "설교컷편집: $HERE  (모드: $MODE)"

step "기본 환경"
[ "$(uname -m)" = arm64 ] && ok "Apple Silicon macOS" || bad "Apple Silicon 맥이 필요하다 (음성 인식이 MLX를 쓴다)"
[ -d "$FCP_APP" ] && ok "Final Cut Pro ($(defaults read "$FCP_APP/Contents/Info" CFBundleShortVersionString 2>/dev/null))" \
  || bad "Final Cut Pro가 없다: App Store에서 설치"
[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] && ok "Google Chrome (썸네일 렌더)" \
  || bad "Google Chrome이 없다: 썸네일을 만들려면 https://www.google.com/chrome 에서 설치"
command -v claude >/dev/null && ok "Claude Code" || bad "Claude Code가 없다: https://claude.com/claude-code"
if xcode-select -p >/dev/null 2>&1 && command -v swiftc >/dev/null && command -v xmllint >/dev/null; then ok "Xcode 명령줄 도구 (swiftc, xmllint)"
else bad "Xcode 명령줄 도구가 없다: 터미널에서 xcode-select --install 을 직접 실행"; fi
if command -v brew >/dev/null; then ok "Homebrew"; else
  bad "Homebrew가 없다. 맥 비밀번호가 필요하므로 직접 설치: https://brew.sh 의 한 줄 명령"
  [ "$MODE" = install ] && { echo "Homebrew를 설치한 뒤 다시 실행하세요."; exit 1; }
fi

step "Homebrew 도구"
for f in ffmpeg uv; do
  if command -v "$f" >/dev/null; then ok "$f ($(command -v "$f"))"; else run "brew install $f" brew install "$f"; fi
done
if command -v uv >/dev/null && uv python list --only-installed 2>/dev/null | grep -q "cpython-3\.11"; then ok "Python 3.11 (uv)"
else run "uv python install 3.11" uv python install 3.11; fi

step "Python 환경"
venv stt-venv stt.txt "mlx_audio silero_vad numpy PIL soundfile"
venv youtube-venv youtube.txt "googleapiclient google_auth_oauthlib"

step "FCPXML DTD"
DTD="$(ls "$FCP_APP"/Contents/Frameworks/Interchange.framework/Versions/A/Resources/FCPXMLv1_*.dtd 2>/dev/null | sort -V | tail -1)"
if [ -z "$DTD" ]; then bad "Final Cut Pro에서 FCPXML DTD를 찾지 못했다"
elif [ -f "$CACHE/$(basename "$DTD")" ]; then ok "$CACHE/$(basename "$DTD")"
else run "DTD 복사 ($(basename "$DTD"))" bash -c "mkdir -p '$CACHE' && cp '$(dirname "$DTD")'/FCPXMLv1_*.dtd '$CACHE/'"; fi

step "썸네일 글꼴 (SIL OFL)"
has_font() { [ -n "$(find "$FONTS" /Library/Fonts -maxdepth 2 -iname "$1" 2>/dev/null | head -1)" ]; }
if has_font "*pretendard*black*"; then ok "Pretendard"
else run "Pretendard 받기 (공식 GitHub 릴리스) → ~/Library/Fonts" bash -c "t=\$(mktemp -d) && curl -fsSL '$PRETENDARD_URL' -o \$t/p.zip && unzip -q \$t/p.zip -d \$t && mkdir -p '$FONTS' && find \$t -name 'Pretendard-*.otf' -path '*static*' -exec cp -n {} '$FONTS/' \; && ls '$FONTS'/Pretendard-Black.otf >/dev/null"; fi
if has_font "*nanum*myeongjo*"; then ok "나눔명조"
else run "나눔명조 받기 (Google Fonts GitHub) → ~/Library/Fonts" bash -c "mkdir -p '$FONTS' && for w in Regular Bold ExtraBold; do curl -fsSL '$NANUM_URL/NanumMyeongjo-'\$w'.ttf' -o '$FONTS/NanumMyeongjo-'\$w'.ttf' || exit 1; done"; fi

step "폴더"
[ -f "$HERE/.claude/skills/sermon-cut/SKILL.md" ] && ok "sermon-cut 스킬 (이 폴더에서 claude를 열면 읽힌다)" || bad "스킬 파일이 없다"
[ -d "$HERE/transcript" ] && ok "transcript/" || run "transcript/ 만들기" mkdir -p "$HERE/transcript"

echo
if [ "$FAIL" = 0 ]; then echo "준비 완료. 시작: cd \"$HERE\" && claude  →  \"설교컷편집 해줘\""
else echo "빠진 항목이 있다 (✗). 위 안내대로 처리한 뒤 ./install.sh 를 다시 실행하세요."; fi
exit $FAIL

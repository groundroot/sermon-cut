"""엔진 로그 i18n — FCPA_LANG env(기본 ko)로 언어 선택, en 폴백.

기본이 ko라 기존 테스트·로그 파서(EtaModel·funStage 매칭)가 무변경으로 동작한다.
앱은 L10n 현재 언어를 FCPA_LANG로 주입한다. 사용법: log(T("key", x=1)).

주의: 결과물 파일명 접미(_무음제거/_편집 등)는 앱 프로토콜 — 여기서 다루지 않는다(불변).
"""
from __future__ import annotations

import os

LANG = "en" if os.environ.get("FCPA_LANG", "ko").lower().startswith("en") else "ko"

# key: {"en":…, "ko":…} — {} 자리는 str.format
_M: dict = {
    # ── 작업 완료 요약 (JobRunner extractSummary가 양언어 앵커로 파싱) ──────
    "sum.header": {"ko": "작업 완료 요약 (v0.12 엔진 — 메인 스토리라인 오디오 기준)",
                   "en": "Job Summary (v0.12 engine — main storyline audio)"},
    "sum.items": {"ko": "스파인 항목    : 전체 {n} / 분석(오디오) {a} / 통과 {p}",
                  "en": "Spine items    : {n} total / {a} analyzed (audio) / {p} passed"},
    "sum.subs": {"ko": "자막           : {n}개", "en": "Captions       : {n}"},
    "sum.clip_len": {"ko": "원본 클립 길이  : {m}분 {s:.1f}초  ({t:.1f}s)",
                     "en": "Source length  : {m}m {s:.1f}s  ({t:.1f}s)"},
    "sum.cut": {"ko": "잘라낸 분량     : {m}분 {s:.1f}초  ({t:.1f}s, {pct:.1f}%)",
                "en": "Removed length : {m}m {s:.1f}s  ({t:.1f}s, {pct:.1f}%)"},
    "sum.kept": {"ko": "최종 분량(무음제거): {m}분 {s:.1f}초  ({t:.1f}s)",
                 "en": "Final length (silence-cut): {m}m {s:.1f}s  ({t:.1f}s)"},
    # ── 분석(analyze_words) ────────────────────────────────────────────────
    "err.xml_broken": {
        "ko": "FCPXML 파일이 손상됐습니다({detail}) — 파이널컷에서 다시 내보내 주세요. "
              "내보내기가 중간에 멈췄거나 파일이 잘렸을 때 생깁니다.",
        "en": "The FCPXML file is damaged ({detail}) — please export it again from "
              "Final Cut Pro. This happens when an export is interrupted or truncated."},
    "err.media_missing": {
        "ko": "미디어 파일을 찾을 수 없습니다: {path}\n"
              "파일을 옮겼거나 이름을 바꿨다면 파이널컷에서 다시 연결한 뒤 내보내 주세요.",
        "en": "Media file not found: {path}\n"
              "If you moved or renamed it, relink it in Final Cut Pro and export again."},
    "err.media_unreadable": {
        "ko": "미디어 파일을 열 수 없습니다: {name}\n"
              "파일이 손상됐거나(다운로드·복사 중단) 지원하지 않는 형식입니다. "
              "파이널컷에서 그 클립이 정상 재생되는지 확인하고, 안 되면 미디어를 "
              "다시 받아 연결한 뒤 내보내 주세요.",
        "en": "Cannot open the media file: {name}\n"
              "It is damaged (an interrupted download or copy) or in an unsupported "
              "format. Check that the clip plays in Final Cut Pro; if it does not, "
              "replace the media, relink it and export again."},
    "err.output_unwritable": {
        "ko": "결과물을 저장할 수 없습니다: {path}\n"
              "({detail}) 이 폴더에 쓸 권한이 없거나 디스크가 가득 찼습니다. "
              "다른 위치를 고르거나 공간을 확보한 뒤 다시 시도해 주세요. "
              "원본 프로젝트와 미디어는 그대로입니다.",
        "en": "Cannot save the results to: {path}\n"
              "({detail}) The folder is not writable or the disk is full. "
              "Choose another location or free up space and try again. "
              "Your original project and media are untouched."},
    "err.media_no_permission": {
        "ko": "미디어 파일을 읽을 권한이 없습니다: {name}\n"
              "시스템 설정 › 개인정보 보호 및 보안 › 전체 디스크 접근에서 이 앱을 허용한 뒤 "
              "다시 시도해 주세요. 외장 드라이브·다운로드·데스크탑 폴더의 파일에서 자주 생깁니다.",
        "en": "No permission to read the media file: {name}\n"
              "Allow this app in System Settings › Privacy & Security › Full Disk Access, "
              "then try again. This usually happens with files on external drives, "
              "Downloads or Desktop."},
    "err.no_audio_track": {
        "ko": "이 영상 파일에는 오디오 트랙이 없습니다: {name}\n"
              "영상만 받아진 파일(유튜브 화질별 다운로드 등)일 수 있습니다 — "
              "소리가 들어 있는 파일로 교체한 뒤 다시 내보내 주세요.",
        "en": "This video file has no audio track: {name}\n"
              "It may be a video-only download — replace it with a file that "
              "contains audio and export again."},
    "an.no_speech": {"ko": "[분석] 음성 없음 — 건너뜀 (클립은 보존)",
                     "en": "[analyze] no speech — skipped (clip preserved)"},
    "an.segments": {"ko": "[분석] 음성 구간 {n}개 → ASR",
                    "en": "[analyze] {n} speech segments → ASR"},
    "an.lang_fix": {"ko": "[언어 보정] {t:.1f}s 구간 {old}→{new} 재전사",
                    "en": "[lang-fix] {t:.1f}s segment {old}→{new} re-transcribed"},
    "an.asr_time": {"ko": "[시간] ASR+정렬 {sec:.1f}s ('{name}')",
                    "en": "[time] ASR+align {sec:.1f}s ('{name}')"},
    "an.diar_turns": {"ko": "[diarize] '{name}' 화자 턴 {n}개",
                      "en": "[diarize] '{name}' — {n} speaker turns"},
    "an.diar_fail": {"ko": "[diarize] ⚠️ '{name}' 화자 분리 실패({err}) — 이 소스는 화자 분리 없이",
                     "en": "[diarize] ⚠️ '{name}' diarization failed ({err}) — continuing without it"},
    # ── 전사 스트림 (UI 실시간 패널; [전사 i/n] 접두는 매칭 프로토콜 — 불변) ──
    # ── 자막/출력 ─────────────────────────────────────────────────────────
    "sub.placed": {"ko": "[v12] 자막 {n}개 배치", "en": "[v12] placed {n} captions"},
    "sub.fast_cards": {"ko": "  읽기속도 경고: {n}개 카드가 21자/초 초과 (가독성 참고)",
                       "en": "  reading-speed warning: {n} cards exceed 21 chars/sec"},
    "sil.removed": {"ko": "[silence] 제거 구간 {n}개 / 총 {sec:.1f}s",
                    "en": "[silence] removed {n} ranges / total {sec:.1f}s"},
    "sil.skip_connected": {"ko": "[silence] ⚠️ 연결 스토리라인/연결 오디오가 있어 무음컷을 생략합니다 (자막은 정상 생성)",
                           "en": "[silence] ⚠️ connected storyline/audio present — skipping silence cut (captions still generated)"},
    "sil.reextract": {"ko": "[silence] 추출 오디오가 없어 원본에서 재추출 (캐시 재출력)",
                      "en": "[silence] extracted audio missing — re-extracting from source (cache re-export)"},
    "story.built": {"ko": "[storyline] 자막 스토리라인 {n}개 구성{by}",
                    "en": "[storyline] built {n} caption storylines{by}"},
    "story.by_speaker": {"ko": "(화자별)", "en": " (per speaker)"},
    "story.sil_built": {"ko": "[storyline] 무음제거본 자막 스토리라인 {n}개{by}",
                        "en": "[storyline] silence-cut caption storylines: {n}{by}"},
    # ── 캐시/편집 ─────────────────────────────────────────────────────────
    "cache.reuse": {"ko": "[v12] 분석 캐시 재사용 — 음성 인식 생략 ({name})",
                    "en": "[v12] reusing analysis cache — skipping transcription ({name})"},
    "edit.split": {"ko": "[편집] 토큰 나누기 {n}건 적용",
                   "en": "[edit] applied {n} token split(s)"},
    "edit.applied": {"ko": "[edit] 대본 편집 적용 — 수정 {e}건, 삭제 {d}단어",
                     "en": "[edit] transcript edits applied — {e} edits, {d} words deleted"},
    # ── 템플릿/기타 ────────────────────────────────────────────────────────
    "tmpl.title": {"ko": "[v12] 타이틀 템플릿: {name} (씬 {w}×{h})",
                   "en": "[v12] title template: {name} (scene {w}×{h})"},
    "split.fit": {"ko": "[분리] 한 줄 폭 제한: 최대 {a}자 → {b}자 (글자 {pt}pt가 화면 폭을 넘지 않게{hint})",
                  "en": "[split] one-line width cap: {a} → {b} chars (so {pt}pt text fits the frame{hint})"},
    "split.fit_hint2": {"ko": " — 더 길게 담으려면 2줄을 켜세요", "en": " — enable two lines to fit more"},
    # ── transcribe ────────────────────────────────────────────────────────
    "tr.apple_start": {"ko": "[transcribe] Apple 음성 인식(SpeechAnalyzer) 시작 — 로케일 {loc}",
                       "en": "[transcribe] Apple SpeechAnalyzer start — locale {loc}"},
    "tr.apple_done": {"ko": "[transcribe] Apple 음성 인식 완료 — 단어 {n}개, {s:.1f}s",
                      "en": "[transcribe] Apple SpeechAnalyzer done — {n} words in {s:.1f}s"},
    "tr.gate": {"ko": "[transcribe] 비발화 게이트 {t:.1f}s (speechiness={v:.2f}) — 자막 생략",
                "en": "[transcribe] non-speech gate {t:.1f}s (speechiness={v:.2f}) — caption skipped"},
    "tr.halluc": {"ko": "[transcribe] 환청 필터 {t:.1f}s — '{txt}' 자막 생략",
                  "en": "[transcribe] hallucination filter {t:.1f}s — '{txt}' skipped"},
    "tr.asr_load": {"ko": "[transcribe] ASR 모델 로딩: {name}", "en": "[transcribe] loading ASR model: {name}"},
    "tr.align_load": {"ko": "[transcribe] ForcedAligner 로딩: {name}",
                      "en": "[transcribe] loading ForcedAligner: {name}"},
    # ── diarize ───────────────────────────────────────────────────────────
    "di.none": {"ko": "[diarize] 라이브러리 없음, 화자 분리 건너뜀: {err}",
                "en": "[diarize] library missing, skipping diarization: {err}"},
    "di.short": {"ko": "[diarize] 오디오 너무 짧음, 화자 분리 건너뜀",
                 "en": "[diarize] audio too short, skipping diarization"},
    "di.count": {"ko": "[diarize] 화자 {n}명으로 분류 (임베딩 {m}개)",
                 "en": "[diarize] classified into {n} speakers ({m} embeddings)"},
    "di.segs": {"ko": "[diarize] 화자 구간 {n}개 감지", "en": "[diarize] {n} speaker segments"},
    "di.pyannote": {"ko": "[diarize] pyannote community-1 — 화자 {n}명, 구간 {m}개(겹침 포함)",
                    "en": "[diarize] pyannote community-1 — {n} speakers, {m} segments (incl. overlap)"},
    "di.py_fallback": {"ko": "[diarize] pyannote 실패 — resemblyzer로 폴백",
                       "en": "[diarize] pyannote failed — falling back to resemblyzer"},
}


def T(key: str, **kw) -> str:
    m = _M.get(key)
    if not m:
        return key
    s = m.get(LANG) or m.get("en") or key
    try:
        return s.format(**kw) if kw else s
    except Exception:
        return s

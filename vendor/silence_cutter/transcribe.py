"""Qwen3-ASR/Whisper를 이용한 음성 인식 및 단어별 타임스탬프"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import numpy as np
from .i18n import T
import soundfile as sf


# ── 비발화(음악·정적) 게이트 상수 (튜닝용) ───────────────────────────────────
# 오탐(진짜 발화 삭제)이 환청 자막보다 나쁘므로 보수적으로. 음악이 새면 ↑, 발화가
# 잘리면 ↓. speechiness = 진폭 엔벨로프의 음절대역(3~8Hz) 변조 비율.
_SPEECH_MIN = 0.18

# ── 스테레오타입 환청구 (ASR이 비발화에서 반복 생성하는 상투구) ──────────────
_HALLUCINATION_PHRASES = {
    "시청해주셔서 감사합니다", "시청해 주셔서 감사합니다", "구독과 좋아요",
    "thank you", "thank you for watching", "thanks for watching",
    "dead we are dead", "we are dead",
}


def _speechiness(audio: np.ndarray, sr: int = 16000) -> float:
    """발화다움 [0~1 근사] — 진폭 엔벨로프의 음절대역(3~8Hz) 변조 에너지 비율.

    말은 음절 리듬으로 3~8Hz 진폭 변조가 강하다. 지속되는 음악·순수 톤·정적·백색잡음은
    이 대역 변조가 약하다. FFT 한 번으로 싸게 계산(numpy만)."""
    if audio is None or len(audio) < sr // 2:      # 0.5초 미만은 판단 보류(통과)
        return 1.0
    x = np.asarray(audio, dtype=np.float64)
    # 프레임 RMS 엔벨로프 (10ms hop = 100Hz 엔벨로프 샘플레이트)
    hop = max(1, sr // 100)
    n = len(x) // hop
    if n < 16:
        return 1.0
    frames = x[:n * hop].reshape(n, hop)
    env = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    env = env - env.mean()
    if np.allclose(env, 0):
        return 0.0
    fenv = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    freqs = np.fft.rfftfreq(len(env), d=hop / sr)
    total = float(np.sum(fenv) + 1e-12)
    syl = float(np.sum(fenv[(freqs >= 3.0) & (freqs <= 8.0)]))
    return syl / total


# 고유명사 힌트를 system_prompt로 넣으면 모델이 그 지시문 자체를 발화로 착각해
# 뱉는다 — 실제로 '다음 고유명사용어를 정확히 인식하세요'가 자막 한 장으로 나왔다.
# 등록한 용어가 자막에 유령처럼 박히는 것도 같은 경로다.
_PROMPT_PREFIX = "다음 고유명사·용어를 정확히 인식하세요: "


def _echoes_prompt(text: str, custom_prompt: str) -> bool:
    """출력이 주입한 지시문·용어 목록을 되뱉은 것인가 (구두점·공백 무시)."""
    def norm(x: str) -> str:
        return "".join(ch for ch in x.lower() if ch.isalnum())
    t = norm(text)
    if not t:
        return False
    # 지시문 자체(부분이라도 8자 이상 겹치면 발화로 볼 수 없다)
    head = norm(_PROMPT_PREFIX)
    if len(t) >= 8 and (t in head or head.startswith(t) or t.startswith(head)):
        return True
    # 용어 목록을 통째로 읊은 경우 — '정예린(제리/재리 아님)' → '정예린제리재리'
    if custom_prompt:
        terms = norm(custom_prompt)
        if len(t) >= 6 and (t in terms or terms.startswith(t)):
            return True
    return False


def _looks_hallucinated(text: str) -> bool:
    """명백한 환청/degeneracy만 True (오탐 거의 0 — 보수적)."""
    t = " ".join(text.lower().split())
    if not t:
        return False
    # 상투구는 발화 전체가 그것일 때만 환청. 부분 문자열로 보면 "and thank you
    # so much for sticking around…" 같은 정상 발화가 통째로 날아간다.
    if t.strip(" .,!?\"'") in _HALLUCINATION_PHRASES:
        return True
    toks = t.replace(",", " ").replace(".", " ").split()
    if len(toks) >= 4:
        # 고유단어 비율로만 판정. 반복 횟수(예: 한 토큰 3회 이상)로 보면 길이가
        # 늘어날수록 무조건 걸린다 — 영어 30초 발화는 'the'만 5번 나온다.
        counts: dict = {}
        for w in toks:
            counts[w] = counts.get(w, 0) + 1
        if len(counts) / len(toks) < 0.4:
            return True
    return False


@contextlib.contextmanager
def _noop_ctx():
    """No-op context manager — used when no progress callback is provided."""
    yield


# XML 1.0에 쓸 수 없는 문자 — 단어에 섞이면 FCPXML이 DTD 검증에서 통째로 버려진다(apptest 2026-10-17)
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


@dataclass
class WordTimestamp:
    """단어 단위 타임스탬프 (원본 오디오 기준 절대 시간)"""
    text: str
    start: float  # 초
    end: float    # 초
    # 사용자가 토큰을 쪼갠 경우, 이 단어가 '뒤 조각과 원래 한 단어'인가.
    # True면 이 뒤에서 자동으로 자막을 끊지 않는다 — 안 막으면 분리기가 길이
    # 균형만 보고 '사'|'과를' 사이를 끊는다(실측 재현됨).
    piece_cont: bool = False

    # 만들 때도, AI 교정·편집이 .text에 나중에 쓸 때도 거른다 — 모든 단어 경로가 여기를 지난다
    def __setattr__(self, name, value):
        if name == "text" and isinstance(value, str):
            value = _XML_INVALID.sub("", value)
        super().__setattr__(name, value)


@dataclass
class TranscribedSegment:
    """하나의 음성 구간에 대한 전사 결과"""
    seg_start: float   # VAD 구간 시작 (원본 기준)
    seg_end: float     # VAD 구간 끝 (원본 기준)
    text: str
    words: List[WordTimestamp] = field(default_factory=list)
    language_code: str | None = None
    language_confidence: float | None = None

    def __post_init__(self):
        # 제어 문자만이던 단어는 거르고 나면 빈 단어 — 빈 자막을 만들지 않게 뺀다
        self.words = [w for w in self.words if w.text]


def _load_segment_audio(audio_path: str | Path, start: float, end: float) -> np.ndarray:
    """오디오 파일에서 특정 구간만 읽기"""
    info = sf.info(str(audio_path))
    sr = info.samplerate
    start_frame = int(start * sr)
    n_frames = int((end - start) * sr)
    data, _ = sf.read(str(audio_path), start=start_frame, frames=n_frames, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data


def _compact_text(text: str) -> str:
    """비교용 텍스트 정규화: 공백/문장부호 제거."""
    return "".join(ch for ch in text if ch.isalnum())


def _restore_question_marks(text: str, words: list) -> None:
    """ASR 텍스트의 '?'를 정렬된 단어에 되붙인다 — 정렬기 단어엔 구두점이 없다.

    "아 진짜요?"처럼 억양으로만 질문인 문장은 어미 규칙이 못 잡으므로 모델이 들은
    물음표가 유일한 신호다. 글자가 똑같이 맞은 단어에만 붙인다(오탐 0 우선)."""
    import difflib
    toks = text.split()
    a = [_compact_text(t) for t in toks]
    b = [_compact_text(w.text) for w in words]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, _j2 in sm.get_opcodes():
        if tag != "equal":
            continue
        for k in range(i2 - i1):
            if toks[i1 + k].endswith("?") and a[i1 + k] and not words[j1 + k].text.endswith("?"):
                words[j1 + k].text += "?"


def _is_faster_whisper_model(model_id: str) -> bool:
    return "faster-whisper" in model_id.lower()


def _is_apple_model(model_id: str) -> bool:
    """Apple SpeechAnalyzer(macOS 26+) 백엔드 — 번들 CLI(typecut-speech)로 전사.
    실측(2026-08-12, tools/sa-probe): 102x 실시간, 단어 타임스탬프, 정답 구간
    CER 3.6%(Qwen 0%) — 속도·배포 압승, 정확도는 Qwen 우세라 선택지로만 둔다."""
    return model_id.lower().startswith("apple")


# SpeechAnalyzer 로케일 — 앱 언어 라벨(ISO 코드)→ BCP-47 (지원 30개 중 대표)
_APPLE_LOCALES = {
    "ko": "ko_KR", "en": "en_US", "ja": "ja_JP", "zh": "zh_CN",
    "de": "de_DE", "fr": "fr_FR", "es": "es_ES", "it": "it_IT",
    "pt": "pt_BR", "yue": "yue_CN",
}

# 어절에 붙어 나오는 문장부호 — 우리 파이프라인은 구두점을 별도 단계(AI 구두점)로
# 다루므로 토큰에서는 벗겨 다른 백엔드와 같은 모양을 유지한다.
_APPLE_PUNCT = ".,?!…~‥"


def _apple_speech_bin():
    """번들 typecut-speech 실행 파일 — 번들 옆 또는 FCPA_SPEECH_BIN."""
    import os
    cand = []
    if os.environ.get("FCPA_SPEECH_BIN"):
        cand.append(Path(os.environ["FCPA_SPEECH_BIN"]))
    cand.append(Path(__file__).resolve().parent.parent / "typecut-speech")
    for p in cand:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def _language_code(language: str) -> str | None:
    """Map app language labels to Whisper ISO-639-1 language codes."""
    normalized = language.strip().lower()
    if not normalized or normalized == "auto":
        return None
    return {
        "korean": "ko",
        "english": "en",
        "japanese": "ja",
        "chinese": "zh",
        "german": "de",
        "french": "fr",
        "spanish": "es",
        "italian": "it",
        "portuguese": "pt",
    }.get(normalized, normalized)


def _language_name(language_code: str | None) -> str | None:
    """Map ISO-639-1 language codes back to app/Qwen language labels."""
    if not language_code:
        return None
    return {
        "ko": "Korean",
        "en": "English",
        "ja": "Japanese",
        "zh": "Chinese",
        "de": "German",
        "fr": "French",
        "es": "Spanish",
        "it": "Italian",
        "pt": "Portuguese",
    }.get(language_code.strip().lower())


def _qwen_result_language(result, fallback_language: str | None) -> str | None:
    """Extract the Qwen3-ASR result language as an app language label."""
    raw_language = getattr(result, "language", None)
    if isinstance(raw_language, list):
        raw_language = raw_language[0] if raw_language else None
    if not isinstance(raw_language, str) or not raw_language.strip():
        return fallback_language

    raw_language = raw_language.strip()
    if raw_language.lower() == "auto":
        return fallback_language

    return _language_name(_language_code(raw_language)) or raw_language


def _cached_hf_snapshot(model_id: str) -> str | None:
    """Return a local HuggingFace snapshot path for model_id if it was downloaded."""
    hf_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    folder = "models--" + model_id.replace("/", "--")
    snapshots_dir = Path(hf_home) / "hub" / folder / "snapshots"
    if not snapshots_dir.is_dir():
        return None

    snapshots = [path for path in snapshots_dir.iterdir() if path.is_dir()]
    if not snapshots:
        return None
    return str(max(snapshots, key=lambda path: path.stat().st_mtime))


class Transcriber:
    """Qwen3-ASR + ForcedAligner 또는 faster-whisper 래퍼"""

    def __init__(
        self,
        asr_model: str = "mlx-community/Qwen3-ASR-0.6B-8bit",
        aligner_model: str = "mlx-community/Qwen3-ForcedAligner-0.6B-8bit",
        language: str = "Korean",
        language_candidates: list[str] | None = None,
        on_progress=None,
        custom_prompt: str = "",
    ):
        self.language = language
        self.custom_prompt = (custom_prompt or "").strip()
        self._language_candidates = [
            code for code in (_language_code(lang) for lang in (language_candidates or []))
            if code
        ]
        self._asr_model_id = asr_model
        self._aligner_model_id = aligner_model
        self._backend = ("apple" if _is_apple_model(asr_model)
                         else "whisper" if _is_faster_whisper_model(asr_model)
                         else "qwen")
        # apple 백엔드 — 파일 전체를 1회 전사(102x라 수 초)하고 구간은 슬라이스
        self._apple_words: dict = {}
        self._asr = None
        self._aligner = None
        self._on_progress = on_progress
        import threading
        self._load_lock = threading.Lock()   # 백그라운드 예열과 본 로드의 경쟁 방지

    def _ensure_loaded(self):
        if self._backend == "apple":
            if _apple_speech_bin() is None:
                raise RuntimeError(
                    "Apple 음성 인식 실행 파일(typecut-speech)이 없습니다 — "
                    "macOS 26 이상에서만 지원되며 앱 번들에 포함되어야 합니다.")
            return
        if self._asr is not None:
            return
        with self._load_lock:               # 한 스레드만 로드, 나머지는 대기
            if self._asr is not None:
                return
            if self._backend == "whisper":
                self._load_whisper()
                return

            from mlx_audio.stt import load

            patch_factory = self._patch_tqdm() if self._on_progress else None

            if self._on_progress:
                self._on_progress("model_download", 0, f"ASR 모델 준비 중: {self._asr_model_id}")
            print(T("tr.asr_load", name=self._asr_model_id), file=sys.stderr)
            with (patch_factory() if patch_factory else _noop_ctx()):
                self._asr = load(self._asr_model_id)

            if self._on_progress:
                self._on_progress("model_download", 50, f"Aligner 모델 준비 중: {self._aligner_model_id}")
            print(T("tr.align_load", name=self._aligner_model_id), file=sys.stderr)
            with (patch_factory() if patch_factory else _noop_ctx()):
                self._aligner = load(self._aligner_model_id)

            if self._on_progress:
                self._on_progress("model_download", 100, "모델 로딩 완료")

    def _load_whisper(self):
        """Load a downloaded faster-whisper model from the app HuggingFace cache."""
        local_snapshot = _cached_hf_snapshot(self._asr_model_id)
        if not local_snapshot:
            raise RuntimeError(
                "Whisper 모델이 아직 다운로드되지 않았습니다. "
                "모델 관리 화면에서 Whisper Small을 먼저 다운로드해 주세요."
            )

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper가 설치되어 있지 않습니다. Python 런타임을 다시 준비해 주세요."
            ) from exc

        if self._on_progress:
            self._on_progress("model_download", 0, f"Whisper 모델 준비 중: {self._asr_model_id}")
        print(f"[transcribe] Whisper 모델 로딩: {local_snapshot}", file=sys.stderr)
        self._asr = WhisperModel(
            local_snapshot,
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )
        if self._on_progress:
            self._on_progress("model_download", 100, "Whisper 모델 로딩 완료")

    def _patch_tqdm(self):
        """huggingface_hub.snapshot_download에 커스텀 tqdm_class를 monkey-patch.

        tqdm을 상속하되, 자체적으로 downloaded 바이트를 추적하여
        on_progress 콜백으로 바이트 단위 진행률을 전달한다.
        """
        import contextlib
        import threading
        cb = self._on_progress

        from tqdm.auto import tqdm as base_tqdm

        class _ProgressTqdm(base_tqdm):
            """tqdm subclass that forwards download progress via callback."""
            _lock = threading.Lock()
            _downloaded = 0
            _total_size = 0

            def __init__(self, *args, **kwargs):
                # Don't disable — huggingface_hub accesses self.total directly
                super().__init__(*args, **kwargs)

            def update(self, n=1):
                super().update(n)
                if n and n > 0:
                    with _ProgressTqdm._lock:
                        _ProgressTqdm._downloaded += n
                    self._report_progress()

            def refresh(self, *args, **kwargs):
                # huggingface_hub calls bytes_progress.total += file_size then refresh()
                if self.total and self.total > _ProgressTqdm._total_size:
                    _ProgressTqdm._total_size = self.total
                self._report_progress()

            def _report_progress(self):
                downloaded = _ProgressTqdm._downloaded
                total = _ProgressTqdm._total_size
                if total > 1024 * 1024:  # 1MB 이상일 때만 (메타데이터 스킵)
                    pct = min(int(downloaded * 100 / total), 100)
                    mb_done = downloaded / (1024 * 1024)
                    mb_total = total / (1024 * 1024)
                    cb("model_download", pct, f"Downloading… {mb_done:.0f} / {mb_total:.0f} MB")

            def display(self, *args, **kwargs):
                # Suppress tqdm visual output
                pass

            def close(self):
                pass

        @contextlib.contextmanager
        def _patch():
            import huggingface_hub._snapshot_download as _sd
            original = _sd.hf_tqdm
            _ProgressTqdm._downloaded = 0
            _ProgressTqdm._total_size = 0
            _sd.hf_tqdm = _ProgressTqdm
            try:
                yield
            finally:
                _sd.hf_tqdm = original

        return _patch

    def _apple_transcribe_file(self, audio_path, language) -> list:
        """파일 전체를 typecut-speech로 1회 전사 → WordTimestamp 목록 (파일 좌표)."""
        import json as _json
        import subprocess
        code = _language_code(language) or "ko"
        if code not in _APPLE_LOCALES:
            # 예전엔 f"{code}_{code.upper()}"로 없는 로케일을 만들어 넘겼다 —
            # SpeechTranscriber가 무엇을 할지 계약이 없다(코덱스 리뷰 2026-08-13).
            raise RuntimeError(
                f"Apple 음성 인식이 지원하지 않는 언어입니다({code}) — "
                f"지원: {', '.join(sorted(_APPLE_LOCALES))}. 다른 모델을 선택하세요.")
        # 키에 로케일과 파일 신원(크기·mtime)을 넣는다 — 경로만 쓰면 언어를 바꿔
        # 다시 부를 때 옛 로케일 결과가 재사용된다(코덱스 리뷰 2026-08-13).
        _st = Path(audio_path).stat()
        key = (str(audio_path), code, _st.st_size, _st.st_mtime_ns)
        if key in self._apple_words:
            return self._apple_words[key]
        locale = _APPLE_LOCALES[code]
        binp = _apple_speech_bin()
        print(T("tr.apple_start", loc=locale), file=sys.stderr)
        # 고정 30분 타임아웃은 장편에서 정당한 작업을 죽인다 — 길이 비례(관측
        # 100배속의 20배 여유) + 최소 5분. 3시간 파일도 안전하게 덮는다.
        import soundfile as _sf
        try:
            _dur = _sf.info(str(audio_path)).duration
        except Exception:
            _dur = 0.0
        _timeout = max(300.0, _dur / 5.0)
        try:
            proc = subprocess.run([binp, str(audio_path), locale],
                                  capture_output=True, timeout=_timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Apple 음성 인식이 제한 시간({_timeout:.0f}s)을 넘겼습니다 "
                f"(오디오 {_dur:.0f}s) — 다른 모델을 선택하거나 구간을 나눠 주세요.")
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            if "UNSUPPORTED_LOCALE" in err:
                raise RuntimeError(
                    f"Apple 음성 인식이 이 언어({locale})를 지원하지 않습니다 — "
                    "다른 모델을 선택하세요.")
            if "ASSET_INSTALL_FAILED" in err:
                raise RuntimeError(
                    "Apple 음성 인식의 언어 에셋을 내려받지 못했습니다 — "
                    "네트워크를 확인하거나 다른 모델을 선택하세요.")
            raise RuntimeError("Apple 음성 인식 실패: " + err[-300:])
        doc = _json.loads(proc.stdout.decode("utf-8"))
        words = []
        for w in doc.get("words", []):
            txt = str(w["text"]).strip(_APPLE_PUNCT).strip()
            if txt:
                words.append(WordTimestamp(text=txt, start=float(w["start"]),
                                           end=float(w["end"])))
        words.sort(key=lambda w: w.start)
        # 파일당 단어 배열이 크다(17분에 2천 개) — 무한정 쌓지 않고 최근 2개만.
        # 파이프라인은 파일 단위로 순차 처리라 2개면 재전사가 생기지 않는다.
        if len(self._apple_words) >= 2:
            self._apple_words.pop(next(iter(self._apple_words)))
        self._apple_words[key] = words
        print(T("tr.apple_done", n=len(words), s=doc.get("elapsed", 0)), file=sys.stderr)
        return words

    def _transcribe_segment_apple(self, audio_path, seg_start, seg_end,
                                  language) -> TranscribedSegment:
        allw = self._apple_transcribe_file(audio_path, language)
        # 구간 슬라이스 — 단어 중심이 구간 안이면 포함 (경계 걸침 관대)
        ws = [WordTimestamp(text=w.text, start=w.start, end=w.end)
              for w in allw if seg_start <= (w.start + w.end) / 2 < seg_end]
        return TranscribedSegment(
            seg_start=seg_start, seg_end=seg_end,
            text=" ".join(w.text for w in ws), words=ws,
            language_code=_language_code(language), language_confidence=None)

    def transcribe_segment(
        self,
        audio_path: str | Path,
        seg_start: float,
        seg_end: float,
        language: str | None = None,
    ) -> TranscribedSegment:
        """하나의 음성 구간을 전사하고 단어별 타임스탬프 생성"""
        self._ensure_loaded()
        effective_language = language or self.language

        # 구간 오디오 추출
        segment_audio = _load_segment_audio(audio_path, seg_start, seg_end)

        # 비발화 게이트 — 음악·정적은 ASR 전에 걸러 환청 자막을 막는다(연산도 절약)
        _sp = _speechiness(segment_audio)
        if _sp < _SPEECH_MIN:
            print(T("tr.gate", t=seg_start, v=_sp), file=sys.stderr)
            return TranscribedSegment(seg_start=seg_start, seg_end=seg_end,
                                      text="", words=[])

        if self._backend == "apple":
            return self._transcribe_segment_apple(
                audio_path, seg_start, seg_end, effective_language)

        if self._backend == "whisper":
            return self._transcribe_segment_whisper(
                segment_audio,
                seg_start,
                seg_end,
                effective_language,
            )

        # ASR: 텍스트 생성
        qwen_language_arg = (
            None
            if _language_code(effective_language) is None
            else effective_language
        )
        _gen_kwargs = {}
        if self.custom_prompt:
            # 고유명사·전문용어를 system_prompt로 주입해 인식 정확도 향상
            _gen_kwargs["system_prompt"] = (
                _PROMPT_PREFIX + self.custom_prompt
            )
        asr_result = self._asr.generate(segment_audio, language=qwen_language_arg, **_gen_kwargs)
        text = asr_result.text.strip()
        # 환청/degeneracy 안전망 — 비발화에서 나오는 상투구·반복은 드롭(자막 없음).
        # 주입한 지시문·용어 목록을 되뱉는 것도 같은 자리에서 막는다.
        if text and _echoes_prompt(text, self.custom_prompt or ""):
            print(T("tr.halluc", t=seg_start, txt=text[:30]), file=sys.stderr)
            return TranscribedSegment(seg_start=seg_start, seg_end=seg_end,
                                      text="", words=[])
        if text and _looks_hallucinated(text):
            print(T("tr.halluc", t=seg_start, txt=text[:30]), file=sys.stderr)
            return TranscribedSegment(seg_start=seg_start, seg_end=seg_end,
                                      text="", words=[])
        detected_language = _qwen_result_language(asr_result, qwen_language_arg)
        detected_language_code = _language_code(detected_language or effective_language)
        language_confidence = 1.0 if qwen_language_arg is not None else None

        if not text:
            return TranscribedSegment(
                seg_start=seg_start,
                seg_end=seg_end,
                text="",
                words=[],
                language_code=detected_language_code,
                language_confidence=language_confidence,
            )

        # ForcedAligner: 단어별 타임스탬프
        aligner_language = detected_language or qwen_language_arg or "English"
        try:
            align_result = self._aligner.generate(
                audio=segment_audio,
                text=text,
                language=aligner_language,
            )
        except Exception as exc:
            print(
                "[transcribe] aligner fallback "
                f"({seg_start:.1f}s ~ {seg_end:.1f}s): {type(exc).__name__}: {exc}"
            )
            return TranscribedSegment(
                seg_start=seg_start,
                seg_end=seg_end,
                text=text,
                words=[],
                language_code=detected_language_code,
                language_confidence=language_confidence,
            )

        words = []
        for item in align_result:
            words.append(WordTimestamp(
                text=item.text,
                start=seg_start + item.start_time,  # 절대 시간으로 변환
                end=seg_start + item.end_time,
            ))
        _restore_question_marks(text, words)

        text_compact = _compact_text(text)
        words_compact = _compact_text("".join(word.text for word in words))
        coverage = (len(words_compact) / len(text_compact)) if text_compact else 1.0
        if not words or (len(text_compact) >= 6 and coverage < 0.75):
            print(
                "[transcribe] aligner text coverage fallback "
                f"({seg_start:.1f}s ~ {seg_end:.1f}s, coverage={coverage:.2f})"
            )
            return TranscribedSegment(
                seg_start=seg_start,
                seg_end=seg_end,
                text=text,
                words=[],
                language_code=detected_language_code,
                language_confidence=language_confidence,
            )

        return TranscribedSegment(
            seg_start=seg_start,
            seg_end=seg_end,
            text=text,
            words=words,
            language_code=detected_language_code,
            language_confidence=language_confidence,
        )

    def detect_language_segment(
        self,
        audio_path: str | Path,
        seg_start: float,
        seg_end: float,
    ) -> tuple[str | None, float | None]:
        """Detect a segment language with faster-whisper without using it as final ASR."""
        self._ensure_loaded()
        if self._backend != "whisper":
            raise RuntimeError("Language detection requires a faster-whisper model.")

        segment_audio = _load_segment_audio(audio_path, seg_start, seg_end)
        language = _language_code(self.language)
        if language is not None:
            return language, 1.0

        _segments_iter, info = self._asr.transcribe(
            segment_audio,
            language=None,
            multilingual=True,
            word_timestamps=False,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        detected_language = getattr(info, "language", None)
        language_confidence = getattr(info, "language_probability", None)

        if self._language_candidates:
            detected_language, language_confidence = self._candidate_language(
                detected_language,
                language_confidence,
                getattr(info, "all_language_probs", None),
            )

        return detected_language, language_confidence

    def _transcribe_segment_whisper(
        self,
        segment_audio: np.ndarray,
        seg_start: float,
        seg_end: float,
        language_label: str,
    ) -> TranscribedSegment:
        """Transcribe one segment with faster-whisper word timestamps."""
        language = _language_code(language_label)
        segments_iter, _info = self._asr.transcribe(
            segment_audio,
            language=language,
            multilingual=language is None,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        detected_language = getattr(_info, "language", None)
        language_confidence = getattr(_info, "language_probability", None)

        if language is None and self._language_candidates:
            detected_language, language_confidence = self._candidate_language(
                detected_language,
                language_confidence,
                getattr(_info, "all_language_probs", None),
            )

        text_parts: list[str] = []
        words: list[WordTimestamp] = []
        for segment in segments_iter:
            segment_text = (segment.text or "").strip()
            if segment_text:
                text_parts.append(segment_text)

            segment_words = getattr(segment, "words", None) or []
            for item in segment_words:
                token = (getattr(item, "word", "") or "").strip()
                start = getattr(item, "start", None)
                end = getattr(item, "end", None)
                if not token or start is None or end is None or end <= start:
                    continue
                words.append(WordTimestamp(
                    text=token,
                    start=seg_start + float(start),
                    end=seg_start + float(end),
                ))

            if not segment_words and segment_text:
                start = getattr(segment, "start", 0.0) or 0.0
                end = getattr(segment, "end", seg_end - seg_start) or (seg_end - seg_start)
                if end > start:
                    words.append(WordTimestamp(
                        text=segment_text,
                        start=seg_start + float(start),
                        end=seg_start + float(end),
                    ))

        text = " ".join(text_parts).strip()
        if not text:
            return TranscribedSegment(
                seg_start=seg_start,
                seg_end=seg_end,
                text="",
                words=[],
                language_code=detected_language,
                language_confidence=language_confidence,
            )

        return TranscribedSegment(
            seg_start=seg_start,
            seg_end=seg_end,
            text=text,
            words=words,
            language_code=detected_language,
            language_confidence=language_confidence,
        )

    def _candidate_language(
        self,
        detected_language: str | None,
        language_confidence: float | None,
        all_language_probs,
    ) -> tuple[str | None, float | None]:
        """Prefer one of the configured multilingual candidate languages when possible."""
        if detected_language in self._language_candidates:
            return detected_language, language_confidence

        if all_language_probs:
            ranked = [
                (code, prob)
                for code, prob in all_language_probs
                if code in self._language_candidates
            ]
            if ranked:
                return max(ranked, key=lambda item: item[1])

        return detected_language, language_confidence

    def transcribe_all(
        self,
        audio_path: str | Path,
        segments: list,
    ) -> List[TranscribedSegment]:
        """모든 음성 구간을 순차 전사"""
        results = []
        total = len(segments)
        for i, seg in enumerate(segments, 1):
            print(f"[transcribe] ({i}/{total}) {seg.start:.1f}s ~ {seg.end:.1f}s")
            result = self.transcribe_segment(audio_path, seg.start, seg.end)
            if result.text:
                results.append(result)
        return results


# ---------------------------------------------------------------------------
# 세그먼트 경계 후처리 — 조사/접미사 분리 복구
# ---------------------------------------------------------------------------

# 세그먼트 시작에 위치할 경우 이전 세그먼트로 옮겨야 하는 한국어 조사/어미 패턴.
# 1~2음절 조사, 어미 접미사.
_JOSA_SET = frozenset([
    # 주격/목적격/보격
    "이", "가", "을", "를", "은", "는", "도", "만",
    # 부사격/관형격 등
    "에", "에서", "에게", "의", "와", "과", "로", "으로",
    "까지", "부터", "마저", "조차", "밖에", "처럼", "같이",
    "보다", "한테", "더러", "라고", "이라고",
    # 접속 어미
    "고", "며", "면", "서",
    # 기타 자주 분리되는 접미사
    "들", "것", "중",
])


def merge_orphan_josa(segments: List[TranscribedSegment]) -> List[TranscribedSegment]:
    """세그먼트 경계에서 분리된 조사를 이전 세그먼트로 병합.

    ForcedAligner가 형태소 단위로 단어를 분리하기 때문에,
    split_long_speech_segments의 시간 기반 분할 경계에서
    "맛집" | "을 검색을..." 처럼 조사가 다음 세그먼트로 넘어가는 현상을 보정.

    동작:
    - 세그먼트의 첫 번째 단어(word)가 조사이면 이전 세그먼트의 마지막에 붙임
    - 여러 조사가 연속(e.g. "을", "은")이면 모두 이동
    - 단어가 없는(words==[]) 세그먼트는 텍스트 기준으로 판단
    """
    if len(segments) <= 1:
        return segments

    result = [segments[0]]
    for seg in segments[1:]:
        if not seg.words:
            # words가 없으면 텍스트의 첫 토큰으로 판단
            first_token = seg.text.strip().split()[0] if seg.text.strip() else ""
            if first_token in _JOSA_SET and result:
                prev = result[-1]
                result[-1] = TranscribedSegment(
                    seg_start=prev.seg_start,
                    seg_end=seg.seg_end,
                    text=(prev.text + " " + seg.text).strip(),
                    words=prev.words,
                    language_code=prev.language_code,
                    language_confidence=prev.language_confidence,
                )
                continue
            result.append(seg)
            continue

        # words가 있는 경우: 앞쪽 조사 단어들을 이전 세그먼트로 이동
        move_count = 0
        for w in seg.words:
            if w.text in _JOSA_SET:
                move_count += 1
            else:
                break

        if move_count > 0 and result and move_count < len(seg.words):
            moving_words = seg.words[:move_count]
            remaining_words = seg.words[move_count:]

            prev = result[-1]
            merged_words = prev.words + moving_words
            merged_text = " ".join(w.text for w in merged_words)
            result[-1] = TranscribedSegment(
                seg_start=prev.seg_start,
                seg_end=moving_words[-1].end,
                text=merged_text,
                words=merged_words,
                language_code=prev.language_code,
                language_confidence=prev.language_confidence,
            )

            remaining_text = " ".join(w.text for w in remaining_words)
            result.append(TranscribedSegment(
                seg_start=remaining_words[0].start,
                seg_end=seg.seg_end,
                text=remaining_text,
                words=remaining_words,
                language_code=seg.language_code,
                language_confidence=seg.language_confidence,
            ))
        else:
            result.append(seg)

    return result

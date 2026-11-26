"""Silero VAD를 이용한 음성/무음 구간 감지.

torch + silero-vad 패키지가 있으면 그대로 사용하고(개발 venv),
없으면 내장 ONNX 모델(onnxruntime)로 동일한 알고리즘을 수행한다
(앱 번들 런타임 — torch ~400MB를 제외해 용량 절감).
"""

from __future__ import annotations

import atexit
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .i18n import T

import numpy as np
import soundfile as sf

try:
    import torch
    from silero_vad import load_silero_vad, get_speech_timestamps
    _HAVE_TORCH_VAD = True
except ImportError:
    _HAVE_TORCH_VAD = False


@dataclass
class SpeechSegment:
    """음성 구간 (초 단위)"""
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _resolve_ffmpeg() -> str:
    """ffmpeg 실행 경로 해석 — 앱 번들 우선 → 시스템 → PATH."""
    import os, shutil
    env = os.environ.get("FCPAUTOCUT_FFMPEG")
    if env and Path(env).exists():
        return env
    # 앱 번들 런타임 (PATH에 Runtime/bin이 있으면 그 ffmpeg 우선)
    for p in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(p) / "ffmpeg"
        if cand.exists():
            return str(cand)
    return shutil.which("ffmpeg") or "ffmpeg"


# 추출한 WAV는 호출자들이 딕셔너리에 캐시해 실행 내내 재사용한다 — 그래서 함수가
# 끝날 때 지울 수 없다. 대신 프로세스가 끝날 때 한 번에 정리한다.
# (안 하면 실행 1회당 ~52MB가 임시 폴더에 영구히 쌓인다: 실측 2일 76개 2.8GB.
#  호출부가 9곳이라 각자 지우게 하면 캐시가 공유하는 경로를 두 번 지운다.)
_TEMP_WAVS: list[str] = []

# 우리 파일임을 파일명으로 식별할 수 있어야 한다. 강제 종료(SIGKILL·크래시)면 atexit이
# 안 돌아 잔재가 남는데, 접두사가 없으면 다른 앱의 tmp*.wav와 구분이 안 돼 청소도 못 한다.
_WIN_MARGIN = 2.0   # 창 앞뒤 여유(초) — 경계에서 패딩·문맥이 잘리지 않게
_TMP_PREFIX = "typecut-"
_STALE_SEC = 6 * 3600     # 이보다 오래된 잔재는 돌던 작업이 아니다


def _cleanup_temp_wavs():
    for path in _TEMP_WAVS:
        try:
            os.unlink(path)
        except OSError:
            pass


def _sweep_stale_temp_wavs():
    """지난 실행이 강제 종료돼 남긴 잔재 청소 — 우리 접두사 + 6시간 경과분만.

    동시에 다른 작업이 돌고 있어도 그쪽 파일은 방금 만들어졌으므로 건드리지 않는다.
    """
    now = time.time()
    try:
        entries = list(Path(tempfile.gettempdir()).glob(f"{_TMP_PREFIX}*.wav"))
    except OSError:
        return
    for p in entries:
        try:
            if now - p.stat().st_mtime > _STALE_SEC:
                p.unlink()
        except OSError:
            pass


atexit.register(_cleanup_temp_wavs)
_sweep_stale_temp_wavs()


class NoAudioTrack(RuntimeError):
    """오디오 트랙이 아예 없는 영상(드론·스톡 B롤) — 고칠 것이 없는 클립이라 '실패'와 구분한다."""


def extract_audio(video_path: str | Path, sample_rate: int = 16000) -> Path:
    """영상에서 16kHz mono WAV 오디오 추출 (ffmpeg).
    다운믹스 실패 시 pan 필터로 재시도해 견고성 확보 (AutoSubs audio_preprocess 패턴)."""
    # 미디어가 없으면 여기서 분명하게 끊는다 — 안 그러면 ffmpeg가 실패하고
    # "오디오 추출 실패"라는 원인 불명 메시지만 남는다. 파일을 옮기는 건 흔한 일이고,
    # media_path가 None인 클립도 있다(engine.py:1728이 그래서 None을 검사한다).
    if not video_path or not Path(video_path).exists():
        raise RuntimeError(T("err.media_missing", path=str(video_path) if video_path else "(FCPXML에 경로 없음)"))
    tmp = tempfile.NamedTemporaryFile(prefix=_TMP_PREFIX, suffix=".wav", delete=False)
    tmp.close()
    _TEMP_WAVS.append(tmp.name)   # 실패해서 raise해도 정리되도록 만들자마자 등록
    ffmpeg = _resolve_ffmpeg()
    base = [ffmpeg, "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le",
            "-ar", str(sample_rate)]

    # 1차: 표준 모노 다운믹스
    attempts = [
        base + ["-ac", "1", tmp.name],
        # 2차: pan 필터로 첫 채널을 모노로 (일부 코덱/레이아웃에서 -ac 실패 구제)
        base + ["-af", "pan=mono|c0=c0", tmp.name],
    ]
    last_err = ""
    for i, cmd in enumerate(attempts):
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode == 0 and Path(tmp.name).stat().st_size > 44:  # WAV 헤더보다 큼
            return Path(tmp.name)
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        # 애초에 오디오 트랙이 없는 파일(영상만 받은 유튜브 다운로드 등).
        # ffmpeg는 "Invalid argument"로만 끝나 원인을 알 수 없다 — 여기서 분명히 끊는다.
        # 2차 시도(pan 필터)도 실을 스트림이 없으니 똑같이 실패한다.
        if "does not contain any stream" in err:
            raise NoAudioTrack(T("err.no_audio_track", name=Path(video_path).name))
        # 파일은 있는데 못 읽는 경우 — 이 앱에서 가장 흔한 실패다(외장 드라이브·
        # 다운로드·데스크탑의 파일은 macOS가 접근을 막는다). ffmpeg 영문 원문
        # ("Error opening input files: Permission denied")을 그대로 보여주면
        # 사용자가 무엇을 해야 하는지 알 수 없다. 재시도해도 결과는 같으므로 바로 끊는다.
        if "Permission denied" in err:
            raise RuntimeError(T("err.media_no_permission", name=Path(video_path).name))
        # 0바이트·다운로드 중단·확장자만 바꾼 파일 — ffmpeg는 전부 이 한 줄로 끝난다.
        # 2차 시도(pan 필터)도 열지 못하는 파일이라 결과는 같으므로 바로 끊는다.
        if "Invalid data found when processing input" in err:
            raise RuntimeError(T("err.media_unreadable", name=Path(video_path).name))
        last_err = err.splitlines()[-1] if err.splitlines() else ""
    raise RuntimeError(f"ffmpeg 오디오 추출 실패 ({ffmpeg}): {last_err}")


def _load_audio_16k(audio_path: str | Path) -> np.ndarray:
    """soundfile로 WAV 읽기 (torchaudio/torchcodec 의존성 회피)"""
    data, sr = sf.read(str(audio_path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    # 16kHz 리샘플링 (ffmpeg에서 이미 16kHz로 추출하지만 안전장치)
    if sr != 16000:
        import librosa
        data = librosa.resample(data, orig_sr=sr, target_sr=16000)
    return data


def detect_speech(
    audio_path: str | Path,
    *,
    threshold: float = 0.5,
    min_speech_ms: int = 250,
    min_silence_ms: int = 300,
    speech_pad_ms: int = 100,
    window: tuple[float, float] | None = None,
) -> List[SpeechSegment]:
    """Silero VAD로 음성 구간 감지. 반환: 시간순 SpeechSegment 리스트.

    window=(시작초, 끝초)를 주면 **그 구간만** 검사한다. 멀티캠·컴파운드는 1~2시간짜리
    원본 앵글에서 타임라인이 몇 초만 쓰는 일이 흔한데, 전체를 검사하면 그만큼 그냥
    버린다(실측: 370초 파일 1.51s → 4.6초 구간 0.07s, 21배).
    반환 시각은 **항상 파일 절대 좌표**다 — 여기서 창 시작을 다시 더해 돌려주므로
    호출부의 좌표계가 바뀌지 않는다(어긋나면 자막이 통째로 밀린다).
    """
    data = _load_audio_16k(audio_path)
    offset = 0.0
    if window is not None:
        total = len(data) / 16000.0
        # 창을 딱 맞게 자르면 결과가 전체 검사와 달라진다(실측: 자막 시작이 1~3프레임
        # 밀렸다). 이유가 둘이다 —
        #  ① VAD는 512샘플 격자로 훑는다. 시작이 격자에서 어긋나면 위상이 달라져
        #     경계가 몇 십 ms 움직인다 → 시작을 512샘플 배수로 내린다.
        #  ② 앞뒤 여유(speech_pad_ms)가 창 밖으로 못 나가 잘린다 → _WIN_MARGIN만큼
        #     더 읽어 문맥을 준다. 잘라낸 구간은 어차피 호출부가 다시 창으로 거른다.
        lo = max(0.0, window[0] - _WIN_MARGIN)
        hi = min(total, (window[1] if window[1] else total) + _WIN_MARGIN)
        lo = (int(lo * 16000) // 512) * 512 / 16000.0     # 512샘플 격자로 정렬
        if hi > lo and (hi - lo) < total:
            data = data[int(lo * 16000):int(hi * 16000)]
            offset = lo

    if _HAVE_TORCH_VAD:
        torch.set_num_threads(1)
        model = load_silero_vad()
        raw = get_speech_timestamps(
            torch.from_numpy(data),
            model,
            threshold=threshold,
            sampling_rate=16000,
            min_speech_duration_ms=min_speech_ms,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=speech_pad_ms,
            return_seconds=True,
        )
        return [SpeechSegment(start=s["start"] + offset, end=s["end"] + offset)
                for s in raw]

    segs = _detect_speech_onnx(
        data,
        threshold=threshold,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
        speech_pad_ms=speech_pad_ms,
    )
    if offset:
        for s in segs:
            s.start += offset
            s.end += offset
    return segs


# ── ONNX 경로 (torch 없는 번들 런타임용) ─────────────────────────────────────

_ONNX_SESSION = None


def _onnx_session():
    global _ONNX_SESSION
    if _ONNX_SESSION is None:
        import onnxruntime
        model_path = Path(__file__).parent / "data" / "silero_vad.onnx"
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        _ONNX_SESSION = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"], sess_options=opts
        )
    return _ONNX_SESSION


def _detect_speech_onnx(
    data: np.ndarray,
    *,
    threshold: float,
    min_speech_ms: int,
    min_silence_ms: int,
    speech_pad_ms: int,
) -> List[SpeechSegment]:
    """silero-vad v5 ONNX 모델 + get_speech_timestamps 동일 알고리즘.

    입력 = [context 64 samples | chunk 512 samples], state (2,1,128), sr int64.
    max_speech_duration은 사용하지 않으므로(무한) 해당 분기는 생략.
    """
    session = _onnx_session()
    sr = 16000
    window = 512
    context_size = 64

    n = len(data)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, context_size), dtype=np.float32)
    sr_input = np.array(sr, dtype=np.int64)

    probs: List[float] = []
    for start in range(0, n, window):
        chunk = data[start:start + window]
        if len(chunk) < window:
            chunk = np.pad(chunk, (0, window - len(chunk)))
        x = np.concatenate([context, chunk.reshape(1, -1)], axis=1).astype(np.float32)
        out, state = session.run(None, {"input": x, "state": state, "sr": sr_input})
        context = x[:, -context_size:]
        probs.append(float(out[0][0]))

    # silero get_speech_timestamps 상태 머신 (max_speech=inf 단순화판)
    neg_threshold = max(threshold - 0.15, 0.01)
    min_speech = sr * min_speech_ms / 1000
    min_silence = sr * min_silence_ms / 1000
    pad = int(sr * speech_pad_ms / 1000)

    speeches: List[dict] = []
    current: dict = {}
    triggered = False
    temp_end = 0
    for i, p in enumerate(probs):
        cur = window * i
        if p >= threshold and temp_end:
            temp_end = 0
        if p >= threshold and not triggered:
            triggered = True
            current["start"] = cur
            continue
        if p < neg_threshold and triggered:
            if not temp_end:
                temp_end = cur
            if cur - temp_end < min_silence:
                continue
            current["end"] = temp_end
            if current["end"] - current["start"] > min_speech:
                speeches.append(current)
            current = {}
            triggered = False
            temp_end = 0
    if triggered and current and (n - current["start"]) > min_speech:
        current["end"] = n
        speeches.append(current)

    # 앞뒤 패딩 (이웃 구간과 겹치지 않게 분배)
    for i, sp in enumerate(speeches):
        if i == 0:
            sp["start"] = max(0, sp["start"] - pad)
        else:
            gap = sp["start"] - speeches[i - 1]["end"]
            if gap < 2 * pad:
                half = gap // 2
                speeches[i - 1]["end"] += half
                sp["start"] = max(0, sp["start"] - half)
            else:
                speeches[i - 1]["end"] += pad
                sp["start"] = max(0, sp["start"] - pad)
        if i == len(speeches) - 1:
            sp["end"] = min(n, sp["end"] + pad)

    return [SpeechSegment(start=s["start"] / sr, end=s["end"] / sr) for s in speeches]


def merge_short_speech_segments(
    segments: List[SpeechSegment],
    *,
    max_segment_seconds: float = 28.0,
    max_gap_seconds: float = 1.5,
) -> List[SpeechSegment]:
    """짧은 인접 음성 구간을 하나로 병합해 STT 호출 수를 줄인다.

    ASR(Qwen)은 ~30초 창으로 처리하므로, 짧은 쉼(<max_gap)으로 나뉜 인접 구간을
    max_segment 이내에서 합치면 (a) 세그먼트당 고정 오버헤드(~0.15s)를 줄이고
    (b) 세그먼트 경계에서 생기는 전사 아티팩트(단어 중복 등)를 줄인다.
    단어 타임스탬프는 절대시간이라 병합해도 자막 분리·컷에 영향 없음."""
    if not segments:
        return segments
    out = [SpeechSegment(start=segments[0].start, end=segments[0].end)]
    for s in segments[1:]:
        prev = out[-1]
        if (s.start - prev.end) <= max_gap_seconds and \
           (s.end - prev.start) <= max_segment_seconds:
            prev.end = s.end
        else:
            out.append(SpeechSegment(start=s.start, end=s.end))
    return out


def split_long_speech_segments(
    audio_path: str | Path,
    segments: List[SpeechSegment],
    *,
    max_segment_seconds: float = 15.0,
    min_segment_seconds: float = 3.0,
    search_window_seconds: float = 2.0,
    frame_ms: int = 20,
) -> List[SpeechSegment]:
    """긴 음성 구간을 ASR용으로 더 짧게 분할.

    VAD가 배경음/룸톤 때문에 긴 덩어리로 붙는 경우가 있어, 전사용 경로에서만
    실제 묵음 구간 또는 저에너지 지점을 찾아 세그먼트를 잘게 나눈다.

    개선: 단어 중간이 아닌 실제 발화 사이의 묵음(pause)에서 분할되도록
    묵음 구간 탐색을 우선하고, 탐색 범위를 ±2초로 확장.
    """
    refined: list[SpeechSegment] = []
    for seg in segments:
        refined.extend(
            _split_long_segment(
                audio_path,
                seg,
                max_segment_seconds=max_segment_seconds,
                min_segment_seconds=min_segment_seconds,
                search_window_seconds=search_window_seconds,
                frame_ms=frame_ms,
            )
        )
    return refined


def _split_long_segment(
    audio_path: str | Path,
    segment: SpeechSegment,
    *,
    max_segment_seconds: float,
    min_segment_seconds: float,
    search_window_seconds: float,
    frame_ms: int,
) -> List[SpeechSegment]:
    if segment.duration <= max_segment_seconds or segment.duration < min_segment_seconds * 2:
        return [segment]

    info = sf.info(str(audio_path))
    sr = info.samplerate
    start_frame = int(segment.start * sr)
    n_frames = int(segment.duration * sr)
    data, _ = sf.read(str(audio_path), start=start_frame, frames=n_frames, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if len(data) == 0:
        return [segment]

    frame_size = max(1, int(sr * frame_ms / 1000))
    usable = len(data) - (len(data) % frame_size)
    if usable < frame_size:
        return [segment]

    frames = data[:usable].reshape(-1, frame_size)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    frame_seconds = frame_size / sr

    # 묵음 판별 임계값: 전체 RMS의 하위 15% 수준
    silence_threshold = float(np.percentile(rms, 15))
    # 묵음으로 간주할 최소 연속 프레임 수 (60ms = 3 프레임 @20ms)
    min_silence_frames = max(2, int(0.06 / frame_seconds))

    refined: list[SpeechSegment] = []
    cursor = segment.start
    segment_end = segment.end

    while segment_end - cursor > max_segment_seconds:
        target = cursor + max_segment_seconds
        search_start = max(cursor + min_segment_seconds, target - search_window_seconds)
        search_end = min(segment_end - min_segment_seconds, target + search_window_seconds)
        fallback = min(target, segment_end - min_segment_seconds)

        if search_start >= search_end:
            split_at = fallback
        else:
            idx_start = max(0, int((search_start - segment.start) / frame_seconds))
            idx_end = min(len(rms) - 1, int((search_end - segment.start) / frame_seconds))
            if idx_end <= idx_start:
                split_at = fallback
            else:
                # 1순위: 묵음 구간(연속 저에너지) 중 target에 가장 가까운 지점
                split_at = _find_silence_split(
                    rms, idx_start, idx_end, target,
                    segment.start, frame_seconds,
                    silence_threshold, min_silence_frames,
                )
                # 2순위: 묵음 못 찾으면 최소 에너지 지점
                if split_at is None:
                    local_min = int(np.argmin(rms[idx_start:idx_end + 1])) + idx_start
                    split_at = segment.start + ((local_min + 0.5) * frame_seconds)

        split_at = max(cursor + min_segment_seconds, min(split_at, segment_end - min_segment_seconds))
        if split_at <= cursor:
            split_at = fallback

        refined.append(SpeechSegment(start=cursor, end=split_at))
        cursor = split_at

    refined.append(SpeechSegment(start=cursor, end=segment_end))
    return refined


def _find_silence_split(
    rms: np.ndarray,
    idx_start: int,
    idx_end: int,
    target_time: float,
    seg_start: float,
    frame_seconds: float,
    threshold: float,
    min_frames: int,
) -> float | None:
    """탐색 범위 내에서 묵음 구간의 중앙점을 찾아 반환.

    묵음 구간 = threshold 이하의 RMS가 min_frames 이상 연속.
    여러 묵음 구간이 있으면 target_time에 가장 가까운 것을 선택.
    """
    silence_regions = []
    run_start = None

    for i in range(idx_start, idx_end + 1):
        if rms[i] <= threshold:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and (i - run_start) >= min_frames:
                silence_regions.append((run_start, i - 1))
            run_start = None

    # 마지막 구간 처리
    if run_start is not None and (idx_end + 1 - run_start) >= min_frames:
        silence_regions.append((run_start, idx_end))

    if not silence_regions:
        return None

    # target_time에 가장 가까운 묵음 구간의 중앙점 선택
    best = None
    best_dist = float("inf")
    for start_idx, end_idx in silence_regions:
        mid_idx = (start_idx + end_idx) / 2
        mid_time = seg_start + (mid_idx + 0.5) * frame_seconds
        dist = abs(mid_time - target_time)
        if dist < best_dist:
            best_dist = dist
            best = mid_time

    return best

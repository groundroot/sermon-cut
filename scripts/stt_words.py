# Run with: ~/.cache/video-editor/stt-venv/bin/python scripts/stt_words.py [--lang auto] <clip> [<clip> ...]
# --lang auto lets Qwen3-ASR detect the language per speech segment (English interviews, sermons with an interpreter);
# Korean mode turned English speech into Korean translations.
# Korean word-level transcript via Silence-Cutter's Qwen3-ASR + ForcedAligner (much faster than WhisperX here).
# Writes transcript/<stem>.words.json per clip (times in seconds from the clip's first frame); skips clips already done.
import json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kit_paths import SILENCE_CUTTER  # noqa: E402
sys.path.insert(0, SILENCE_CUTTER)
from silence_cutter.transcribe import Transcriber
from silence_cutter.vad import detect_speech, split_long_speech_segments

args = sys.argv[1:]
lang = "Korean"
if args[:1] == ["--lang"]:
    lang, args = args[1], args[2:]
tr = Transcriber(asr_model="mlx-community/Qwen3-ASR-1.7B-8bit",
                 aligner_model="mlx-community/Qwen3-ForcedAligner-0.6B-8bit", language=lang)
for clip in map(Path, args):
    out = Path("transcript") / f"{clip.stem}.words.json"
    if out.exists():
        print(f"{out} already exists, skipping")
        continue
    wav = Path("transcript") / f"{clip.stem}.wav"
    # First audio track only (GH6 lecture files carry the same mic on a:0 and a:1, a:2/a:3 silent).
    if not wav.exists() and subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
                       str(wav)]).returncode:  # no audio stream (some Pocket 3 clips): skip, don't stop the batch
        print(f"skipped {clip.name}: no audio")
        continue
    speech = detect_speech(wav, threshold=0.5, min_speech_ms=250, min_silence_ms=300, speech_pad_ms=100)
    segs = tr.transcribe_all(wav, split_long_speech_segments(wav, speech))
    out.write_text(json.dumps({"source": str(clip), "segments": [
        {"start": s.seg_start, "end": s.seg_end, "text": s.text, "lang": s.language_code,
         "words": [{"w": w.text, "s": round(w.start, 3), "e": round(w.end, 3)} for w in s.words]} for s in segs]},
        ensure_ascii=False, indent=1))
    print(f"saved {out}: {len(segs)} segments, {sum(len(s.words) for s in segs)} words")

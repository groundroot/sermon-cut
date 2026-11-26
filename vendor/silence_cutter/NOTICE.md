# silence_cutter (vendored subset)

Six modules of Silence-Cutter (Apache License 2.0, see LICENSE), copied unchanged so that
`scripts/stt_words.py` (Qwen3-ASR + ForcedAligner) and `scripts/make_edit.py` (subtitle chunking)
run without a separate checkout. Upstream: https://github.com/leeyc09/Silence-Cutter (the
Qwen3-ASR path comes from a later fork of it). Copied 2026-11-10 from fork commit 987d97e.

To use another checkout instead, set `SILENCE_CUTTER=<folder that contains silence_cutter/>`
(environment variable or `kit_paths.local.json`).

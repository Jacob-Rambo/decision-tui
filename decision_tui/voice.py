"""Local voice input for text fields: record mic audio, transcribe on-box.

Capture uses pw-record (PipeWire) with an arecord (ALSA) fallback — both ship on
the target machine. Transcription is fully local on CPU via faster-whisper
(CTranslate2); the model auto-downloads once to a cache on the storage drive. No
audio ever leaves the machine.

The module is framework-agnostic: it knows how to record and transcribe, and
nothing about Textual. The app drives the toggle and threads the transcription.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

# small.en is the sweet spot for short dictation on CPU: quick, English-only
# (these are text fields typed by an English speaker), and clearly better than
# base.en. Override with DECISION_TUI_WHISPER_MODEL (e.g. "medium.en").
_MODEL_NAME = os.environ.get("DECISION_TUI_WHISPER_MODEL", "small.en")

# Keep the ~480 MB model off the OS drive when the storage drive is present,
# matching the "caches live on /mnt/general-storage" convention. Honour an
# explicit override; otherwise fall back to faster-whisper's default cache.
_STORAGE_CACHE = Path("/mnt/general-storage/caches/whisper")


def _download_root() -> str | None:
    override = os.environ.get("DECISION_TUI_WHISPER_CACHE")
    if override:
        return override
    # /mnt/general-storage is the mount root; its presence is the signal to keep
    # the model off the smaller OS drive. Create the cache subtree as needed.
    if Path("/mnt/general-storage").is_dir():
        _STORAGE_CACHE.mkdir(parents=True, exist_ok=True)
        return str(_STORAGE_CACHE)
    return None


# 16 kHz mono s16 WAV is exactly what whisper wants — no resample needed.
_RECORDERS = (
    ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"],
    ["arecord", "-q", "-r", "16000", "-c", "1", "-f", "S16_LE", "-t", "wav"],
)


def _recorder_command(out_path: str) -> list | None:
    for rec in _RECORDERS:
        if shutil.which(rec[0]):
            return rec + [out_path]
    return None


def voice_available() -> tuple[bool, str]:
    """Return (usable, reason). Usable means we can both capture and transcribe."""
    if _recorder_command("/dev/null") is None:
        return False, "no recorder (pw-record/arecord) found"
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False, "faster-whisper not installed"
    return True, ""


class Recording:
    """A single in-flight mic capture.

    Construction starts the recorder; stop() ends it and returns the finalized
    WAV path, or None if nothing usable was captured.
    """

    def __init__(self) -> None:
        fd, self._path = tempfile.mkstemp(prefix="decision-tui-voice-", suffix=".wav")
        os.close(fd)
        cmd = _recorder_command(self._path)
        if cmd is None:
            Path(self._path).unlink(missing_ok=True)
            raise RuntimeError("no recorder (pw-record/arecord) found")
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

    def stop(self) -> str | None:
        # SIGINT (not SIGTERM): both pw-record and arecord treat it as a clean
        # stop and flush a valid WAV header. SIGTERM can leave a truncated file.
        self._proc.send_signal(signal.SIGINT)
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        path = Path(self._path)
        # a sub-kilobyte file is just a WAV header with no real audio
        if path.exists() and path.stat().st_size > 1024:
            return self._path
        path.unlink(missing_ok=True)
        return None


_model = None


def _get_model():
    """Lazily build the faster-whisper model (a process-wide singleton)."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            _MODEL_NAME,
            device="cpu",
            compute_type="int8",
            download_root=_download_root(),
        )
    return _model


def preload() -> None:
    """Build the model ahead of first use so dictation has no cold-start stall."""
    _get_model()


def transcribe(wav_path: str) -> str:
    """Transcribe a WAV file to text, then delete the file. Returns "" if silent."""
    try:
        model = _get_model()
        segments, _info = model.transcribe(
            wav_path,
            language="en",
            beam_size=1,  # greedy: fastest, plenty for short dictation
            vad_filter=True,  # trim leading/trailing silence
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        Path(wav_path).unlink(missing_ok=True)

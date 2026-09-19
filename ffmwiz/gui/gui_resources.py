"""Qt-free ownership and PCM validation shared by editor consumers."""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import weakref


class OwnedTemporaryDirectory:
    """Automatic cleanup until a live writer explicitly requires preservation."""

    def __init__(self, *, prefix: str) -> None:
        self.name = tempfile.mkdtemp(prefix=prefix)
        self._finalize = weakref.finalize(self, shutil.rmtree, self.name, ignore_errors=True)

    def preserve(self) -> None:
        """Do not let GC/atexit contradict a failed explicit shutdown."""
        self._finalize.detach()

    def cleanup(self) -> None:
        try:
            shutil.rmtree(self.name)
        except FileNotFoundError:
            pass
        self._finalize.detach()


def selected_audio_stream(request: dict, segment: dict | None = None) -> str:
    """A segment override wins; synthetic silence always has audio stream zero."""
    if segment is not None and not segment.get("has_audio", True):
        return "a:0"
    value = (segment or {}).get("audio_stream") or request.get("audio_stream") or "a:0"
    return str(value).strip().lstrip(":") or "a:0"


def read_completed_pcm(path: Path, *, returncode: int, normal_exit: bool = True) -> bytes:
    """Never promote a failed decode or a truncated int16 sample to a cache."""
    if not normal_exit or returncode != 0:
        raise RuntimeError(f"waveform decoder did not complete successfully (rc={returncode})")
    data = Path(path).read_bytes()
    if not data or len(data) % 2:
        raise ValueError("waveform PCM is empty or contains a truncated int16 sample")
    return data

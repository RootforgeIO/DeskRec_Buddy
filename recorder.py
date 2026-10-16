"""Low-overhead capture into one interruption-tolerant MP4 with mixed audio."""

from __future__ import annotations

import ctypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OMP_WAIT_POLICY"] = "PASSIVE"

import dxcam
import imageio_ffmpeg
import numpy as np
import soundcard as sc
from windows_support import AudioPipe, process_alive

SAMPLE_RATE = 48_000
AUDIO_BLOCK = 960
QUALITY = {
    "Экономно": {"fps": 24, "video_kbps": 1600, "audio_kbps": 160, "preset": "veryfast"},
    "Баланс": {"fps": 30, "video_kbps": 2600, "audio_kbps": 192, "preset": "veryfast"},
    "Высокое": {"fps": 30, "video_kbps": 4500, "audio_kbps": 192, "preset": "faster"},
}


def ffmpeg_path():
    return imageio_ffmpeg.get_ffmpeg_exe()


def devices():
    return sc.all_speakers(), sc.all_microphones()


def estimate_bytes(seconds, quality):
    settings = QUALITY[quality]
    return int(seconds * (settings["video_kbps"] + settings["audio_kbps"]) * 1000 / 8)


def format_size(size):
    return f"{size / 1024**3:.2f} ГБ" if size >= 1024**3 else f"{size / 1024**2:.0f} МБ"


def finalize_mp4(source: Path, scratch: Path, destination: Path):
    """Keep the playable recording intact until a regular MP4 is complete."""
    source, scratch, destination = Path(source).resolve(), Path(scratch).resolve(), Path(destination).resolve()
    result = subprocess.run([
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+discardcorrupt", "-err_detect", "ignore_err", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", str(scratch),
    ], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=300)
    if result.returncode or not scratch.exists() or scratch.stat().st_size < 1024:
        raise RuntimeError(f"Не удалось завершить MP4. Запись сохранена: {source}\n{result.stderr[-1200:]}")
    if destination != source and destination.exists():
        raise FileExistsError(destination)
    try:
        os.replace(scratch, destination)
    except OSError as exc:
        if destination != source or getattr(exc, "winerror", None) not in (5, 32, 33):
            raise
        # Players and Explorer can open the live MP4 without FILE_SHARE_DELETE.
        # Publish the completed copy under a new name instead of reporting that
        # an already encoded recording could not be saved.
        for index in range(1, 1000):
            suffix = "_saved" if index == 1 else f"_saved-{index}"
            alternate = destination.with_name(destination.stem + suffix + destination.suffix)
            try:
                os.rename(scratch, alternate)  # Windows rename never overwrites.
                destination = alternate
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError(f"Не удалось выбрать имя MP4. Готовая запись: {scratch}") from exc
    if destination != source:
        try:
            source.unlink()
        except OSError:
            pass  # A player may still be reading the original fragmented copy.
    return destination


def retire_session(folder: Path, saved: Path):
    """Journal cleanup must never turn a published recording into a save error."""
    try:
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            state = {}
        state.update(status="saved", saved_output=saved.name)
        temporary = folder / "state.tmp"
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, folder / "state.json")
    except (OSError, ValueError):
        pass
    try:
        shutil.rmtree(folder)
    except OSError:
        pass


def recover_pending_recordings(destination: Path):
    recovered, errors = [], []
    if not destination.is_dir():
        return recovered, errors
    root = destination.resolve()
    for folder in root.glob(".deskrec-*"):
        if not folder.is_dir() or folder.resolve().parent != root or not (folder / "state.json").exists():
            continue
        try:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            if state.get("version") != 2:
                continue
            if state.get("status") == "saved":
                try:
                    shutil.rmtree(folder)
                except OSError:
                    pass
                continue
            if process_alive(state.get("pid")) or process_alive(state.get("encoder_pid")):
                continue
            name = state["output"]
            if Path(name).name != name or not name.endswith(".mp4"):
                raise ValueError("Неверное имя файла в журнале записи")
            source = root / name
            if not source.exists() or source.stat().st_size < 1024:
                continue
            saved = finalize_mp4(source, folder / "recovered.mp4", source)
            recovered.append(saved)
            retire_session(folder, saved)
        except Exception as exc:
            errors.append(str(exc))
    return recovered, errors


@dataclass(frozen=True)
class CaptureOptions:
    region: tuple[int, int, int, int]
    quality: str
    destination: Path
    speaker_id: str
    microphone_id: str
    microphone_gain: float = 2.0


def audio_mix_filter(microphone_gain):
    if not math.isfinite(microphone_gain) or not 1 <= microphone_gain <= 4:
        raise ValueError("Усиление микрофона должно быть от 100% до 400%.")
    # Copy mono equally to L/R: automatic rematrixing attenuates it by 3 dB.
    # amix's default normalization would halve both sources even during silence.
    return (f"[1:a]pan=stereo|c0=c0|c1=c0,volume={microphone_gain:.6f}[mic];"
            "[0:a][mic]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
            "alimiter=limit=0.90:level=0:latency=1[a]")


class CaptureSession:
    def __init__(self, options: CaptureOptions):
        self.options = options
        self.settings = QUALITY[options.quality]
        self.folder = Path(tempfile.mkdtemp(prefix=".deskrec-", dir=options.destination))
        self.output_path = options.destination / f"DeskRec_{datetime.now():%Y-%m-%d_%H-%M-%S-%f}.mp4"
        self.stop_event = threading.Event()
        self.start_event = threading.Event()
        self.first_frame_event = threading.Event()
        self.ready_events = [threading.Event() for _ in range(3)]
        self.threads = []
        self.pipes = []
        self.encoder = None
        self.errors = []
        self._error_lock = threading.Lock()
        self.started_at = 0.0
        self.final_path = None
        self._write_state()

    def _write_state(self):
        temporary = self.folder / "state.tmp"
        temporary.write_text(json.dumps({"version": 2, "pid": os.getpid(),
                                         "encoder_pid": self.encoder.pid if self.encoder else None,
                                         "output": self.output_path.name}), encoding="utf-8")
        os.replace(temporary, self.folder / "state.json")

    def _error(self, label, exc):
        with self._error_lock:
            self.errors.append(f"{label}: {exc}")
        self.stop_event.set()

    def _ffmpeg(self, args, log_name):
        with (self.folder / log_name).open("wb") as log:
            return subprocess.Popen([
                ffmpeg_path(), "-hide_banner", "-loglevel", "warning", "-y",
                "-filter_threads", "1", "-filter_complex_threads", "1", *args,
            ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW)

    def start(self):
        audio_mix_filter(self.options.microphone_gain)
        left, top, right, bottom = self.options.region
        if right-left < 64 or bottom-top < 64 or (right-left) % 2 or (bottom-top) % 2:
            raise ValueError("Область должна быть не меньше 64 × 64 пикселей с чётными размерами.")
        token = uuid.uuid4().hex
        try:
            for label in ("system", "microphone"):
                self.pipes.append(AudioPipe(r"\\.\pipe\deskrec-" + token + "-" + label, self.stop_event))
        except Exception:
            for pipe in self.pipes:
                pipe.close()
            raise
        jobs = [(self._audio_worker, (self.options.speaker_id, 2, 0)),
                (self._audio_worker, (self.options.microphone_id, 1, 1)),
                (self._video_worker, ())]
        for target, args in jobs:
            thread = threading.Thread(target=target, args=args, daemon=True)
            self.threads.append(thread)
            thread.start()
        for event in self.ready_events:
            if not event.wait(10):
                self._error("Подготовка", "Не удалось подготовить устройство за 10 секунд")
                break
        if self.errors:
            self.start_event.set()
            raise RuntimeError("\n".join(self.errors))
        self.started_at = time.monotonic()
        self.start_event.set()
        if not self.first_frame_event.wait(10):
            self._error("Подготовка", "Не удалось передать первый кадр кодировщику за 10 секунд")
        if self.errors:
            raise RuntimeError("\n".join(self.errors))

    def _audio_worker(self, device_id, channels, index):
        com_result = ctypes.windll.ole32.CoInitializeEx(None, 0)
        pipe = self.pipes[index]
        try:
            if com_result not in (0, 1):
                raise RuntimeError(f"WASAPI COM: {com_result}")
            microphone = sc.get_microphone(device_id, include_loopback=index == 0)
            if bool(microphone.isloopback) != (index == 0):
                raise RuntimeError("Неверное звуковое устройство")
            # SoundCard's Windows mono capture is broken: capture stereo first.
            # A larger shared-mode WASAPI buffer tolerates brief scheduling or
            # encoder stalls; we still read only 20 ms of samples at a time.
            recorder = microphone.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=SAMPLE_RATE//2)
            self.ready_events[index].set()
            self.start_event.wait()
            # Start both audio devices on the same recording epoch as video,
            # avoiding pre-recorded samples while the camera is preparing.
            with recorder:
                if self.stop_event.is_set():
                    return
                pipe.connect()
                written = 0
                while not self.stop_event.is_set():
                    block = recorder.record(numframes=AUDIO_BLOCK)
                    if channels == 1:
                        block = block.mean(axis=1, keepdims=True)
                    pipe.write(np.asarray(block, dtype="<f4").tobytes())
                    written += len(block)
                    self.stop_event.wait(max(0, self.started_at + written/SAMPLE_RATE - time.monotonic()))
        except Exception as exc:
            if not self.stop_event.is_set():
                self._error("Системный звук" if index == 0 else "Микрофон", exc)
        finally:
            self.ready_events[index].set()
            pipe.close()
            if com_result in (0, 1):
                ctypes.windll.ole32.CoUninitialize()

    def _video_worker(self):
        camera = None
        com_result = ctypes.windll.ole32.CoInitializeEx(None, 2)
        try:
            if com_result not in (0, 1):
                raise RuntimeError(f"DXGI COM: {com_result}")
            left, top, right, bottom = self.options.region
            fps = self.settings["fps"]
            camera = dxcam.create(output_color="BGRA", processor_backend="numpy")
            args = []
            for pipe, channels in zip(self.pipes, (2, 1)):
                args += ["-thread_queue_size", "32", "-probesize", "32", "-analyzeduration", "0",
                         "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(channels), "-i", pipe.name]
            args += ["-thread_queue_size", "4", "-probesize", "32", "-analyzeduration", "0",
                     "-f", "rawvideo", "-pix_fmt", "bgra", "-s:v", f"{right-left}x{bottom-top}",
                     "-framerate", str(fps), "-i", "pipe:0",
                     "-filter_complex", audio_mix_filter(self.options.microphone_gain),
                     "-map", "2:v:0", "-map", "[a]", "-c:v", "libx264", "-threads:v", "2",
                     "-preset", self.settings["preset"], "-tune", "zerolatency", "-g", str(fps*2),
                     "-b:v", f"{self.settings['video_kbps']}k", "-maxrate", f"{self.settings['video_kbps']}k",
                     "-bufsize", f"{self.settings['video_kbps']*2}k", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-threads:a", "1", "-b:a", f"{self.settings['audio_kbps']}k",
                     "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                     "-frag_duration", "1000000", "-flush_packets", "1", str(self.output_path)]
            self.encoder = self._ffmpeg(args, "recording.log")
            self._write_state()
            self.ready_events[2].set()
            self.start_event.wait()
            index, last_frame = 0, None
            while not self.stop_event.is_set():
                frame = camera.grab(region=self.options.region, copy=False, new_frame_only=True)
                if frame is not None:
                    last_frame = frame.tobytes()
                if last_frame is not None:
                    self.encoder.stdin.write(last_frame)
                    self.first_frame_event.set()
                    index += 1
                delay = self.started_at + index/fps - time.monotonic()
                self.stop_event.wait(max(0.01 if index == 0 else 0, delay))
        except Exception as exc:
            if not self.stop_event.is_set():
                self._error("Запись", exc)
        finally:
            self.ready_events[2].set()
            if self.encoder:
                try:
                    self.encoder.stdin.close()
                    if self.encoder.wait(timeout=30):
                        self._error("FFmpeg", (self.folder / "recording.log").read_text(errors="replace")[-1500:])
                except Exception as exc:
                    self._error("Завершение", exc)
            if camera:
                camera.release()
            if com_result in (0, 1):
                ctypes.windll.ole32.CoUninitialize()

    def current_bytes(self):
        try:
            return self.output_path.stat().st_size
        except OSError:
            return 0

    def cancel(self):
        """Finish capture workers even when preparation has only partly succeeded."""
        self.stop_event.set()
        self.start_event.set()
        deadline = time.monotonic() + 35
        for thread in self.threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self.threads):
            raise RuntimeError(f"Запись не завершилась вовремя. MP4: {self.output_path}")

    def stop_and_save(self, output=None):
        self.cancel()
        if not self.output_path.exists() or self.output_path.stat().st_size < 1024:
            raise RuntimeError("\n".join(self.errors) + f"\nЖурнал: {self.folder}")
        output = Path(output) if output else self.output_path
        self.final_path = finalize_mp4(self.output_path, self.folder / "final.mp4", output)
        retire_session(self.folder, self.final_path)
        return self.final_path

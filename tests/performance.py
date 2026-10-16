"""Measure recorder CPU, including its FFmpeg processes, on animated 1080p capture."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from recorder import CaptureSession, CaptureOptions
import soundcard as sc


def child_cpu(process):
    values = [wintypes.FILETIME() for _ in range(4)]
    get_times = ctypes.windll.kernel32.GetProcessTimes
    get_times.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    if not get_times(int(process._handle), *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError()
    return sum((value.dwHighDateTime << 32) + value.dwLowDateTime for value in values[2:]) / 10_000_000


class MeasuredSession(CaptureSession):
    def __init__(self, options):
        super().__init__(options)
        self.processes = []

    def _ffmpeg(self, args, log_name):
        process = super()._ffmpeg(args, log_name)
        self.processes.append(process)
        return process


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "measurement"
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    window = tk.Tk()
    window.title("DeskRec performance test")
    window.geometry("1280x720+100+100")
    canvas = tk.Canvas(window, background="#142235", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    square = canvas.create_rectangle(0, 220, 220, 440, fill="#25d7c4", outline="")
    caption = canvas.create_text(640, 100, fill="white", font=("Segoe UI", 24), text="Recording performance test")
    window.update()
    if label == "--animate":
        started = time.monotonic()
        while time.monotonic() - started < 30:
            position = int((time.monotonic() - started) * 200) % 1050
            canvas.coords(square, position, 220, position + 220, 440)
            canvas.itemconfigure(caption, text=f"Portable recording performance test · {time.monotonic()-started:.1f}s")
            window.update()
            time.sleep(1 / 30)
        window.destroy()
        return
    destination = ROOT / ".smoke" / f"perf-{label}-{time.time_ns()}"
    destination.mkdir(parents=True)
    session = MeasuredSession(CaptureOptions(
        (0, 0, 1920, 1080), "Баланс", destination,
        sc.default_speaker().id, sc.default_microphone().id,
    ))
    try:
        session.start()
        started = time.monotonic()
        previous = started
        previous_self = time.process_time()
        previous_children = sum(child_cpu(process) for process in session.processes)
        measurements = []
        while time.monotonic() - started < 10:
            now = time.monotonic()
            position = int((now - started) * 200) % 1050
            canvas.coords(square, position, 220, position + 220, 440)
            canvas.itemconfigure(caption, text=f"Recording performance test · {now-started:.1f}s")
            window.update()
            if now - previous >= 1:
                cpu_self = time.process_time()
                cpu_children = sum(child_cpu(process) for process in session.processes)
                divisor = (now - previous) * os.cpu_count()
                sample = {"second": round(now-started, 2),
                          "app_cpu_percent": round(100 * (cpu_self-previous_self) / divisor, 2),
                          "ffmpeg_cpu_percent": round(100 * (cpu_children-previous_children) / divisor, 2)}
                measurements.append(sample)
                print(json.dumps(sample), flush=True)
                previous, previous_self, previous_children = now, cpu_self, cpu_children
            time.sleep(1 / 30)
        duration = time.monotonic() - started
        output = session.stop_and_save(destination / "recording.mp4")
        steady = measurements[2:]
        report = {"label": label, "logical_processors": os.cpu_count(), "region": [1920, 1080],
                  "fps": 30, "quality": "Баланс", "wall_seconds": duration,
                  "average_app_cpu_percent": round(sum(s["app_cpu_percent"] for s in steady)/len(steady), 2),
                  "average_ffmpeg_cpu_percent": round(sum(s["ffmpeg_cpu_percent"] for s in steady)/len(steady), 2),
                  "samples": measurements, "output": str(output)}
        (destination / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
    finally:
        session.stop_event.set()
        session.start_event.set()
        window.destroy()


if __name__ == "__main__":
    main()

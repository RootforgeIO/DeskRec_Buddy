"""Measure gain, stereo balance and overload protection in the final AAC/MP4."""

from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))
from recorder import CaptureSession, CaptureOptions, SAMPLE_RATE, ffmpeg_path
import numpy as np


class Tone:
    def __init__(self, frequency, loopback, amplitude):
        self.frequency, self.isloopback = frequency, loopback
        self.amplitude = amplitude
        self.position = 0

    def recorder(self, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def record(self, numframes):
        timeline = (np.arange(numframes) + self.position) / SAMPLE_RATE
        self.position += numframes
        return np.column_stack([
            self.amplitude * np.sin(2 * np.pi * frequency * timeline)
            for frequency in self.frequency
        ]).astype("float32")


def measure(destination, gain, system_level=0.2, microphone_level=0.06):
    sources = {"system": Tone((440, 660), True, system_level),
               "mic": Tone((880, 880), False, microphone_level)}
    with patch("recorder.sc.get_microphone", side_effect=lambda device_id, **kwargs: sources[device_id]):
        session = CaptureSession(CaptureOptions((100, 100, 740, 460), "Экономно", destination,
                                                "system", "mic", microphone_gain=gain))
        session.start()
        time.sleep(2.5)
        output = session.stop_and_save()
    assert not session.errors, session.errors
    result = subprocess.run([ffmpeg_path(), "-v", "error", "-i", str(output), "-vn", "-f", "f32le", "-ac", "2", "-ar", str(SAMPLE_RATE), "pipe:1"], capture_output=True)
    assert result.returncode == 0, result.stderr
    audio = np.frombuffer(result.stdout, dtype="<f4").reshape(-1, 2)[SAMPLE_RATE:2*SAMPLE_RATE]
    assert len(audio) == SAMPLE_RATE, "Audio timeline is shorter than the recording"
    amplitude = 2 * np.abs(np.fft.rfft(audio, axis=0)) / len(audio)
    system = np.array([amplitude[440, 0], amplitude[660, 1]])
    microphone = amplitude[880]
    peak = np.max(np.abs(audio))
    print(f"gain={gain}: system={system}, mic={microphone}, peak={peak:.4f}", flush=True)
    return system, microphone, peak, amplitude


def main():
    destination = ROOT / ".smoke" / f"audio-mix-{time.time_ns()}"
    destination.mkdir(parents=True)
    results = []
    for gain in (1, 2, 4):
        system, microphone, peak, spectrum = measure(destination, gain)
        assert np.allclose(system, 0.2, rtol=0.08), "System sound is being attenuated"
        assert np.allclose(microphone, 0.06 * gain, rtol=0.10), "Incorrect microphone gain or mono/stereo attenuation"
        assert spectrum[440, 1] < 0.005 and spectrum[660, 0] < 0.005, "System stereo channels were mixed together"
        assert np.isclose(microphone[0], microphone[1], rtol=0.02), "Voice must have equal level in both ears"
        results.append(microphone.mean())
    assert np.isclose(results[1] / results[0], 2, rtol=0.05), "200% must double the mic without changing the system sound"
    system, microphone, *_ = measure(destination, 2, microphone_level=0)
    assert np.allclose(system, 0.2, rtol=0.08) and np.max(microphone) < 0.003, "Silent microphone changes system sound"
    _, _, peak, _ = measure(destination, 4, system_level=0.8, microphone_level=0.8)
    assert peak < 1, "Loud overlapping sources overload the saved AAC"
    print(f"PASS: mic 100/200/400%, unchanged system stereo, silent mic, limiter; {destination}")


if __name__ == "__main__":
    main()

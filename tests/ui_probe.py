"""Hardware UI regression: drag, release, start, stop and decode MP4."""

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import DeskRecApp, make_dpi_aware
from recorder import ffmpeg_path
from tkinter import ttk
from windows_support import work_area
from save_locked import hold_live_file


def pump_until(app, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "UI operation timed out"
        app.update()
        time.sleep(0.01)
    app.update()


def drag(app, start, motion, release):
    app.pick_region()
    app.update()
    picker = app.picker
    picker.canvas.event_generate("<ButtonPress-1>", x=start[0], y=start[1])
    picker.canvas.event_generate("<B1-Motion>", x=motion[0], y=motion[1])
    picker.canvas.event_generate("<ButtonRelease-1>", x=release[0], y=release[1])
    app.update()
    assert not picker.window.winfo_exists(), "Selection still blocks the main window"
    assert app.picker is None and app.state() == "normal"
    assert app.start_button.instate(["!disabled"])
    assert app.selection_frame is not None


def check_exclusion(windows):
    for window in windows:
        affinity = ctypes.c_uint()
        hwnd = int(window.frame(), 0)
        ok = ctypes.windll.user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
        assert ok and affinity.value == 17, "Frame or control bar can appear in capture"


def main():
    make_dpi_aware()
    settings_path = ROOT / ".smoke" / f"ui-settings-{time.time_ns()}.json"
    settings_override = patch("app.SETTINGS_PATH", settings_path)
    settings_override.start()
    app = DeskRecApp()
    errors = []
    try:
        with patch("app.messagebox.showinfo"), patch("app.messagebox.showerror", side_effect=lambda *args: errors.append(args)):
            app.update()
            pump_until(app, lambda: not app.recovering)
            for widget in app.content_frame.winfo_children():
                assert widget.winfo_y() + widget.winfo_height() <= app.winfo_height(), "A settings control is clipped at Windows' current DPI"
            assert app.speaker_box.get() and app.microphone_box.get()
            assert app.microphone_gain_var.get() == "200%"
            assert not any(isinstance(widget, ttk.Button) and "Ярлык" in widget.cget("text")
                           for widget in app.content_frame.winfo_children())
            app.microphone_gain_box.set("300%")
            app.microphone_gain_box.event_generate("<<ComboboxSelected>>")
            app.update()
            assert json.loads(settings_path.read_text(encoding="utf-8"))["microphone_gain"] == "300%"
            drag(app, (120, 150), (760, 510), (800, 530))
            assert app.region == (120, 150, 800, 530), "Release coordinates were ignored"
            check_exclusion(app.selection_frame.windows)
            drag(app, (1000, 800), (850, 650), (800, 600))
            assert app.region == (800, 600, 1000, 800), "Reverse drag failed"
            drag(app, (1000, 800), (1050, 840), (1080, 860))
            assert app.region == (800, 600, 1080, 860), "Corner resize failed"
            drag(app, (900, 700), (920, 680), (940, 660))
            assert app.region == (840, 560, 1120, 820), "Moving the selected frame failed"
            app.pick_region()
            app.update()
            assert app.picker.hit_test(840, 950) == "new", "Edge hit area extends beyond the frame"
            app.picker.window.event_generate("<Escape>")
            app.update()
            assert app.picker is None and app.region == (840, 560, 1120, 820)
            check_exclusion(app.selection_frame.windows)
            destination = ROOT / ".smoke" / f"ui-{time.time_ns()}"
            app.folder_var.set(str(destination))
            app.quality_var.set("Экономно")
            app.start_button.invoke()
            pump_until(app, lambda: app.overlay is not None or bool(errors))
            assert not errors, errors
            assert app.session.options.microphone_gain == 3.0, "Selected voice gain did not reach the recorder"
            assert app.selection_frame is None
            assert app.state() == "iconic", "Recording must remain accessible from the taskbar"
            assert app.start_button.cget("text") == "Стоп и сохранить"
            assert app.stop_hotkey.available, "Global stop hotkey was not registered"
            check_exclusion(app.overlay.windows)
            assert abs(float(app.overlay.bar.attributes("-alpha")) - 0.9) < 0.01
            left, top, right, bottom = work_area(app)
            expected = app.overlay.clamp_position(right-app.overlay.width-20, (top+bottom-app.overlay.height)//2)
            assert (app.overlay.bar.winfo_x(), app.overlay.bar.winfo_y()) == expected
            for widget, dx, dy in ((app.overlay.bar, -160, -100), (app.overlay.label, 20, 30)):
                old_x, old_y = app.overlay.bar.winfo_x(), app.overlay.bar.winfo_y()
                widget.event_generate("<ButtonPress-1>", x=4, y=4, rootx=old_x+4, rooty=old_y+4)
                widget.event_generate("<B1-Motion>", x=4, y=4, rootx=old_x+4+dx, rooty=old_y+4+dy)
                widget.event_generate("<ButtonRelease-1>", x=4, y=4, rootx=old_x+4+dx, rooty=old_y+4+dy)
                app.update()
                assert (app.overlay.bar.winfo_x(), app.overlay.bar.winfo_y()) == (old_x+dx, old_y+dy)
            saved_position = app.panel_position
            assert json.loads(settings_path.read_text(encoding="utf-8"))["panel_position"] == list(saved_position)
            hwnd = int(app.overlay.bar.frame(), 0)
            assert ctypes.windll.user32.GetWindowLongPtrW(hwnd, -8) == 0, "Toolbar is owned by the minimized setup window"
            assert ctypes.windll.user32.GetWindowLongW(hwnd, -20) & 0x40000, "Panel needs a taskbar entry"
            # An intentional minimize must survive watchdog updates and panel recreation.
            app.overlay.minimize_button.invoke()
            start_hidden = time.monotonic()
            pump_until(app, lambda: time.monotonic() - start_hidden > 1.2)
            assert ctypes.windll.user32.IsIconic(hwnd), "Watchdog restored a deliberately minimized panel"
            assert app.session and app.recording_active
            old_bar = app.overlay.bar
            old_bar.destroy()
            pump_until(app, lambda: app.overlay.bar is not old_bar)
            hwnd = int(app.overlay.bar.frame(), 0)
            assert ctypes.windll.user32.IsIconic(hwnd), "Recreated panel lost the user's minimized state"
            # SW_RESTORE is the same native restoration used by the taskbar.
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            pump_until(app, lambda: not app.panel_minimized)
            assert ctypes.windll.user32.IsWindowVisible(hwnd)
            check_exclusion(app.overlay.windows)
            assert (app.overlay.bar.winfo_x(), app.overlay.bar.winfo_y()) == saved_position
            ctypes.windll.user32.ShowWindow(hwnd, 0)
            pump_until(app, lambda: ctypes.windll.user32.IsWindowVisible(hwnd))
            assert ctypes.windll.user32.GetWindowLongW(hwnd, -20) & 8, "Toolbar must stay topmost"
            old_bar = app.overlay.bar
            old_bar.destroy()
            pump_until(app, lambda: app.overlay.bar is not old_bar)
            assert app.overlay.bar.winfo_exists(), "Destroyed toolbar was not recreated"
            assert (app.overlay.bar.winfo_x(), app.overlay.bar.winfo_y()) == saved_position
            start = time.monotonic()
            pump_until(app, lambda: time.monotonic() - start > 1.5)
            stop_button = app.overlay.stop_button
            sessions = [app.session]
            with hold_live_file(app.session.output_path):
                stop_button.invoke()
                pump_until(app, lambda: not app.saving, timeout=40)
            assert app.status_var.get() == f"Сохранено: {sessions[0].final_path}"
            assert sessions[0].final_path != sessions[0].output_path, "Locked live file must use a published fallback"
            assert not errors, errors
            assert app.selection_frame is not None and app.start_button.instate(["!disabled"])
            # A second recording can also be stopped through the taskbar window.
            app.start_button.invoke()
            pump_until(app, lambda: app.overlay is not None or bool(errors))
            assert not errors, errors
            sessions.append(app.session)
            wait_start = time.monotonic()
            pump_until(app, lambda: time.monotonic() - wait_start > 1)
            app.deiconify()
            app.start_button.invoke()
            pump_until(app, lambda: not app.saving, timeout=40)
            assert not errors, errors
            # The third recording must stop even while the toolbar is hidden.
            app.start_button.invoke()
            pump_until(app, lambda: app.overlay is not None or bool(errors))
            assert not errors, errors
            sessions.append(app.session)
            wait_start = time.monotonic()
            pump_until(app, lambda: time.monotonic() - wait_start > 1)
            app.overlay.minimize_button.invoke()
            # Generate Ctrl+Shift+F9 through Windows, including key-up events.
            keyboard = ctypes.windll.user32.keybd_event
            keyboard.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_size_t]
            for key in (0x11, 0x10, 0x78):
                keyboard(key, 0, 0, 0)
            for key in (0x78, 0x10, 0x11):
                keyboard(key, 0, 2, 0)
            pump_until(app, lambda: app.session is None or bool(errors))
            pump_until(app, lambda: not app.saving, timeout=40)
            assert not errors, errors
            outputs = [session.final_path for session in sessions]
            assert len(outputs) == 3 and len(set(outputs)) == 3, outputs
            for output in outputs:
                result = subprocess.run([ffmpeg_path(), "-v", "error", "-i", str(output), "-f", "null", "NUL"], capture_output=True)
                assert result.returncode == 0, result.stderr
            # Reopening the application restores the selected voice gain.
            reopened = DeskRecApp()
            try:
                reopened.update()
                pump_until(reopened, lambda: not reopened.recovering)
                assert reopened.microphone_gain_var.get() == "300%"
            finally:
                reopened.destroy()
            print(f"PASS: region selection, native panel minimize/restore/recovery, capture exclusion, taskbar stop, global hotkey and three MP4 decodes: {destination}")
    finally:
        if app.session:
            app.session.stop_event.set()
            app.session.start_event.set()
        app.destroy()
        settings_override.stop()


if __name__ == "__main__":
    main()

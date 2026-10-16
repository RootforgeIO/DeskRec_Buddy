"""DeskRec Buddy — compact Windows screen and audio recorder."""

from __future__ import annotations

import ctypes
import json
import logging
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import Tk, Toplevel, Canvas, StringVar, messagebox, filedialog, TclError
from tkinter import ttk

ROOT = Path(__file__).resolve().parent
if (ROOT / "vendor").is_dir():
    sys.path.insert(0, str(ROOT / "vendor"))

from recorder import CaptureOptions, CaptureSession, QUALITY, devices, estimate_bytes, format_size, recover_pending_recordings
from windows_support import StopHotkey, keep_on_top, work_area
from product import APP_NAME, VERSION, PUBLISHER
from runtime_support import settings_path, create_desktop_shortcut, configure_logging, InstanceGuard

SETTINGS_PATH = settings_path()
MICROPHONE_GAINS = ("100%", "150%", "200%", "300%", "400%")


def make_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def exclude_from_capture(window: Toplevel) -> None:
    window.update_idletasks()
    try:
        hwnd = int(window.frame(), 0)
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
    except Exception:
        pass


def click_through(window: Toplevel) -> None:
    try:
        user32 = ctypes.windll.user32
        hwnd = int(window.frame(), 0)
        current = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, current | 0x20 | 0x08000000)
    except Exception:
        pass


class RegionPicker:
    def __init__(self, app: "DeskRecApp") -> None:
        self.app = app
        self.window = Toplevel(app)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.30)
        self.width = app.winfo_screenwidth()
        self.height = app.winfo_screenheight()
        self.window.geometry(f"{self.width}x{self.height}+0+0")
        self.canvas = Canvas(self.window, bg="#08111e", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.rect = list(app.region) if app.region else None
        self.mode = "new"
        self.origin = (0, 0)
        self.old_rect = self.rect[:] if self.rect else None
        self.dragging = False
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.canvas.bind("<Motion>", self.on_hover)
        self.window.bind("<Return>", lambda _: self.accept())
        self.window.bind("<Escape>", lambda _: self.close())
        self.draw()
        self.window.focus_force()

    def draw(self) -> None:
        if not self.canvas.find_withtag("instructions"):
            self.canvas.create_rectangle(24, 20, 650, 88, fill="#102238", outline="", tags="instructions")
            self.canvas.create_text(40, 38, anchor="w", fill="white", font=("Segoe UI", 12, "bold"),
                                    text="Выберите область записи", tags="instructions")
            self.canvas.create_text(40, 66, anchor="w", fill="#c5d1dc", font=("Segoe UI", 10),
                                    text="Потяните мышью и отпустите — область выбрана · Esc — отмена",
                                    tags="instructions")
        self.canvas.delete("selection")
        if self.rect is None:
            return
        left, top, right, bottom = self.rect
        x1, x2 = sorted((left, right))
        y1, y2 = sorted((top, bottom))
        self.canvas.create_rectangle(x1, y1, x2, y2, outline="#25d7c4", width=3, tags="selection")
        for x in (x1, (x1 + x2) // 2, x2):
            for y in (y1, (y1 + y2) // 2, y2):
                if x == (x1 + x2) // 2 and y == (y1 + y2) // 2:
                    continue
                self.canvas.create_rectangle(x - 5, y - 5, x + 5, y + 5,
                                             fill="#ffffff", outline="#25d7c4", tags="selection")
        self.canvas.create_text(x1 + 4, max(100, y1 - 15), anchor="w", fill="white",
                                font=("Segoe UI", 10, "bold"), text=f"{x2-x1} × {y2-y1}", tags="selection")
        self.canvas.tag_raise("instructions")

    def hit_test(self, x: int, y: int) -> str:
        if self.rect is None:
            return "new"
        x1, y1, x2, y2 = self.rect
        if not (x1 - 12 <= x <= x2 + 12 and y1 - 12 <= y <= y2 + 12):
            return "new"
        near_x = "left" if abs(x - x1) <= 12 else "right" if abs(x - x2) <= 12 else ""
        near_y = "top" if abs(y - y1) <= 12 else "bottom" if abs(y - y2) <= 12 else ""
        if near_x or near_y:
            return near_x + ":" + near_y
        return "move" if x1 <= x <= x2 and y1 <= y <= y2 else "new"

    def on_hover(self, event) -> None:
        if not self.dragging:
            mode = self.hit_test(event.x, event.y)
            self.canvas.configure(cursor="crosshair" if mode == "new" else "fleur" if mode == "move" else "sizing")

    def on_down(self, event) -> None:
        x, y = event.x, event.y
        self.origin = (x, y)
        self.old_rect = self.rect[:] if self.rect else None
        self.mode = self.hit_test(x, y)
        self.dragging = True
        if self.mode == "new":
            self.rect = [x, y, x, y]

    def on_move(self, event) -> None:
        if not self.dragging:
            return
        x = min(max(event.x, 0), self.width)
        y = min(max(event.y, 0), self.height)
        if self.mode == "new":
            self.rect[2:] = [x, y]
        elif self.mode == "move":
            dx = x - self.origin[0]
            dy = y - self.origin[1]
            x1, y1, x2, y2 = self.old_rect
            dx = max(-x1, min(dx, self.width - x2))
            dy = max(-y1, min(dy, self.height - y2))
            self.rect = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
        else:
            self.rect = self.old_rect[:]
            axis_x, axis_y = self.mode.split(":")
            if axis_x == "left":
                self.rect[0] = x
            elif axis_x == "right":
                self.rect[2] = x
            if axis_y == "top":
                self.rect[1] = y
            elif axis_y == "bottom":
                self.rect[3] = y
        self.draw()

    def on_up(self, event) -> None:
        if not self.dragging:
            return
        self.on_move(event)
        self.dragging = False
        self.accept()

    def accept(self) -> None:
        if self.rect is None:
            return
        x1, y1, x2, y2 = self.rect
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        x2 = x1 + ((x2 - x1) // 2) * 2
        y2 = y1 + ((y2 - y1) // 2) * 2
        self.rect = [x1, y1, x2, y2]
        self.draw()
        if x2 - x1 < 64 or y2 - y1 < 64:
            messagebox.showwarning("Малая область", "Выберите область не меньше 64 × 64 пикселей.", parent=self.window)
            return
        self.close((x1, y1, x2, y2))

    def close(self, region=None) -> None:
        self.window.destroy()
        self.app.picker = None
        if region is not None:
            self.app.set_region(region)
        else:
            self.app.show_selection_frame()
        self.app.deiconify()
        self.app.lift()
        self.app.focus_force()


class SelectionFrame:
    def __init__(self, app: "DeskRecApp", color="#25d7c4") -> None:
        self.app = app
        self.windows: list[Toplevel] = []
        x1, y1, x2, y2 = app.region
        for x, y, width, height in (
            (x1 - 2, y1 - 2, x2 - x1 + 4, 2),
            (x1 - 2, y2, x2 - x1 + 4, 2),
            (x1 - 2, y1, 2, y2 - y1),
            (x2, y1, 2, y2 - y1),
        ):
            window = Toplevel(app)
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.configure(bg=color)
            window.geometry(f"{width}x{height}+{max(0,x)}+{max(0,y)}")
            exclude_from_capture(window)
            click_through(window)
            keep_on_top(window, detach=True)
            self.windows.append(window)

    def destroy(self) -> None:
        for window in self.windows:
            try:
                if window.winfo_exists():
                    window.destroy()
            except TclError:
                pass


class RecordingOverlay(SelectionFrame):
    width, height = 340, 58

    def __init__(self, app: "DeskRecApp") -> None:
        super().__init__(app, color="#f06464")
        x1, y1, x2, y2 = app.region
        self.bar = Toplevel(app)
        self.bar.overrideredirect(True)
        self.bar.attributes("-topmost", True)
        self.bar.attributes("-alpha", 0.9)
        self.bar.configure(bg="#102238")
        left, top, right, bottom = work_area(app)
        x, y = app.panel_position or (right-self.width-20, (top+bottom-self.height)//2)
        self.position = self.clamp_position(x, y)
        self.bar.geometry(f"{self.width}x{self.height}+{self.position[0]}+{self.position[1]}")
        self.hint = StopHotkey.label if app.stop_hotkey and app.stop_hotkey.available else "Стоп — также в окне на панели задач"
        self.label = ttk.Label(self.bar, text="● 00:00 · 0 МБ", foreground="#ff6969",
                               background="#102238", font=("Segoe UI", 9, "bold"))
        self.label.pack(side="left", padx=10)
        self.stop_button = ttk.Button(self.bar, text="Стоп", command=app.stop_recording)
        self.stop_button.pack(side="right", padx=6, pady=5)
        exclude_from_capture(self.bar)
        keep_on_top(self.bar, detach=True)
        self.windows.append(self.bar)
        self.last_raised = 0.0
        self.drag_origin = None
        self.bar.bind("<ButtonPress-1>", self.begin_drag)
        self.bar.bind("<B1-Motion>", self.drag)
        self.bar.bind("<ButtonRelease-1>", self.end_drag)

    def clamp_position(self, x, y):
        left, top, right, bottom = work_area(self.app)
        return max(left, min(right-self.width, x)), max(top, min(bottom-self.height, y))

    def begin_drag(self, event):
        if event.widget is self.stop_button:
            self.drag_origin = None
            return
        self.drag_origin = (event.x_root, event.y_root, self.bar.winfo_x(), self.bar.winfo_y())

    def drag(self, event):
        if self.drag_origin is None:
            return
        x, y, old_x, old_y = self.drag_origin
        new_x, new_y = self.clamp_position(old_x+event.x_root-x, old_y+event.y_root-y)
        self.position = new_x, new_y
        self.bar.geometry(f"+{new_x}+{new_y}")

    def end_drag(self, event):
        if self.drag_origin is not None:
            self.drag(event)
            self.drag_origin = None
            self.app.panel_position = self.position
            self.app.save_settings()

    def update(self, seconds: int, bytes_used: int) -> None:
        self.label.configure(text=f"● {seconds//60:02d}:{seconds%60:02d} · {format_size(bytes_used)}\n{self.hint}")
        if time.monotonic() - self.last_raised > 0.5:
            self.last_raised = time.monotonic()
            x, y = self.clamp_position(self.bar.winfo_x(), self.bar.winfo_y())
            if (x, y) != (self.bar.winfo_x(), self.bar.winfo_y()):
                self.bar.geometry(f"+{x}+{y}")
            for window in self.windows:
                keep_on_top(window, detach=True)

class DeskRecApp(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DeskRec Buddy")
        self.geometry("565x470")
        self.minsize(565, 470)
        try:
            self.iconbitmap(str(ROOT / "assets" / "deskrec-buddy.ico"))
        except TclError:
            pass
        self.configure(bg="#0b1524")
        self.region: tuple[int, int, int, int] | None = None
        self.session: CaptureSession | None = None
        self.overlay: RecordingOverlay | None = None
        self.selection_frame: SelectionFrame | None = None
        self.picker: RegionPicker | None = None
        self.stop_hotkey = None
        self.recording_active = False
        self.starting = False
        self.close_after_save = False
        self.saving = False
        self.recovering = False
        self.events: queue.Queue = queue.Queue()
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        position = settings.get("panel_position")
        self.panel_position = tuple(position) if isinstance(position, list) and len(position) == 2 and all(type(n) is int for n in position) else None
        self.folder_var = StringVar(value=settings.get("folder", str(Path.home() / "Videos" / "DeskRec Buddy")))
        self.quality_var = StringVar(value="Баланс")
        gain = settings.get("microphone_gain", "200%")
        self.microphone_gain_var = StringVar(value=gain if gain in MICROPHONE_GAINS else "200%")
        self.region_var = StringVar(value="Область не выбрана")
        self.status_var = StringVar(value="Готово к записи")
        self.speaker_names: dict[str, str] = {}
        self.microphone_names: dict[str, str] = {}
        self._build()
        self.fit_window()
        self.status_var.trace_add("write", lambda *_: self.after_idle(self.fit_window))
        self.refresh_devices()
        self.quality_var.trace_add("write", lambda *_: self.update_estimate())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll)
        self.after(0, self.recover_previous)

    def _build(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#0b1524")
        style.configure("TLabel", background="#0b1524", foreground="#d8e4ec", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10), padding=7)
        style.configure("TCombobox", padding=5)
        style.configure("Accent.TButton", background="#22c9b7", foreground="#071420", font=("Segoe UI", 11, "bold"))
        frame = ttk.Frame(self, padding=18)
        self.content_frame = frame
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="DeskRec Buddy", font=("Segoe UI", 19, "bold"), foreground="#ffffff").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(frame, text="Экран + микрофон + звук компьютера", foreground="#8fa7b6").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 17))
        ttk.Label(frame, text="Область").grid(row=2, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.region_var).grid(row=2, column=1, sticky="w")
        ttk.Button(frame, text="Выбрать рамкой", command=self.pick_region).grid(row=2, column=2, sticky="e")
        ttk.Label(frame, text="Качество").grid(row=3, column=0, sticky="w", pady=8)
        ttk.Combobox(frame, textvariable=self.quality_var, values=list(QUALITY), state="readonly", width=17).grid(
            row=3, column=1, sticky="w")
        self.estimate_label = ttk.Label(frame, text="")
        self.estimate_label.grid(row=3, column=2, sticky="e")
        ttk.Label(frame, text="Звук компьютера").grid(row=4, column=0, sticky="w", pady=8)
        self.speaker_box = ttk.Combobox(frame, state="readonly", width=42)
        self.speaker_box.grid(row=4, column=1, columnspan=2, sticky="ew")
        ttk.Label(frame, text="Микрофон").grid(row=5, column=0, sticky="w", pady=8)
        self.microphone_box = ttk.Combobox(frame, state="readonly", width=42)
        self.microphone_box.grid(row=5, column=1, columnspan=2, sticky="ew")
        ttk.Label(frame, text="Усиление голоса").grid(row=6, column=0, sticky="w", pady=8)
        self.microphone_gain_box = ttk.Combobox(frame, textvariable=self.microphone_gain_var,
                                               values=MICROPHONE_GAINS, state="readonly", width=17)
        self.microphone_gain_box.grid(row=6, column=1, sticky="w")
        self.microphone_gain_box.bind("<<ComboboxSelected>>", lambda _: self.save_settings())
        ttk.Label(frame, text="Только в записи", foreground="#8fa7b6", font=("Segoe UI", 9)).grid(
            row=6, column=2, sticky="e")
        ttk.Label(frame, text="Сохранять в").grid(row=7, column=0, sticky="w", pady=8)
        ttk.Entry(frame, textvariable=self.folder_var).grid(row=7, column=1, sticky="ew")
        ttk.Button(frame, text="Обзор…", command=self.choose_folder).grid(row=7, column=2, sticky="e")
        ttk.Separator(frame).grid(row=8, column=0, columnspan=3, sticky="ew", pady=16)
        ttk.Label(frame, textvariable=self.status_var, wraplength=510).grid(
            row=9, column=0, columnspan=3, sticky="w")
        self.start_button = ttk.Button(frame, text="Начать запись", style="Accent.TButton", command=self.start_recording)
        self.start_button.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(17, 0))
        ttk.Button(frame, text="Ярлык на рабочем столе", command=self.make_shortcut).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(frame, text=f"v{VERSION} · {PUBLISHER}", foreground="#668091", font=("Segoe UI", 9)).grid(
            row=11, column=2, sticky="e", pady=(10, 0))
        frame.columnconfigure(1, weight=1)
        self.update_estimate()

    def fit_window(self):
        self.update_idletasks()
        self.minsize(max(565, self.content_frame.winfo_reqwidth()), self.content_frame.winfo_reqheight())

    def set_region(self, region: tuple[int, int, int, int]) -> None:
        self.region = region
        x1, y1, x2, y2 = region
        self.region_var.set(f"{x2-x1} × {y2-y1} пикс.  ({x1}, {y1})")
        self.show_selection_frame()

    def show_selection_frame(self) -> None:
        self.hide_selection_frame()
        if self.region is not None:
            self.selection_frame = SelectionFrame(self)

    def hide_selection_frame(self) -> None:
        if self.selection_frame:
            self.selection_frame.destroy()
            self.selection_frame = None

    def pick_region(self) -> None:
        if self.starting or self.session or self.saving:
            return
        if self.picker is None:
            self.hide_selection_frame()
            self.withdraw()
            self.picker = RegionPicker(self)

    def choose_folder(self) -> None:
        if self.starting or self.session or self.saving:
            return
        chosen = filedialog.askdirectory(title="Папка для MP4", initialdir=self.folder_var.get())
        if chosen:
            self.folder_var.set(chosen)
            self.save_settings()
            self.recover_previous()

    def save_settings(self):
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = SETTINGS_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps({"folder": self.folder_var.get(), "panel_position": self.panel_position,
                                             "microphone_gain": self.microphone_gain_var.get()}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(SETTINGS_PATH)
        except OSError:
            logging.exception("Cannot save settings")
            self.status_var.set("Не удалось запомнить настройки; выбранные параметры действуют до закрытия")

    def make_shortcut(self):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(APP_NAME, "Создание ярлыка доступно в собранной версии приложения.")
            return
        try:
            name = create_desktop_shortcut()
            messagebox.showinfo(APP_NAME, f"Ярлык создан на рабочем столе:\n{name}")
        except Exception as exc:
            logging.exception("Cannot create desktop shortcut")
            messagebox.showerror(APP_NAME, str(exc))

    def report_callback_exception(self, exc_type, exc_value, traceback):
        logging.error("UI callback failed", exc_info=(exc_type, exc_value, traceback))
        messagebox.showerror(APP_NAME, f"Ошибка интерфейса: {exc_value}\nПодробности в logs/deskrec.log рядом с настройками.")

    def recover_previous(self):
        if self.recovering or self.session or self.saving:
            return
        self.recovering = True
        self.start_button.configure(state="disabled")
        destination = Path(self.folder_var.get())

        def task():
            try:
                self.events.put(("recovered", recover_pending_recordings(destination)))
            except Exception as exc:
                self.events.put(("recovered", ([], [str(exc)])))

        threading.Thread(target=task, daemon=True).start()

    def refresh_devices(self) -> None:
        try:
            speakers, microphones = devices()
        except Exception:
            logging.exception("Cannot enumerate audio devices")
            speakers, microphones = [], []
            self.status_var.set("Не найдены звуковые устройства. Проверьте подключение и перезапустите приложение.")
        self.speaker_names = {f"{i+1}. {speaker.name}": speaker.id for i, speaker in enumerate(speakers)}
        self.microphone_names = {f"{i+1}. {mic.name}": mic.id for i, mic in enumerate(microphones)}
        self.speaker_box.configure(values=list(self.speaker_names))
        self.microphone_box.configure(values=list(self.microphone_names))
        default_speaker_id = None
        default_mic_id = None
        try:
            import soundcard as sc
            default_speaker_id = sc.default_speaker().id
            default_mic_id = sc.default_microphone().id
        except Exception:
            pass
        self.speaker_box.set(next((name for name, id_ in self.speaker_names.items() if id_ == default_speaker_id),
                                  next(iter(self.speaker_names), "")))
        self.microphone_box.set(next((name for name, id_ in self.microphone_names.items() if id_ == default_mic_id),
                                     next(iter(self.microphone_names), "")))

    def update_estimate(self) -> None:
        self.estimate_label.configure(text=f"≈ {format_size(estimate_bytes(3600, self.quality_var.get()))}/час")

    def start_recording(self) -> None:
        if self.recovering or self.starting or self.saving or self.session:
            return
        if self.region is None:
            self.pick_region()
            return
        speaker_id = self.speaker_names.get(self.speaker_box.get())
        microphone_id = self.microphone_names.get(self.microphone_box.get())
        if not speaker_id or not microphone_id:
            messagebox.showerror("Звуковые устройства", "Выберите колонки и микрофон.")
            return
        folder = Path(self.folder_var.get()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not folder.is_dir():
                raise NotADirectoryError(folder)
        except OSError as exc:
            messagebox.showerror("Папка", str(exc))
            return
        self.start_button.configure(state="disabled")
        self.starting = True
        self.save_settings()
        self.status_var.set("Подготовка записи…")
        options = CaptureOptions(self.region, self.quality_var.get(), folder, speaker_id, microphone_id,
                                 microphone_gain=float(self.microphone_gain_var.get().rstrip("%")) / 100)

        def task() -> None:
            try:
                session = CaptureSession(options)
                self.session = session
                session.start()
                self.events.put(("started", None))
            except Exception as exc:
                logging.exception("Cannot start recording")
                if self.session:
                    failed_session = self.session
                    try:
                        failed_session.cancel()
                    except Exception:
                        logging.exception("Cannot finish failed preparation")
                    self.session = None
                self.events.put(("error", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def stop_recording(self) -> None:
        if not self.session:
            return
        self.session.stop_event.set()
        self.recording_active = False
        if self.stop_hotkey:
            self.stop_hotkey.stop()
            self.stop_hotkey = None
        self.start_button.configure(text="Сохраняю MP4…", state="disabled")
        self.status_var.set("Сохраняю MP4…")
        if self.overlay:
            self.overlay.destroy()
            self.overlay = None
        self.deiconify()
        self.lift()
        output = self.session.output_path
        session = self.session
        self.session = None
        self.saving = True

        def task() -> None:
            try:
                result = session.stop_and_save(output)
                self.events.put(("saved", str(result)))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "started":
                    self.starting = False
                    if self.close_after_save:
                        self.stop_recording()
                        continue
                    self.recording_active = True
                    self.hide_selection_frame()
                    self.stop_hotkey = StopHotkey(lambda: self.events.put(("stop_requested", None)))
                    self.start_button.configure(text="Стоп и сохранить", command=self.stop_recording, state="normal")
                    self.title("DeskRec Buddy — идёт запись")
                    self.iconify()
                    self.overlay = RecordingOverlay(self)
                    self.status_var.set(f"Идёт запись · {StopHotkey.label} — остановить\nMP4: {self.session.output_path}")
                elif kind == "stop_requested":
                    self.stop_recording()
                elif kind == "recovered":
                    self.recovering = False
                    self.start_button.configure(state="normal")
                    recovered, errors = value
                    if recovered:
                        self.status_var.set("Восстановлена запись: " + str(recovered[-1]))
                    if errors:
                        self.status_var.set("MP4 сохранён, но завершить восстановление не удалось: " + errors[0])
                elif kind == "saved":
                    self.saving = False
                    logging.info("Saved recording: %s", value)
                    if self.close_after_save:
                        self.destroy()
                        return
                    self.title("DeskRec Buddy")
                    self.start_button.configure(text="Начать запись", command=self.start_recording, state="normal")
                    self.show_selection_frame()
                    self.status_var.set(f"Сохранено: {value}")
                    messagebox.showinfo("DeskRec Buddy", f"MP4 сохранён:\n{value}")
                elif kind == "error":
                    logging.error("Recording error: %s", value)
                    self.starting = False
                    self.saving = False
                    self.deiconify()
                    self.title("DeskRec Buddy")
                    self.start_button.configure(text="Начать запись", command=self.start_recording, state="normal")
                    self.show_selection_frame()
                    self.status_var.set("Ошибка записи")
                    messagebox.showerror("DeskRec Buddy", value)
                    if self.close_after_save:
                        self.destroy()
                        return
        except queue.Empty:
            pass
        if self.session and self.recording_active:
            try:
                if not self.overlay or not self.overlay.bar.winfo_exists():
                    if self.overlay:
                        self.overlay.destroy()
                    self.overlay = RecordingOverlay(self)
                seconds = int(time.monotonic() - self.session.started_at)
                self.overlay.update(seconds, self.session.current_bytes())
            except (TclError, OSError):
                # Keep an accessible Stop button even if Windows removed a
                # transient control window or a display was disconnected.
                self.deiconify()
            if self.session.errors:
                self.stop_recording()
        self.after(150, self.poll)

    def on_close(self) -> None:
        if self.starting:
            self.close_after_save = True
            self.status_var.set("Завершаю подготовку и сохраняю запись перед выходом…")
        elif self.session:
            self.close_after_save = True
            self.stop_recording()
        elif self.saving:
            self.close_after_save = True
            self.status_var.set("Дождитесь окончания сохранения MP4…")
        else:
            self.destroy()


def main():
    make_dpi_aware()
    configure_logging()
    if sys.argv[1:2] == ["--create-shortcut"]:
        try:
            name = create_desktop_shortcut()
            if "--quiet" not in sys.argv:
                ctypes.windll.user32.MessageBoxW(None, f"На рабочем столе создан ярлык:\n{name}", APP_NAME, 0x40)
        except Exception as exc:
            logging.exception("Shortcut command failed")
            if "--quiet" not in sys.argv:
                ctypes.windll.user32.MessageBoxW(None, str(exc), APP_NAME, 0x10)
            return 1
        return 0
    if sys.argv[1:2] == ["--smoke"]:
        import argparse
        import soundcard as sc
        parser = argparse.ArgumentParser(description="DeskRec recording diagnostic")
        parser.add_argument("output_dir", type=Path)
        parser.add_argument("--full-hd", action="store_true")
        parser.add_argument("--seconds", type=float)
        parser.add_argument("--keep-name", action="store_true")
        diagnostic = parser.parse_args(sys.argv[2:])
        output_dir = diagnostic.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        session = CaptureSession(CaptureOptions(
            (0, 0, 1920, 1080) if diagnostic.full_hd else (100, 100, 740, 460),
            "Баланс" if diagnostic.full_hd else "Экономно", output_dir,
            sc.default_speaker().id, sc.default_microphone().id,
        ))
        session.start()
        time.sleep(diagnostic.seconds if diagnostic.seconds is not None else 10 if diagnostic.full_hd else 2.5)
        session.stop_and_save(None if diagnostic.keep_name else output_dir / f"portable-smoke-{int(time.time())}.mp4")
    else:
        guard = InstanceGuard()
        try:
            if not guard.acquired:
                ctypes.windll.user32.MessageBoxW(None, "DeskRec Buddy уже запущен. Откройте его через панель задач.\nОстановка записи: Ctrl+Shift+F9.", APP_NAME, 0x40)
                return 0
            DeskRecApp().mainloop()
        finally:
            guard.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logging.exception("Application failed")
        ctypes.windll.user32.MessageBoxW(None, f"Не удалось запустить DeskRec Buddy:\n{exc}\nПодробности в logs/deskrec.log рядом с настройками.", APP_NAME, 0x10)
        sys.exit(1)

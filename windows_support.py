"""Small Win32 helpers: interruptible audio pipes and independent stop controls."""

import ctypes
from ctypes import wintypes
import threading


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)


class Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]


kernel32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.DWORD, ctypes.c_void_p]
kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
kernel32.ConnectNamedPipe.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(Overlapped)]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped),
                                        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.GetOverlappedResult.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class AudioPipe:
    def __init__(self, name, stop_event):
        self.name = name
        self.stop_event = stop_event
        self.handle = kernel32.CreateNamedPipeW(name, 0x40000002, 0, 1, 262144, 262144, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.event = kernel32.CreateEventW(None, True, False, None)
        if not self.event:
            kernel32.CloseHandle(self.handle)
            raise ctypes.WinError(ctypes.get_last_error())

    def _complete(self, operation, ok):
        transferred = wintypes.DWORD()
        if not ok:
            error = ctypes.get_last_error()
            if error == 535:  # client connected before ConnectNamedPipe
                return 0
            if error != 997:  # ERROR_IO_PENDING
                raise ctypes.WinError(error)
            while kernel32.WaitForSingleObject(self.event, 50) == 258:
                if self.stop_event.is_set():
                    kernel32.CancelIoEx(self.handle, ctypes.byref(operation))
                    kernel32.GetOverlappedResult(self.handle, ctypes.byref(operation), ctypes.byref(transferred), True)
                    raise InterruptedError("Recording stopped")
        if not kernel32.GetOverlappedResult(self.handle, ctypes.byref(operation), ctypes.byref(transferred), False):
            raise ctypes.WinError(ctypes.get_last_error())
        return transferred.value

    def connect(self):
        kernel32.ResetEvent(self.event)
        operation = Overlapped(hEvent=self.event)
        self._complete(operation, kernel32.ConnectNamedPipe(self.handle, ctypes.byref(operation)))

    def write(self, data):
        offset = 0
        buffer = ctypes.create_string_buffer(data)
        while offset < len(data):
            kernel32.ResetEvent(self.event)
            operation = Overlapped(hEvent=self.event)
            count = wintypes.DWORD()
            ok = kernel32.WriteFile(self.handle, ctypes.byref(buffer, offset), len(data) - offset,
                                    ctypes.byref(count), ctypes.byref(operation))
            written = self._complete(operation, ok)
            if not written:
                raise BrokenPipeError("Audio pipe closed")
            offset += written

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            kernel32.CloseHandle(self.event)
            self.handle = None


kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]


def process_alive(pid):
    if not pid:
        return False
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel32.CloseHandle(handle)


user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]


def work_area(window):
    area = wintypes.RECT()
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):
        return area.left, area.top, area.right, area.bottom
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()


def keep_on_top(window, detach=False):
    hwnd = int(window.frame(), 0)
    if detach:
        # An owned toolbar disappears when its owner is minimized (including
        # Win+D). Keep the recording controls independent from the setup window.
        user32.SetWindowLongPtrW(hwnd, -8, 0)
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 4)
    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002 | 0x0040)


user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class StopHotkey:
    label = "Ctrl+Shift+F9"

    def __init__(self, callback):
        self.callback = callback
        self.ready = threading.Event()
        self.available = False
        self.thread_id = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(2)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        self.available = bool(user32.RegisterHotKey(None, 1, 0x4006, 0x78))
        self.ready.set()
        if not self.available:
            return
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312:
                    self.callback()
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self.thread.is_alive():
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=1)

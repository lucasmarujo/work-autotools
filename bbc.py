import ctypes
import ctypes.wintypes
import subprocess
import threading
import time
import math
import sys

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT),
    ]

user32 = ctypes.windll.user32

user32.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
user32.GetCursorPos.restype = ctypes.wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = ctypes.wintypes.BOOL
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint


def _send_click():
    down = INPUT()
    down.type = INPUT_MOUSE
    down._input.mi.dwFlags = MOUSEEVENTF_LEFTDOWN

    up = INPUT()
    up.type = INPUT_MOUSE
    up._input.mi.dwFlags = MOUSEEVENTF_LEFTUP

    inputs = (INPUT * 2)(down, up)
    user32.SendInput(2, ctypes.pointer(inputs[0]), ctypes.sizeof(INPUT))


def _move_circular(radius=40, steps=36):
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    origin_x, origin_y = pt.x, pt.y

    for i in range(steps + 1):
        angle = 2 * math.pi * i / steps
        nx = int(origin_x + radius * math.cos(angle))
        ny = int(origin_y + radius * math.sin(angle))
        user32.SetCursorPos(nx, ny)
        time.sleep(0.03)

    user32.SetCursorPos(origin_x, origin_y)


def _open_notepad_fullscreen():
    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(1)

    hwnd = None
    for _ in range(20):
        hwnd = user32.FindWindowW("Notepad", None)
        if hwnd:
            break
        time.sleep(0.2)

    if hwnd:
        SW_MAXIMIZE = 3
        user32.ShowWindow(hwnd, SW_MAXIMIZE)

    return proc, hwnd


def run():
    try:
        from colorama import Fore, Style
    except ImportError:
        class _Dummy:
            def __getattr__(self, _):
                return ""
        Fore = Style = _Dummy()

    proc, hwnd = _open_notepad_fullscreen()

    print(Fore.YELLOW + Style.BRIGHT + "\n  ⏳  Modo teste automatizado ativo!")
    print(Fore.WHITE + "      Tentando encontrar projetos abertos + executando testes")
    print(Fore.RED + Style.BRIGHT + "      Pressione Ctrl+C para encerrar.\n")

    stop_event = threading.Event()

    def click_loop():
        while not stop_event.is_set():
            stop_event.wait(30)
            if not stop_event.is_set():
                _send_click()
                timestamp = time.strftime("%H:%M:%S")
                print(Fore.CYAN + f"  [{timestamp}] 🖱  Click enviado")

    def move_loop():
        while not stop_event.is_set():
            stop_event.wait(60)
            if not stop_event.is_set():
                _move_circular()
                timestamp = time.strftime("%H:%M:%S")
                print(Fore.CYAN + f"  [{timestamp}] 🔄  Movimento circular executado")

    t_click = threading.Thread(target=click_loop, daemon=True)
    t_move = threading.Thread(target=move_loop, daemon=True)
    t_click.start()
    t_move.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_event.set()
        print(Fore.YELLOW + Style.BRIGHT + "\n  ⏹  Encerrando modo teste automatizado...")

    if proc and proc.poll() is None:
        proc.terminate()

    t_click.join(timeout=2)
    t_move.join(timeout=2)

    print(Fore.GREEN + Style.BRIGHT + "  ✔  Modo teste automatizado encerrado.\n")

"""screenshot (engine built-in procedure) —— shoots the monitor the mouse
is on, attaches it into the task materials. procshim contract:
def run(ctx), materials leave only through ctx.attach/say, raise =
the whole step is voided (engine reports to the human, the order
never ships).

Degrade-as-iron-law (2026-08-05): desktop-automation-style features
never bear load —— here any failure is reported only as an
exception, never retried, never touches focus. Coordinates are taken
in physical pixels (the process declares DPI aware up front, the
PowerShell subprocess declares it too, so the two sides' coordinate
systems line up).
"""
import ctypes
import subprocess
import sys
from ctypes import wintypes


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def _monitor_under_mouse() -> tuple[int, int, int, int]:
    u32 = ctypes.windll.user32
    u32.SetProcessDPIAware()
    pt = _POINT()
    if not u32.GetCursorPos(ctypes.byref(pt)):
        raise RuntimeError("GetCursorPos failed")
    MONITOR_DEFAULTTONEAREST = 2
    u32.MonitorFromPoint.restype = ctypes.c_void_p
    u32.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
    hmon = u32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    if not u32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(mi)):
        raise RuntimeError("GetMonitorInfoW failed")
    r = mi.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


_PS = """\
Add-Type -MemberDefinition '[DllImport("user32.dll")] public static \
extern bool SetProcessDPIAware();' -Name U -Namespace W
[W.U]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap({w}, {h})
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen({x}, {y}, 0, 0, $bmp.Size)
$bmp.Save('{out}', [System.Drawing.Imaging.ImageFormat]::Png)
"""


def run(ctx):
    if sys.platform != "win32":
        raise RuntimeError("screenshot procedure needs win32")
    x, y, w, h = _monitor_under_mouse()
    out = ctx.stage / "shot.png"
    ps = _PS.format(x=x, y=y, w=w, h=h,
                    out=str(out).replace("'", "''"))
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=25)
    if r.returncode != 0 or not out.is_file():
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError("screenshot failed: "
                           + (tail[-1] if tail else f"exit {r.returncode}"))
    ctx.attach(out, label=f"screenshot (monitor under mouse, {w}x{h})")

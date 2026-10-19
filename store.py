import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "mongo-transfer"
CONN_FILE = APP_DIR / "connections.json"
SETTINGS_FILE = APP_DIR / "settings.json"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(fn, data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


# 連線字串可能含密碼，用 Windows DPAPI 綁定目前使用者帳號加密
def encrypt(text: str) -> str:
    return base64.b64encode(_dpapi(ctypes.windll.crypt32.CryptProtectData, text.encode())).decode()


def decrypt(token: str) -> str:
    return _dpapi(ctypes.windll.crypt32.CryptUnprotectData, base64.b64decode(token)).decode()


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write(path, data):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_connections():
    return [{"name": c["name"], "uri": decrypt(c["uri"])} for c in _read(CONN_FILE, [])]


def save_connections(conns):
    _write(CONN_FILE, [{"name": c["name"], "uri": encrypt(c["uri"])} for c in conns])


def load_settings():
    return _read(SETTINGS_FILE, {})


def save_settings(settings):
    _write(SETTINGS_FILE, settings)

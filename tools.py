import glob
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

TOOLS = ("mongodump", "mongorestore", "mongoexport", "mongoimport")
FORMATS = {".json": "json", ".csv": "csv", ".bson": "bson"}


def find_tools_dir(preferred=None):
    which = shutil.which("mongodump")
    dirs = [preferred, which and os.path.dirname(which), r"C:\tools\mongodb-tools\bin",
            *sorted(glob.glob(r"C:\Program Files\MongoDB\Tools\*\bin"), reverse=True)]
    for d in dirs:
        if d and all(os.path.isfile(os.path.join(d, t + ".exe")) for t in TOOLS):
            return d
    return None


def strip_db(uri):
    """URI 帶資料庫會跟 --db 衝突，移到 authSource 以保留原本的驗證來源"""
    scheme, sep, rest = uri.partition("://")
    hosts, _, tail = rest.partition("/")
    db, _, query = tail.partition("?")
    if not db:
        return uri
    if not any(p.split("=", 1)[0].lower() == "authsource" for p in query.split("&")):
        query = f"{query}&authSource={db}" if query else f"authSource={db}"
    return f"{scheme}{sep}{hosts}/?{query}"


def mask_credentials(text):
    """mongo tools 連線失敗時會印出含帳密的完整 URI"""
    return re.sub(r"(mongodb(?:\+srv)?://)[^/\s]*@", r"\1***@", text)


def write_config(uri):
    """URI 寫進暫存 --config 檔，避免密碼出現在命令列（工作管理員看得到）"""
    fd, path = tempfile.mkstemp(prefix="mongo-transfer-", suffix=".yaml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"uri: {json.dumps(strip_db(uri))}\n")
    return path


def _base_name(path):
    name = Path(path).name
    return name[:-3] if name.lower().endswith(".gz") else name


def file_format(path):
    """只有 BSON 接受 .gz：mongoimport 不能讀壓縮檔"""
    fmt = FORMATS.get(Path(_base_name(path)).suffix.lower())
    return fmt if fmt == "bson" or not path.lower().endswith(".gz") else None


def coll_from_file(path):
    name = _base_name(path)
    return name.rsplit(".", 1)[0] if file_format(path) else name


def is_json_array(path):
    with open(path, "rb") as f:
        return f.read(4096).lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"[")


def flatten_keys(docs):
    keys = {}

    def walk(doc, prefix):
        for k, v in doc.items():
            if isinstance(v, dict) and v:
                walk(v, f"{prefix}{k}.")
            else:
                keys[prefix + k] = None

    for d in docs:
        walk(d, "")
    return list(keys)


def export_args(db, coll, fmt, out_dir, query="", fields="", json_array=False, pretty=False, gzip=False):
    args = ["--db", db, "--collection", coll] + (["--query", query] if query else [])
    if fmt == "bson":
        return "mongodump", args + ["--out", out_dir] + ["--gzip"] * gzip
    args += ["--out", str(Path(out_dir) / f"{coll}.{fmt}")]
    if fmt == "csv":
        return "mongoexport", args + ["--type=csv", "--fields", fields]
    return "mongoexport", args + ["--jsonArray"] * json_array + ["--pretty"] * pretty


def import_args(db, coll, path, drop=False, mode="insert"):
    fmt = file_format(path)
    args = ["--db", db, "--collection", coll] + ["--drop"] * drop
    if fmt == "bson":
        return "mongorestore", args + ["--gzip"] * path.lower().endswith(".gz") + [path]
    args += ["--file", path, "--mode", mode]
    if fmt == "csv":
        return "mongoimport", args + ["--type=csv", "--headerline"]
    return "mongoimport", args + ["--jsonArray"] * is_json_array(path)

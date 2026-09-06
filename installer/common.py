"""Shared, deliberately narrow release inputs and private atomic file writes."""
from __future__ import annotations

import hashlib
import gzip
import json
import os
from pathlib import Path
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"__pycache__", ".pytest_cache", ".venv", ".build", "dist", "runtime", "models", "voices", "outputs", ".git"}


def source_files(root: Path = ROOT):
    """Allowlist deployable source; never package a workspace recursively."""
    for name in ("VERSION", "README.md", "AGENTS.md", "install.py", "Makefile", "installer", "docs", "services/voice-backend"):
        base = root / name
        for path in sorted(base.rglob("*") if base.is_dir() else [base]):
            rel = path.relative_to(root)
            if path.is_symlink() or not path.is_file():
                continue
            if any(p.startswith(".") or p in EXCLUDED or p.endswith(".egg-info") for p in rel.parts):
                continue
            if path.name.startswith(".") or path.suffix in {".pyc", ".key", ".pem", ".log", ".sqlite3", ".wav", ".flac"}:
                continue
            yield path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_id(root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for path in source_files(root):
        h.update(str(path.relative_to(root)).encode() + b"\0" + bytes.fromhex(digest(path)))
    return h.hexdigest()


def product_source_id(root: Path = ROOT) -> str:
    """Fallback product identity includes the Mac source and CI, not just deploy code."""
    h = hashlib.sha256(bytes.fromhex(source_id(root)))
    for name in ("apps/macos-client", "scripts", ".github"):
        for path in sorted((root / name).rglob("*")):
            relative = path.relative_to(root)
            if path.is_symlink() or not path.is_file() or any(p in EXCLUDED or (p.startswith(".") and p != ".github") for p in relative.parts):
                continue
            if path.suffix in {".pyc", ".log"} or path.name == ".DS_Store":
                continue
            h.update(str(relative).encode() + b"\0" + bytes.fromhex(digest(path)))
    return h.hexdigest()


def archive(target: Path, root: Path = ROOT) -> None:
    with target.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as out:
        for path in source_files(root):
            info = out.gettarinfo(str(path), arcname=str(path.relative_to(root)))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o644
            with path.open("rb") as stream:
                out.addfile(info, stream)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".joi-")
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path: Path, value) -> None:
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())

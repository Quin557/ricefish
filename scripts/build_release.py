#!/usr/bin/env python3
"""Build the public release zip from the committed tree.

The zip is intentionally built with ``git archive`` so that it contains
exactly the files tracked in the public commit.  Local-only files such as
``.env``, ``data/``, ``vendor/`` and personal persona files can never leak
into a release, even when they exist in the working tree.

Usage::

    python scripts/build_release.py
    python scripts/build_release.py --version 0.3.0 --allow-dirty

Output::

    dist/ricefish-v0.3.0.zip
    dist/ricefish-v0.3.0.zip.sha256
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

REQUIRED = (
    "start.bat",
    "setup.ps1",
    "run.py",
    "README.md",
    "使用说明.txt",
    "LICENSE",
    ".env.example",
    "config/bot.yaml",
    "config/personas/global.md",
    "config/personas/groups/example.md",
    "assets/emoji/manifest.json",
)

FORBIDDEN_PREFIXES = (
    "data/",
    "vendor/",
    "dist/",
    ".tmp/",
    ".git/",
    ".ruff_cache/",
    ".pytest_cache/",
    "config/personas/private/",
)

FORBIDDEN_EXACT = {
    ".env",
    ".env.local",
    "qr-now.png",
    "setup.log",
    "config/personas/learned.md",
}

FORBIDDEN_SUFFIXES = (
    ".pyc",
    ".db",
    ".db-wal",
    ".db-shm",
)

GROUP_PERSONA_RE = re.compile(r"^config/personas/groups/\d+\.md$")


def read_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("无法从 pyproject.toml 读取版本号")
    return match.group(1)


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：\n{result.stderr or result.stdout}")
    return result.stdout


def check_worktree(allow_dirty: bool) -> None:
    if allow_dirty:
        return
    dirty = run_git("status", "--porcelain").strip()
    if dirty:
        raise SystemExit(
            "工作树不干净，先提交再打发行包（或加 --allow-dirty 只做本地预览）：\n"
            + dirty
        )


def validate_archive(path: Path, prefix: str) -> list[str]:
    problems: list[str] = []
    with zipfile.ZipFile(path) as archive:
        broken = archive.testzip()
        if broken:
            raise SystemExit(f"zip 损坏：{broken}")
        names = [name for name in archive.namelist() if not name.endswith("/")]

    relative: list[str] = []
    for name in names:
        if not name.startswith(prefix):
            problems.append(f"文件不在 {prefix} 前缀下：{name}")
            continue
        rel = name[len(prefix) :].replace("\\", "/")
        relative.append(rel)

        if rel in FORBIDDEN_EXACT:
            problems.append(f"包含禁止文件：{rel}")
        if rel.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"包含禁止目录：{rel}")
        if GROUP_PERSONA_RE.match(rel):
            problems.append(f"包含真实群人设：{rel}")
        if ".personal." in Path(rel).name:
            problems.append(f"包含个人备份：{rel}")
        if rel.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"包含运行数据：{rel}")
        parts = rel.split("/")
        if "__pycache__" in parts or ".git" in parts or ".tmp" in parts:
            problems.append(f"包含缓存/临时文件：{rel}")

    missing = [item for item in REQUIRED if item not in relative]
    for item in missing:
        problems.append(f"缺少必需文件：{item}")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 ricefish 发行 zip")
    parser.add_argument("--version", default="", help="覆盖 pyproject.toml 里的版本号")
    parser.add_argument("--out-dir", default=str(DIST), help="输出目录，默认 dist/")
    parser.add_argument("--allow-dirty", action="store_true", help="允许工作树有未提交修改")
    args = parser.parse_args(argv)

    version = args.version or read_version()
    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", version)
    if not safe_version:
        raise SystemExit("版本号不合法")

    check_worktree(args.allow_dirty)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    zip_name = f"ricefish-v{safe_version}.zip"
    prefix = f"ricefish-v{safe_version}/"
    final_path = out_dir / zip_name
    temp_path = out_dir / f".{zip_name}.tmp"
    if temp_path.exists():
        temp_path.unlink()

    run_git(
        "archive",
        "--format=zip",
        f"--prefix={prefix}",
        "-o",
        str(temp_path),
        "HEAD",
    )

    problems = validate_archive(temp_path, prefix)
    if problems:
        temp_path.unlink(missing_ok=True)
        print("发行包校验失败：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
    if final_path.exists():
        final_path.unlink()
    temp_path.replace(final_path)

    sha_path = out_dir / f"{zip_name}.sha256"
    sha_path.write_text(f"{digest}  {zip_name}\n", encoding="utf-8")

    size_mb = final_path.stat().st_size / (1024 * 1024)
    with zipfile.ZipFile(final_path) as archive:
        count = sum(1 for name in archive.namelist() if not name.endswith("/"))
    print(f"已生成：{final_path}")
    print(f"文件数：{count}，大小：{size_mb:.2f} MB")
    print(f"SHA256：{digest}")
    print(f"校验文件：{sha_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

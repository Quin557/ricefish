"""SQLite 在线备份：用 sqlite3 的 backup API，运行中也能安全拷。

    python scripts/backup_db.py --db data/bot.db --out data/backups --keep 14

备份文件名 bot-YYYYmmdd-HHMMSS.db；--keep 只保留最近 N 份。
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path


def backup(db: Path, out: Path, keep: int = 14) -> Path:
    if not db.is_file():
        raise FileNotFoundError(db)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"bot-{time.strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    backups = sorted(out.glob("bot-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old in backups[max(1, keep):]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--out", default="data/backups")
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()
    target = backup(Path(args.db), Path(args.out), max(1, args.keep))
    print(f"已备份：{target}（{target.stat().st_size / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

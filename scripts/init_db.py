"""初始化（或检查）本地数据库。

    python scripts/init_db.py
    python scripts/init_db.py --stats
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ricefish.memory.store import MemoryStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot.db")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    store = MemoryStore(args.db)
    stats = store.stats()
    print(f"数据库：{Path(args.db).resolve()}")
    for key, value in stats.items():
        if isinstance(value, dict):
            print(f"  {key}: {value}")
        else:
            print(f"  {key}: {value}")
    if args.stats:
        print("\n最近 10 条记忆：")
        for memory in store.list_memories(limit=10):
            print(f"  [{memory['scope_type']}:{memory['scope_id']}] "
                  f"{memory['content'][:60]}")
    store.close()
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

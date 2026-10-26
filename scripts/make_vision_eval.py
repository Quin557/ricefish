"""从一堆图片生成识图评测 manifest 模板。

    python scripts/make_vision_eval.py --src data/emoji-inbox --out data/vision-eval.json --limit 30
    # 然后手动给每一项的 expect 填 2~4 个关键词，再跑：
    python scripts/eval_vision.py --manifest data/vision-eval.json

图片不会被复制，只写相对项目根的路径；data/ 已在 .gitignore 里。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(ROOT / "data" / "emoji-inbox"))
    parser.add_argument("--out", default=str(ROOT / "data" / "vision-eval.json"))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    source = Path(args.src)
    if not source.is_dir():
        print(f"目录不存在：{source}")
        return 2
    files = [
        path
        for path in sorted(source.rglob("*"))
        if path.is_file() and path.suffix.lower() in SUFFIXES
    ][: max(1, args.limit)]
    items = [
        {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "expect": [],
            "kind": "unlabeled",
            "note": "把 expect 改成 2~4 个你希望图片卡命中的关键词",
        }
        for path in files
    ]
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {len(items)} 条：{target}")
    print("下一步：编辑每条 expect（2~4 个关键词），再运行：")
    print(f"  python scripts/eval_vision.py --manifest {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""识图评测：用一组带关键词的图片，检查图片卡是否抓到要点。

    python scripts/eval_vision.py --manifest scripts/vision-eval.example.json
    python scripts/eval_vision.py --manifest my-eval.json --limit 30

manifest 格式：
[
  {"path": "data/emoji-inbox/xxx.png", "expect": ["初音", "美味"], "kind": "meme"},
  ...
]
expect 里的关键词命中任意一个就算这条通过；同时输出平均耗时。
这不是单元测试（会花 token），是给 P2 做人工/回归评测用的。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ricefish.__main__ import _load_dotenv  # noqa: E402
from ricefish.config import Config  # noqa: E402
from ricefish.llm.client import DeepSeekClient  # noqa: E402
from ricefish.vision.cards import analyze_image, guess_mime  # noqa: E402


async def run(manifest: Path, limit: int) -> int:
    _load_dotenv(str(ROOT / ".env"))
    config = Config.load(str(ROOT / "config" / "bot.yaml"))
    client = DeepSeekClient(config.llm)
    items = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        print("manifest 顶层应该是数组")
        return 2
    items = items[: max(1, limit)]
    passed = 0
    labeled = 0
    started = time.monotonic()
    for index, item in enumerate(items, 1):
        expected = [str(x) for x in item.get("expect") or [] if str(x).strip()]
        if not expected:
            print(f"{index:02d} SKIP 未标注（先填 expect 关键词）{item.get('path')}")
            continue
        labeled += 1
        path = Path(str(item.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            print(f"{index:02d} SKIP 文件不存在 {path}")
            continue
        data = path.read_bytes()
        card = await analyze_image(
            client, data, guess_mime(data, str(path)), scope_key="eval"
        )
        text = ""
        if card:
            text = " ".join(
                [card.summary, card.ocr, card.meme, card.emotion, *card.reply_angles]
            )
        ok = bool(text) and any(word in text for word in expected)
        if ok:
            passed += 1
        print(
            f"{index:02d} {'PASS' if ok else 'FAIL'} "
            f"{path.name} -> {(card.summary if card else '（空）')[:60]}"
        )
    elapsed = time.monotonic() - started
    if not labeled:
        print("\n没有已标注的条目：先用 make_vision_eval.py 生成模板并填 expect。")
        return 2
    print(f"\n通过 {passed}/{labeled}（另有 {len(items) - labeled} 条未标注），耗时 {elapsed:.1f}s")
    return 0 if passed >= max(1, int(labeled * 0.8)) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "scripts" / "vision-eval.example.json"),
    )
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    return asyncio.run(run(Path(args.manifest), args.limit))


if __name__ == "__main__":
    sys.exit(main())

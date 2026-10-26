"""零手工生成识图评测集：用素材清单里的表情名/梗图台词当关键词。

    python scripts/build_vision_eval.py
    python scripts/eval_vision.py --manifest data/vision-eval.json

不需要你标注，也不需要额外图片：直接用仓库自带的 19 张表情 + 12 张梗图。
它衡量的是「图片卡有没有抓到表情名/台词」，适合做提示词改动的回归基线；
想测截图/照片可以再往 manifest 里加。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "vision-eval.json"


def _keywords(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= 8:
        return [text]
    # 长台词取前 6 个字 + 中间一小段，命中任意一个就算通过
    middle = max(0, len(text) // 2 - 2)
    return [text[:6], text[middle : middle + 4]]


def main() -> int:
    items: list[dict] = []
    emoji_manifest = ROOT / "assets" / "emoji" / "manifest.json"
    if emoji_manifest.is_file():
        raw = json.loads(emoji_manifest.read_text(encoding="utf-8"))
        for item in raw.get("items") or []:
            name = str(item.get("name") or "").strip()
            file = str(item.get("file") or "").strip()
            if name and file:
                items.append(
                    {
                        "path": file,
                        "expect": [name],
                        "kind": "emoji",
                        "note": "素材清单自动生成",
                    }
                )
    meme_manifest = ROOT / "assets" / "memes" / "manifest.json"
    if meme_manifest.is_file():
        raw = json.loads(meme_manifest.read_text(encoding="utf-8"))
        for item in raw.get("items") or []:
            caption = str(item.get("caption") or "").strip()
            file = str(item.get("file") or "").strip()
            if caption and file:
                items.append(
                    {
                        "path": f"assets/memes/{file}",
                        "expect": _keywords(caption),
                        "kind": "meme",
                        "note": f"素材台词：{caption}",
                    }
                )
    items = items[:30]
    if not items:
        print("没有找到素材清单")
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {len(items)} 条自动标注：{OUT}")
    print(f"直接跑：python scripts/eval_vision.py --manifest {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

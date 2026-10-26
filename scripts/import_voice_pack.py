"""按需导入第三方语音包（不随仓库分发）。

为什么要单独导入？
- aceice01/dsh-whale-pet 的 49 句「晓伊」语音是 **Non-Commercial Use License**，
  不是 MIT。把它直接打进本项目会让下游误以为可以商用，所以这里做成"你自己下载"。
- 生成的音频放在 data/ 下面，已经被 .gitignore 忽略，不会进仓库。

用法：
    python scripts/import_voice_pack.py --source aceice --accept-license
    # 然后 config/bot.yaml:
    # tools:
    #   voice_pack: "data/voice-packs/aceice"

只使用 Python 标准库（urllib + base64），不需要 pip install。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "voice-packs" / "aceice"

SOURCES = {
    "aceice": {
        "name": "鲸鱼娘桌宠语音包 · 晓伊 49 句",
        "url": "https://raw.githubusercontent.com/aceice01/dsh-whale-pet/18fec904ec3b4fd3958b6fb8c2fa9c209679328f/lib/audio.json",
        "homepage": "https://github.com/aceice01/dsh-whale-pet",
        "license": "Non-Commercial Use License（非商业，需保留署名）",
        "license_url": "https://github.com/aceice01/dsh-whale-pet/blob/main/LICENSE",
        "note": "音频以 base64 内嵌在上游 lib/audio.json；本脚本只在本机解码，不重新分发。",
    }
}

# 上游 key 前缀 -> 语音包里的 kind。kind 越细，/语音 <kind> 越好用。
KIND_PREFIXES = [
    ("coquetry", "coquetry"),
    ("running", "running"),
    ("celebrate", "celebrate"),
    ("sad", "sad"),
    ("todo", "todo"),
    ("approval", "approval"),
    ("welcome", "greet"),
]


# 这几句本来就是摸头/抱抱/贴贴，归到 pat，戳一戳时也能抽到。
PAT_KEYS = {"coquetry0", "coquetry3", "coquetry11", "coquetry13"}


def kind_for(key: str) -> str:
    if key in PAT_KEYS:
        return "pat"
    for prefix, kind in KIND_PREFIXES:
        if key.startswith(prefix):
            return kind
    return "idle"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "ricefish-voice-import"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def import_aceice(out_dir: Path) -> int:
    source = SOURCES["aceice"]
    print(f"下载 {source['url']}")
    data = fetch_json(source["url"])
    if not isinstance(data, dict) or not data:
        print("上游格式不对：期望一个 key -> {b64,text} 的对象")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, (key, item) in enumerate(data.items()):
        if not isinstance(item, dict):
            continue
        raw = item.get("b64") or item.get("base64") or ""
        if not raw:
            continue
        try:
            payload = base64.b64decode(raw)
        except (ValueError, TypeError) as exc:
            print(f"跳过 {key}：base64 解码失败（{exc}）")
            continue
        if not payload:
            continue
        # 上游是 MP3；用 key 当文件名，避免中文路径
        filename = f"{key}.mp3"
        (out_dir / filename).write_bytes(payload)
        lines.append(
            {
                "id": index,
                "kind": kind_for(key),
                "text": str(item.get("text") or "").strip(),
                "file": filename,
            }
        )
        print(f"  {filename:<18} {len(payload):>7} bytes  {item.get('text', '')}")

    if not lines:
        print("没有导出任何音频，检查上游是否改了格式。")
        return 1

    manifest = {
        "name": source["name"],
        "license": source["license"],
        "license_url": source["license_url"],
        "source": source["homepage"],
        "note": source["note"],
        "lines": lines,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "LICENSE.txt").write_text(
        "本目录音频来自 aceice01/dsh-whale-pet，采用 Non-Commercial Use License。\n"
        f"许可全文：{source['license_url']}\n"
        f"项目主页：{source['homepage']}\n\n"
        "仅允许个人学习、研究、娱乐与非商业分发；请保留本说明与署名。\n",
        encoding="utf-8",
    )
    print(f"\n完成：{len(lines)} 句 -> {out_dir}")
    print("把 config/bot.yaml 的 tools.voice_pack 改成：")
    print(f'  voice_pack: "{out_dir.relative_to(ROOT).as_posix()}"')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按需导入第三方语音包（不会进 git）")
    parser.add_argument("--source", choices=sorted(SOURCES), default="aceice")
    parser.add_argument("--out", default="", help="输出目录，默认 data/voice-packs/<source>")
    parser.add_argument(
        "--accept-license",
        action="store_true",
        help="确认已阅读并接受上游许可（aceice 为非商业许可）",
    )
    args = parser.parse_args()

    source = SOURCES[args.source]
    if not args.accept_license:
        print(f"上游：{source['homepage']}")
        print(f"许可：{source['license']}")
        print(f"全文：{source['license_url']}")
        print("\n这是第三方资源，不是本项目 MIT 的一部分。")
        print("确认接受后，加 --accept-license 再跑一次。")
        return 2

    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    if args.source == "aceice":
        return import_aceice(out_dir)
    return 1


if __name__ == "__main__":
    sys.exit(main())

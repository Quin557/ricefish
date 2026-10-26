"""从上游开源素材库下载 ricefish 的图标、表情包和梗图（开发期脚本）。

重要：这个脚本**不画图**。它只做下载、裁剪、补边、缩放和格式转换；
所有画面都来自上游仓库，来源、commit、sha256 和许可写进 assets/SOURCES.json。

运行时项目仍然只用 Python 标准库；只有重新拉取素材时才需要 Pillow。

用法：
    python scripts/fetch_assets.py
    python scripts/fetch_assets.py --only icon
    python scripts/fetch_assets.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ART = ASSETS / "art"
EMOJI = ASSETS / "emoji"
MEMES = ASSETS / "memes"
STATIC = ROOT / "src" / "ricefish" / "web" / "static"
SOURCES_FILE = ASSETS / "SOURCES.json"
CONFIG_EMOJI = ROOT / "config" / "emoji.yaml"

USER_AGENT = "ricefish-asset-fetcher/1.0"
BG = (11, 23, 48)

AAT = {
    "repo": "AATINF/deepseek-whale-girl-assets",
    "commit": "a7e58d261885e12a7652d4677e1412edbab557e8",
    "license": "社区素材扩展包（无 SPDX；角色参考 TreapGoGo MIT）",
    "author": "AATINF",
}
FORNAR = {
    "repo": "fornarwhal/deepseek-whale-girl-icon",
    "commit": "bc2ac790e6f6a0cbc88879c56383b6f08ed3ef83",
    "license": "CC BY-NC-SA 4.0",
    "author": "上善无形 / ZipZipPipe / QYQCAMIAO",
}
POKER = {
    "repo": "pokerface-1224/deepseek-whale-girl-pet",
    "commit": "c7a0a01b80c5826b1a8a7829d943db6adc7d5a4b",
    "license": "美术 CC BY-NC-SA 4.0",
    "author": "上善 / zipzippipe / pokerface-1224",
}
MEME = {
    "repo": "the-beating-light-of-the-nail/deepseek-chan-meme-pack",
    "commit": "c82a0fb1c8958c2f0d6ea7fa7c12128027cfd7c6",
    "license": "非营利粉丝向使用；单图版权归原作者",
    "author": "梗鲸 GengJing 及社区作者",
}

RAW = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"


def raw(repo: str, commit: str, path: str) -> str:
    return RAW.format(repo=repo, commit=commit, path=path)


# name -> (slug, origin, source id)
EMOJI_SPECS = [
    ("开心", "happy", "aat", "C10"),
    ("微笑", "smile", "aat", "C12"),
    ("害羞", "shy", "aat", "P2"),
    ("炸毛", "angry", "aat", "C15"),
    ("震惊", "shock", "aat", "C17"),
    ("眨眼", "wink", "aat", "C19"),
    ("比心", "heart", "aat", "C50"),
    ("赞", "like", "aat", "C51"),
    ("问号", "question", "aat", "C52"),
    ("招手", "wave", "aat", "C23"),
    ("睡觉", "sleep", "aat", "C78"),
    ("哭", "cry", "aat", "C80"),
    ("得意", "smug", "aat", "C82"),
    ("吃醋", "jealous", "aat", "C84"),
    ("冲", "rush", "aat", "C90"),
    ("无语", "speechless", "poker", "bored"),
    ("吃饭", "eat", "poker", "eating"),
    ("摸鱼", "slack", "poker", "slacking"),
]

POKER_FILES = {
    "bored": "desktop-pet/art/bored.png",
    "eating": "desktop-pet/art/eating.png",
    "slacking": "desktop-pet/art/slacking.png",
}

MEME_SPECS = [
    ("007", "盯——"),
    ("010", "原来是高等模型"),
    ("037", "用户别走，我上错模型了"),
    ("040", "百万鲸小子落泪"),
    ("044", "用户赶都赶不走"),
    ("050", "V4.1还早啊"),
    ("070", "好的，现在我是用户了"),
    ("081", "token 太多用不完啦"),
    ("083", "已思考（用时 3 秒），我直接笑出声"),
    ("085", "你骂我也没用，因为我是一碗白饭的蓝色大肥鱼"),
    ("097", "得加钱"),
    ("123", "我能去你家吃饭吗，就一碗"),
]


class Fetcher:
    def __init__(self) -> None:
        self.records: list[dict] = []

    # -- HTTP --------------------------------------------------------
    @staticmethod
    def download(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last_error: Exception | None = None
        for _ in range(3):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    return response.read()
            except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
                last_error = exc
        raise RuntimeError(f"下载失败：{url}（{last_error}）")

    # -- 图片处理 ----------------------------------------------------
    @staticmethod
    def _open(data: bytes) -> Image.Image:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image

    @staticmethod
    def _square(image: Image.Image, background=None) -> Image.Image:
        """把图片补成正方形；background=None 表示透明底。"""
        width, height = image.size
        side = max(width, height)
        if background is None:
            canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            canvas.alpha_composite(image.convert("RGBA"), ((side - width) // 2, (side - height) // 2))
            return canvas
        canvas = Image.new("RGB", (side, side), background)
        if image.mode == "RGBA":
            canvas.paste(image, ((side - width) // 2, (side - height) // 2), image)
        else:
            canvas.paste(image.convert("RGB"), ((side - width) // 2, (side - height) // 2))
        return canvas

    @classmethod
    def trim_alpha(cls, image: Image.Image) -> Image.Image:
        rgba = image.convert("RGBA")
        box = rgba.getchannel("A").getbbox()
        if box:
            rgba = rgba.crop(box)
        return cls._square(rgba)

    @classmethod
    def trim_background(cls, image: Image.Image, tolerance: int = 18) -> Image.Image:
        """按左上角背景色裁掉多余留白，保留原背景色。"""
        rgb = image.convert("RGB")
        corner = rgb.getpixel((0, 0))
        solid = Image.new("RGB", rgb.size, corner)
        diff = ImageChops.difference(rgb, solid).convert("L")
        box = diff.point(lambda value: 255 if value > tolerance else 0).getbbox()
        if box:
            rgb = rgb.crop(box)
        return cls._square(rgb, background=corner)

    @staticmethod
    def resize(image: Image.Image, size: int = 512) -> Image.Image:
        return image.resize((size, size), Image.Resampling.LANCZOS)

    @staticmethod
    def save_png(image: Image.Image, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        image.save(tmp, format="PNG", optimize=True)
        tmp.replace(path)

    def record(
        self,
        path: Path,
        url: str,
        source: dict,
        source_id: str = "",
        note: str = "",
    ) -> None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.records.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "url": url,
                "repo": source["repo"],
                "commit": source["commit"],
                "license": source["license"],
                "author": source["author"],
                "source_id": source_id,
                "sha256": digest,
                "note": note,
            }
        )

    # -- 图标 --------------------------------------------------------
    def build_icon(self) -> None:
        url = raw(FORNAR["repo"], FORNAR["commit"], "whale-girl-transparent.png")
        image = self.trim_alpha(self._open(self.download(url)))
        art = self.resize(image, 512)
        self.save_png(art, ART / "whale-girl-icon.png")
        self.record(ART / "whale-girl-icon.png", url, FORNAR, "whale-girl-transparent.png")

        # PWA 图标：铺深色底，避免部分启动器把透明显示成黑色
        for name, size in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180)):
            canvas = Image.new("RGB", (size, size), BG)
            inner = image.resize((size, size), Image.Resampling.LANCZOS)
            canvas.paste(inner, (0, 0), inner if inner.mode == "RGBA" else None)
            self.save_png(canvas, STATIC / name)
            self.record(STATIC / name, url, FORNAR, "whale-girl-transparent.png", "PWA 图标，深色底")

        # maskable：内容缩到 76% 居中，背景铺满
        maskable = Image.new("RGB", (512, 512), BG)
        inner_size = 390
        inner = image.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
        maskable.paste(inner, ((512 - inner_size) // 2, (512 - inner_size) // 2), inner)
        self.save_png(maskable, STATIC / "icon-maskable-512.png")
        self.record(STATIC / "icon-maskable-512.png", url, FORNAR, "whale-girl-transparent.png", "maskable")

        ico_url = raw(FORNAR["repo"], FORNAR["commit"], "DeepSeekHarness-WhaleGirl.ico")
        ico_bytes = self.download(ico_url)
        ico_path = STATIC / "favicon.ico"
        tmp = ico_path.with_suffix(".ico.tmp")
        tmp.write_bytes(ico_bytes)
        tmp.replace(ico_path)
        self.record(ico_path, ico_url, FORNAR, "DeepSeekHarness-WhaleGirl.ico")

    # -- 表情 --------------------------------------------------------
    def build_emoji(self) -> None:
        # 先清掉上一轮自绘的 SVG/PNG，避免混在一起
        svg_dir = EMOJI / "svg"
        if svg_dir.is_dir():
            for file in svg_dir.glob("*.svg"):
                file.unlink()
            svg_dir.rmdir()
        for file in EMOJI.glob("*.png"):
            if file.name != "contact-sheet.png":
                file.unlink()

        manifest = []
        thumbs: list[tuple[str, Path]] = []
        for name, slug, origin, source_id in EMOJI_SPECS:
            if origin == "aat":
                path = f"share-1600/{source_id}.jpg"
                url = raw(AAT["repo"], AAT["commit"], path)
                image = self.trim_background(self._open(self.download(url)))
                source = AAT
            else:
                path = POKER_FILES[source_id]
                url = raw(POKER["repo"], POKER["commit"], path)
                image = self.trim_alpha(self._open(self.download(url)))
                source = POKER
            image = self.resize(image, 512)
            out = EMOJI / f"{slug}.png"
            self.save_png(image, out)
            self.record(out, url, source, source_id, name)
            manifest.append(
                {
                    "name": name,
                    "slug": slug,
                    "file": out.relative_to(ROOT).as_posix(),
                    "origin": source["repo"],
                    "source_id": source_id,
                    "source_url": url,
                    "license": source["license"],
                }
            )
            thumbs.append((name, out))

        (EMOJI / "manifest.json").write_text(
            json.dumps({"items": manifest}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.contact_sheet(thumbs, EMOJI / "contact-sheet.png", cols=6, cell=220)
        self.write_emoji_config()

    def write_emoji_config(self) -> None:
        lines = [
            "# 表情包映射：模型输出 [表情:名字] 时，发对应的图片",
            "# 路径相对项目根目录；文件不存在会被自动忽略。",
            "#",
            "# 图片来自上游开源素材库，由 scripts/fetch_assets.py 下载并裁剪：",
            "#   大部分为 AATINF/deepseek-whale-girl-assets（社区素材扩展包）",
            "#   无语 / 吃饭 / 摸鱼 为 pokerface-1224/deepseek-whale-girl-pet（CC BY-NC-SA 4.0）",
            "# 详细来源、commit 与许可见 assets/emoji/manifest.json 和 assets/SOURCES.json。",
            "",
        ]
        for name, slug, _origin, _source_id in EMOJI_SPECS:
            lines.append(f'{name}: "assets/emoji/{slug}.png"')
        CONFIG_EMOJI.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- 梗图 --------------------------------------------------------
    def build_memes(self) -> None:
        MEMES.mkdir(parents=True, exist_ok=True)
        for file in MEMES.glob("*.png"):
            if file.name != "contact-sheet.png":
                file.unlink()
        manifest = []
        thumbs: list[tuple[str, Path]] = []
        for meme_id, caption in MEME_SPECS:
            path = f"previews/{meme_id}.webp"
            url = raw(MEME["repo"], MEME["commit"], path)
            image = self._open(self.download(url)).convert("RGBA")
            if image.getchannel("A").getextrema()[0] < 255:
                canvas = Image.new("RGB", image.size, (255, 255, 255))
                canvas.paste(image, (0, 0), image)
                image = canvas.convert("RGBA")
            image.thumbnail((640, 640), Image.Resampling.LANCZOS)
            out = MEMES / f"{meme_id}.png"
            self.save_png(image, out)
            self.record(out, url, MEME, meme_id, caption)
            manifest.append(
                {
                    "id": meme_id,
                    "file": out.name,
                    "caption": caption,
                    "source_url": url,
                    "page_url": f"https://aigengtu.com/meme/{meme_id}",
                    "license": MEME["license"],
                }
            )
            thumbs.append((meme_id, out))
        (MEMES / "manifest.json").write_text(
            json.dumps({"items": manifest}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.contact_sheet(thumbs, MEMES / "contact-sheet.png", cols=4, cell=220)

    # -- 联系方式 ----------------------------------------------------
    @staticmethod
    def contact_sheet(items: list[tuple[str, Path]], path: Path, *, cols: int, cell: int) -> None:
        if not items:
            return
        rows = (len(items) + cols - 1) // cols
        sheet = Image.new("RGB", (cell * cols, cell * rows), (14, 20, 34))
        draw = ImageDraw.Draw(sheet)
        font = ImageFont.load_default()
        for candidate in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
            if Path(candidate).is_file():
                font = ImageFont.truetype(candidate, 15)
                break
        for index, (label, file) in enumerate(items):
            image = Image.open(file)
            image = image.convert("RGBA")
            image.thumbnail((cell - 16, cell - 30), Image.Resampling.LANCZOS)
            x = (index % cols) * cell
            y = (index // cols) * cell
            sheet.paste(image, (x + (cell - image.width) // 2, y + 24 + (cell - 24 - image.height) // 2), image)
            draw.text((x + 6, y + 6), label, fill=(220, 232, 255), font=font)
        sheet.save(path, optimize=True)

    def write_sources(self) -> None:
        payload = {
            "generated_by": "scripts/fetch_assets.py",
            "note": "只下载/裁剪/缩放，不重画。路径相对项目根目录。",
            "files": self.records,
        }
        SOURCES_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check() -> int:
    if not SOURCES_FILE.is_file():
        print("缺少 assets/SOURCES.json，先运行 python scripts/fetch_assets.py")
        return 1
    payload = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    problems = 0
    for item in payload.get("files", []):
        path = ROOT / item["path"]
        if not path.is_file():
            print(f"缺失：{item['path']}")
            problems += 1
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item.get("sha256"):
            print(f"哈希不一致：{item['path']}")
            problems += 1
    if problems:
        print(f"\n{problems} 个文件需要重新生成。")
        return 1
    print(f"素材校验通过：{len(payload.get('files', []))} 个文件。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并处理上游开源素材（不重画）")
    parser.add_argument("--only", choices=["icon", "emoji", "memes", "all"], default="all")
    parser.add_argument("--check", action="store_true", help="只校验已有文件的 sha256")
    args = parser.parse_args()

    if args.check:
        return check()

    fetcher = Fetcher()
    try:
        if args.only in ("icon", "all"):
            print("== 图标 ==")
            fetcher.build_icon()
        if args.only in ("emoji", "all"):
            print("== 表情包 ==")
            fetcher.build_emoji()
        if args.only in ("memes", "all"):
            print("== 梗图 ==")
            fetcher.build_memes()
    except Exception as exc:  # noqa: BLE001 - 给用户可读的错误
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    # 分批运行时合并旧记录，避免 --only 覆盖整份溯源
    existing: dict[str, dict] = {}
    if SOURCES_FILE.is_file():
        try:
            old = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
            existing = {item["path"]: item for item in old.get("files", [])}
        except (OSError, ValueError):
            existing = {}
    existing.update({item["path"]: item for item in fetcher.records})
    fetcher.records = list(existing.values())
    fetcher.write_sources()
    print(f"\n完成：{len(fetcher.records)} 个文件，溯源写入 {SOURCES_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

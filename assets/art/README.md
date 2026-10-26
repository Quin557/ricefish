# 图标 · 来自上游，不重画

- `whale-girl-icon.png`：512×512，透明底，来自 fornarwhal/deepseek-whale-girl-icon
  的 `whale-girl-transparent.png`，只做了裁透明边、补方、缩放。
- `src/ricefish/web/static/` 下：
  - `icon-192.png` `icon-512.png`：PWA 图标，深色底 `#0b1730`
  - `icon-maskable-512.png`：Android maskable，内容缩到约 76%
  - `apple-touch-icon.png`：iOS 180×180
  - `favicon.ico`：直接使用上游 `DeepSeekHarness-WhaleGirl.ico`
- 本项目不再提供 `icon.svg`。

## 来源与许可

- 上游：https://github.com/fornarwhal/deepseek-whale-girl-icon
- 固定 commit：`bc2ac790e6f6a0cbc88879c56383b6f08ed3ef83`
- 许可：**CC BY-NC-SA 4.0**，见 [`LICENSE-CC-BY-NC-SA-4.0.txt`](LICENSE-CC-BY-NC-SA-4.0.txt)
- 署名：上善无形（角色）、ZipZipPipe（二创）、QYQCAMIAO（修复）、fornarwhal（图标仓）
- 逐文件 sha256 与 URL：`../SOURCES.json`
- 重新拉取：`python scripts/fetch_assets.py --only icon`（需要 Pillow，仅开发期）

# 第三方资源与引用说明

ricefish 的**代码**是 MIT。**图片和语音素材不是同一份许可**，每个目录单独说明。
本项目不是 DeepSeek 官方项目，也不代表任何上游项目的立场。

所有图片都由 [`scripts/fetch_assets.py`](../scripts/fetch_assets.py) 从固定 commit 下载，
只做裁剪、补边、缩放和格式转换，不重画。每个文件的 URL、commit、sha256 和许可都在
[`SOURCES.json`](SOURCES.json)。

## 一眼看懂

| 资源 | 用在哪 | 我们做了什么 | 许可 / 状态 |
|---|---|---|---|
| [fornarwhal/deepseek-whale-girl-icon](https://github.com/fornarwhal/deepseek-whale-girl-icon) | `assets/art/`、PWA 图标、favicon | 用上游透明 PNG 与 ICO，只裁剪/缩放/补边 | **CC BY-NC-SA 4.0** |
| [AATINF/deepseek-whale-girl-assets](https://github.com/AATINF/deepseek-whale-girl-assets) | 15 张表情（开心、害羞、炸毛……） | 下载 `share-1600`，裁留白、补方、缩到 512 | 社区素材扩展包；**无 SPDX** |
| [pokerface-1224/deepseek-whale-girl-pet](https://github.com/pokerface-1224/deepseek-whale-girl-pet) | 无语 / 吃饭 / 摸鱼 | 下载透明立绘，裁 alpha 边、缩放 | 代码 MIT；**美术 CC BY-NC-SA 4.0** |
| [梗鲸 aigengtu.com](https://aigengtu.com) / [镜像仓](https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack) | `assets/memes/` 12 张梗图 | 下载 `previews`，转 PNG，保留原台词 | **非营利粉丝向使用**；单图版权归原作者 |
| [dleaf6211-hash/dsh-whale-pet](https://github.com/dleaf6211-hash/dsh-whale-pet) | `assets/voices/` 19 句语音 | 原样引入 WAV，改 manifest 路径 | **MIT**，保留 LICENSE |
| [aceice01/dsh-whale-pet](https://github.com/aceice01/dsh-whale-pet) | 可选 49 句「晓伊」语音 | 不随仓库复制；导入到 `data/voice-packs/aceice/` | **非商业许可（NOASSERTION）** |
| [YEJASONJIEXIN/dsh-whale-girl-pet](https://github.com/YEJASONJIEXIN/dsh-whale-girl-pet) | 「16 角色音色」思路参考 | 不复制；上游运行时用 Edge TTS 合成 | MIT |
| [鲸鱼娘开源资产库](https://treapgogo.github.io/deepseek-whale-girl/) | 人设文字参考 | 只参考公开文字描述；**未使用画廊图片** | 代码 MIT；画廊单图归作者 |

## 图标

- 上游：https://github.com/fornarwhal/deepseek-whale-girl-icon
- 固定 commit：`bc2ac790e6f6a0cbc88879c56383b6f08ed3ef83`
- 使用文件：`whale-girl-transparent.png`、`DeepSeekHarness-WhaleGirl.ico`
- 署名：角色形象来源 **上善无形**（原创 OC「溟月」）；DeepSeek 元素二创 **ZipZipPipe**；
  改进版修复 **QYQCAMIAO**。
- 许可：**CC BY-NC-SA 4.0**，全文见 [`art/LICENSE-CC-BY-NC-SA-4.0.txt`](art/LICENSE-CC-BY-NC-SA-4.0.txt)。
- 上游 README 说明图片来自网络流传，具体作者未完全确认；如原作者认为不妥，请联系删除。

## 表情包

### AATINF 扩展包（15 张）

- 上游：https://github.com/AATINF/deepseek-whale-girl-assets
- 固定 commit：`a7e58d261885e12a7652d4677e1412edbab557e8`
- 使用文件：`share-1600/C10.jpg`、`C12`、`P2`、`C15`、`C17`、`C19`、`C50`、`C51`、
  `C52`、`C23`、`C78`、`C80`、`C82`、`C84`、`C90`
- 说明：该仓库是社区二创素材扩展包，由 `wan2.7-image` 图生图生成，README 写明
  「供社区二创与细节开发使用」；角色参考 TreapGoGo/deepseek-whale-girl（MIT）。
- **许可注意**：仓库没有挂 SPDX 许可证文件。本项目按社区共享素材使用并完整署名；
  如果你要商用或按 MIT 再分发，请先联系上游确认，或换用严格许可的素材。
- 逐图来源见 [`emoji/manifest.json`](emoji/manifest.json) 和 [`SOURCES.json`](SOURCES.json)。

### pokerface 立绘（3 张）

- 上游：https://github.com/pokerface-1224/deepseek-whale-girl-pet
- 固定 commit：`c7a0a01b80c5826b1a8a7829d943db6adc7d5a4b`
- 使用文件：`desktop-pet/art/bored.png`、`eating.png`、`slacking.png`
- 署名：角色原作者 **上善**；二次设计 / 立绘 **zipzippipe**；项目维护 **pokerface-1224**。
- 许可：**CC BY-NC-SA 4.0**，全文见 [`emoji/LICENSE-POKERFACE.txt`](emoji/LICENSE-POKERFACE.txt)。

## 梗图

- 上游：https://github.com/the-beating-light-of-the-nail/deepseek-chan-meme-pack
- 固定 commit：`c82a0fb1c8958c2f0d6ea7fa7c12128027cfd7c6`
- 使用文件：`previews/` 下 12 张（`007 010 037 040 044 050 070 081 083 085 097 123`）
- 说明：梗鲸是社区表情包档案馆，仓库 README 写明「非营利粉丝向玩梗可以随意使用」；
  角色形象与原始画作版权归原作者及社区作者。
- **不要商用**。单图作者未逐张确认，若你是权利人请走梗鲸的侵权举报入口。
- 逐张说明见 [`memes/manifest.json`](memes/manifest.json)。

## 语音包

`assets/voices/` 下 19 个 WAV 来自 **dleaf6211-hash/dsh-whale-pet** 的
`assets/voices/`，按 MIT 原样引入。完整 MIT 文本保存在
[`voices/LICENSE`](voices/LICENSE)，版权声明：

```
Copyright (c) 2026 dleaf6211-hash
```

49 句 aceice01 语音是 **Non-Commercial Use License**，不随仓库分发；运行
`python scripts/import_voice_pack.py --source aceice --accept-license` 后解码到
`data/voice-packs/aceice/`（gitignore），只在本机使用。不要把 `data/` 里的音频再打包分发。

## 鲸鱼娘开源资产库

- 站点：https://treapgogo.github.io/deepseek-whale-girl/
- 仓库：https://github.com/TreapGoGo/deepseek-whale-girl
- 用途：`config/personas/global.md` 的外观描述、`scripts/fetch_assets.py` 的素材线索。
- 仓库代码 MIT；画廊作品版权归各作者，本项目**未使用画廊图片**。

## 许可边界

- **代码**：MIT（根目录 `LICENSE`）。
- **图标 / pokerface 表情**：CC BY-NC-SA 4.0，禁止商用，二创需同协议分享并署名。
- **AATINF 表情**：社区共享素材，无 SPDX；按署名使用，商用前先确认。
- **梗图**：非营利粉丝向使用；版权归原作者。
- **语音**：19 句 MIT；49 句非商业，仅本机。

如果你要再分发，请保留本文件、`ASSETS-LICENSE.md` 以及各素材目录下的 LICENSE。

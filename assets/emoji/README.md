# 表情包 · 来自上游，不重画

18 张 512×512 PNG，名字和 `config/emoji.yaml` 一一对应：

| 名字 | 文件 | 来源 |
|---|---|---|
| 开心 | `happy.png` | AATINF C10 |
| 微笑 | `smile.png` | AATINF C12 |
| 害羞 | `shy.png` | AATINF P2 |
| 炸毛 | `angry.png` | AATINF C15 |
| 震惊 | `shock.png` | AATINF C17 |
| 眨眼 | `wink.png` | AATINF C19 |
| 比心 | `heart.png` | AATINF C50 |
| 赞 | `like.png` | AATINF C51 |
| 问号 | `question.png` | AATINF C52 |
| 招手 | `wave.png` | AATINF C23 |
| 睡觉 | `sleep.png` | AATINF C78 |
| 哭 | `cry.png` | AATINF C80 |
| 得意 | `smug.png` | AATINF C82 |
| 吃醋 | `jealous.png` | AATINF C84 |
| 冲 | `rush.png` | AATINF C90 |
| 无语 | `speechless.png` | pokerface `bored.png` |
| 吃饭 | `eat.png` | pokerface `eating.png` |
| 摸鱼 | `slack.png` | pokerface `slacking.png` |

- `contact-sheet.png` 是 18 张的总览图。
- `manifest.json` 记录每张的 `origin / source_id / source_url / license`。
- 没有 `svg/`，也不在本地重画；处理只做裁剪、补方、缩放。

## 来源与许可

- AATINF：https://github.com/AATINF/deepseek-whale-girl-assets
  - 固定 commit `a7e58d261885e12a7652d4677e1412edbab557e8`
  - 社区素材扩展包，无 SPDX；角色参考 TreapGoGo MIT
  - 见 [`LICENSE-AATINF.md`](LICENSE-AATINF.md)
- pokerface：https://github.com/pokerface-1224/deepseek-whale-girl-pet
  - 固定 commit `c7a0a01b80c5826b1a8a7829d943db6adc7d5a4b`
  - 美术 CC BY-NC-SA 4.0
  - 见 [`LICENSE-POKERFACE.txt`](LICENSE-POKERFACE.txt)
- 总说明：`../ASSETS-LICENSE.md`、`../NOTICE.md`
- 重新拉取：`python scripts/fetch_assets.py --only emoji`（需要 Pillow，仅开发期）

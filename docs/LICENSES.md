# 素材与许可

代码：MIT（见 `LICENSE`）。

仓库里的图片/语音**不是统一 MIT**，逐项来源和 sha256 见 `assets/SOURCES.json`；
每个子目录也各带 README/LICENSE。

| 目录 | 内容 | 许可/注意 |
|---|---|---|
| `assets/emoji/` | 19 张社区表情 | AATINF/deepseek-whale-girl-assets 社区素材扩展包（无 SPDX，角色参考 TreapGoGo MIT）；`无语/困饿` 来自 pokerface-1224，**CC BY-NC-SA 4.0** |
| `assets/memes/` | 12 张梗鲸梗图 | 非营利粉丝向使用；单图版权归原作者 |
| `assets/voices/` | 19 句预合成语音 | MIT（dleaf6211-hash/dsh-whale-pet），见 `assets/voices/LICENSE` |
| `assets/art/` | 图标 | CC BY-NC-SA 4.0（见目录内 LICENSE） |
| `data/emoji-inbox/` | 群友发过的图 | 本地保存，默认不随项目分发；开源发行不要打包 |

## 开源/分发前检查

- [ ] 默认发行版只带许可清晰的素材；NC 素材单独放或默认不启用。
- [ ] `assets/SOURCES.json` 与实际文件一致，sha256 可校验。
- [ ] README/发布页写清“素材许可见 assets/…”，不要把整个仓库简单标成 MIT。
- [ ] 用户收集的表情包属于用户，不进入 release / 示例数据。
- [ ] 商用/二次分发前逐项确认，尤其是 CC BY-NC-SA 和“非营利粉丝向”素材。

## 替换素材

想完全 MIT 发行：

1. 清空 `assets/emoji`、`assets/memes`、`assets/art` 的非 MIT 文件；
2. 保留 `config/emoji.yaml` 中仍存在的映射；
3. 用 `scripts/fetch_assets.py` 换来源，或放自己的图；
4. 更新 `assets/SOURCES.json` 和本文件。

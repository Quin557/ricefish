# 素材许可总说明

ricefish 的**代码**按根目录 `LICENSE`（MIT）发布。`assets/` 下的图片和语音**不是 MIT**，
它们各自遵循上游许可。把它们当成“代码的一部分”再按 MIT 分发是不对的。

## 目录对照

| 目录 | 内容 | 许可 | 能否商用 |
|---|---|---|---|
| `assets/art/` | 图标、PWA PNG、favicon | CC BY-NC-SA 4.0 | ❌ 不可 |
| `assets/emoji/` | 18 张表情（AATINF + pokerface） | 混合：社区共享 + CC BY-NC-SA 4.0 | ❌ 不建议 |
| `assets/memes/` | 12 张梗鲸梗图 | 非营利粉丝向使用 | ❌ 不可 |
| `assets/voices/` | 19 句 dleaf 语音 | MIT | ✅ 可以（保留声明） |
| `data/voice-packs/aceice/` | 49 句 aceice 语音（本机） | 非商业许可 | ❌ 不可 |

## 署名

- 角色形象：**上善无形**（原创 OC「溟月」）
- DeepSeek 元素二创：**ZipZipPipe**
- 图标改进：**QYQCAMIAO**
- 表情扩展包：**AATINF**
- 桌宠立绘：**上善 / zipzippipe / pokerface-1224**
- 梗图：**梗鲸 GengJing 及社区作者**
- 语音：**dleaf6211-hash**（19 句，MIT）、**aceice01**（49 句，非商业）

## 重要边界

1. **不要商用**：图标、pokerface 立绘、梗图、aceice 语音都带有非商业限制。
2. **CC BY-NC-SA 4.0 的 SA**：如果你修改并公开发布图标或 pokerface 立绘，
   需要以相同协议分享，并保留署名。
3. **AATINF 没有 SPDX**：本项目按上游 README 的社区共享意图使用并署名；
   商用或严格开源再分发前，请先向上游确认。
4. **梗图单图版权归原作者**：如果某张图的权利人要求下架，请删除对应文件并更新
   `assets/memes/manifest.json`。
5. **代码 MIT 不受影响**：你仍然可以单独使用、修改、商用本项目代码；
   但不能把 `assets/` 里的受限素材一起按 MIT 授权。

逐文件来源、commit、sha256 见 [`SOURCES.json`](SOURCES.json)；详细致谢见
[`NOTICE.md`](NOTICE.md)。

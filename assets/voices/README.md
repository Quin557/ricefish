# 语音包 · whale pet voice pack

这 19 条 `line-00.wav` ~ `line-18.wav` 是 **dleaf6211-hash/dsh-whale-pet** 的预合成语音，
按 MIT License 原样引入，未做音频改动，只把 `manifest.json` 里的路径改成相对本目录。

| 项目 | 内容 |
|---|---|
| 上游仓库 | https://github.com/dleaf6211-hash/dsh-whale-pet |
| 固定 commit | `97b734fff7f5a0c2aba7ad23845330cfa25ca8fc` |
| 上游目录 | `assets/voices/` |
| 许可证 | MIT（见同目录 `LICENSE`） |
| 版权 | Copyright (c) 2026 dleaf6211-hash |
| 条数 | 19 句（14 句 idle + 5 句 pat） |
| 音色 | 上游预合成的「晓伊」风格神经网络语音 |

## 用法

- `config/bot.yaml` 的 `tools.voice_pack` 指向本目录即可；
- 被戳一戳时按 `tools.poke_voice_probability` 概率用 `pat` 语音回应；
- `/语音`（不带参数）随机播一句 `idle`；
- `/语音 摸头` 随机播一句 `pat`；
- `/语音 文本` 仍然走 `tools.tts_command` 合成，不会念语音包里的固定台词。

## 想换自己的语音包

保持同样的 `manifest.json` 结构，把 `file` 指向本目录下的音频文件即可。
支持 `.wav` `.mp3` `.silk` `.amr` `.ogg` `.m4a` `.flac`；文件不存在会自动跳过。
`kind` 可以自己定，比如 `idle` `pat` `greet`，`/语音 <kind>` 会按 kind 找。

## 其它语音来源（未内置）

- **aceice01/dsh-whale-pet**：49 句「晓伊」预合成台词，音频以 base64 内嵌在
  `lib/audio.json`，仓库许可证为 **Non-Commercial Use License**，所以本项目**没有复制**。
  想在本机用，跑 `python scripts/import_voice_pack.py --source aceice --accept-license`，
  会解码到 `data/voice-packs/aceice/`（gitignore），再把 `tools.voice_pack` 加上这个目录。
- **YEJASONJIEXIN/dsh-whale-girl-pet**：16 个 Edge TTS 角色音色，运行时合成，
  仓库里没有预合成音频；本项目参考了它的「角色音色」思路，但没有内置其音频。

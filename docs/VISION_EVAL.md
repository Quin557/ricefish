# 识图评测怎么弄

> **不想自己标注？** 直接：
> ```bat
> python scripts/build_vision_eval.py
> python scripts/eval_vision.py --manifest data/vision-eval.json
> ```
> 它用仓库自带的 19 张表情 + 12 张梗图的素材清单自动生成 30 条关键词，不需要你提供图片。
> 这是「图片卡有没有抓到表情名/台词」的回归基线；想测截图/照片再按下面手动加。

目标：拿 30 张真实聊天图，检查「图片卡」有没有抓到要点。评测会花 token，不是单元测试。

## 1. 准备 30 张图

推荐直接用自己的 QQ 群/私聊里出现过的图：

- 现成收件箱：`data/emoji-inbox/`（机器人自动收的群友图，通常已有几十张）
- 或自己新建 `data/vision-eval/`，把 30 张图放进去（表情包 10、截图 8、照片 6、图表/商品 6）

## 2. 生成 manifest 模板

```bat
python scripts/make_vision_eval.py --src data/emoji-inbox --out data/vision-eval.json --limit 30
```

会在 `data/vision-eval.json` 生成 30 条，每条 `expect` 是空的。

## 3. 给每张图填 2~4 个关键词

打开 `data/vision-eval.json`，把 `expect` 改成你希望图片卡命中的词，例如：

```json
[
  {
    "path": "data/emoji-inbox/30f993faf5a8cd24.png",
    "expect": ["初音", "饭碗", "美味"],
    "kind": "meme"
  },
  {
    "path": "data/emoji-inbox/xxxx.png",
    "expect": ["成绩单", "高数", "分数"],
    "kind": "screenshot"
  }
]
```

关键词不用很精确，命中任意一个就算这条通过；建议写「画面主体 + 图上的字 + 梗/情绪」里最关键的 2~4 个。

## 4. 跑评测

```bat
python scripts/eval_vision.py --manifest data/vision-eval.json
```

- 通过率 ≥80% 算达标。
- 未标注的条目会 SKIP，不计入分母。
- 输出会显示每张图的路径、命中/失败、图片卡摘要，方便回头改提示词。

## 5. 看什么

- **FAIL 但摘要其实对**：关键词写得太窄，放宽 `expect`。
- **摘要空/很泛**：检查 `llm.thinking_vision` 是否是 `disabled`、`max_tokens` 是否太小。
- **图上的字没认出来**：先看 NapCat `ocr_image` 是否可用；不可用就靠视觉模型 OCR。
- **截图/表格类差**：把这张图的 `kind` 标成 `screenshot`，后续可以给这类图单独加 OCR 提示。

## 不想用真实图

`scripts/vision-eval.example.json` 里有 3 条示例，跑：

```bat
python scripts/eval_vision.py
```

它只用 `assets/emoji` 和 `assets/memes` 里的图，适合先验证链路；正式评测还是用自己的 30 张。

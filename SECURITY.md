# 安全

## 报告问题

请不要在公开 issue 里贴 API Key、QQ 号、聊天记录、面板 token。
优先用 GitHub Security Advisory（仓库启用后）或私下联系维护者。

## 部署边界

- 面板默认只监听 `127.0.0.1`。改成 `0.0.0.0` 或放到公网时，必须：
  - 设强 `RICEFISH_PANEL_TOKEN`；
  - 前面加 HTTPS（Caddy/Nginx）；
  - 最好只走内网/Tailscale，不要直接暴露。
- NapCat 的反向 WS 端口（默认 8080）只应给 NapCat 访问，不要公网开放。
- 面板当前是单管理员 token 模型，没有多用户/权限分级；不要给不可信的人 token。

## 密钥与数据

- `.env`、`data/`、`vendor/`、`qr-now.png` 已在 `.gitignore`。
- 日志可能包含聊天内容；对外分享日志前先脱敏。
- 数据库是本地 SQLite，未加密；云上请用磁盘加密 + 备份加密。
- 备份文件包含全部记忆和聊天，按敏感数据对待。

## 依赖与素材

- 运行时默认零第三方依赖；可选 `edge-tts`/`PyYAML`。
- 素材逐项许可见 `assets/SOURCES.json`；CC BY-NC-SA 素材商用/再分发前确认。
- 不要提交来源不明的表情包/图片。

## 账号风险

QQ 第三方协议登录有被风控/冻结的概率。请用小号、白名单、限流、静默时段，
先 `dry_run` 再真实发送；这是协议方案本身的边界，不是本项目能消除的。

"""面板页面渲染（纯字符串模板，不引第三方模板引擎）。"""

from __future__ import annotations

import html
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..app import BotApp

CSS = """
:root{--bg:#0f1115;--panel:#171a21;--line:#252a33;--fg:#e6e8ee;--muted:#8b93a3;
--accent:#5b9dff;--ok:#3fbf7f;--warn:#e0a63a;--bad:#e0605a;
--safe-b:env(safe-area-inset-bottom,0px);--safe-t:env(safe-area-inset-top,0px)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,
"Segoe UI","Microsoft YaHei",sans-serif;padding-bottom:var(--safe-b);
overscroll-behavior-y:contain}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{display:flex;gap:14px;align-items:center;padding:calc(10px + var(--safe-t)) 16px 10px;
border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:9}
header .brand{font-weight:600;font-size:15px;white-space:nowrap;display:flex;gap:7px;
align-items:center}
header .brand .brand-icon{width:24px;height:24px;border-radius:7px;
box-shadow:0 0 0 1px #2a3a58}
header .brand .fish{font-size:17px}
nav{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;-ms-overflow-style:none}
nav::-webkit-scrollbar{display:none}
nav a{padding:6px 10px;border-radius:6px;color:var(--muted);white-space:nowrap}
nav a.active,nav a:hover{background:#232833;color:var(--fg);text-decoration:none}
main{max-width:1180px;margin:0 auto;padding:16px}
h1{font-size:19px;margin:0 0 14px}
h2{font-size:15px;margin:22px 0 10px;color:var(--muted);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.card .k{color:var(--muted);font-size:12px}
.card .v{font-size:20px;font-weight:600;margin-top:4px}
.table-wrap{overflow-x:auto;border-radius:10px}
table{width:100%;border-collapse:collapse;background:var(--panel);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
font-size:13px;vertical-align:top}
th{color:var(--muted);font-weight:500;background:#1c2129;white-space:nowrap}
tr:last-child td{border-bottom:none}
input,select,textarea{background:#101319;border:1px solid var(--line);color:var(--fg);
border-radius:8px;padding:8px 10px;font:inherit;width:100%;min-height:40px}
textarea{min-height:260px;font-family:ui-monospace,Consolas,monospace;font-size:13px}
button{background:var(--accent);border:0;color:#08111f;font-weight:600;padding:9px 16px;
border-radius:8px;cursor:pointer;font:inherit;min-height:40px}
button:active{transform:translateY(1px)}
button.ghost{background:#232833;color:var(--fg)}
button.danger{background:var(--bad);color:#1a0808}
form.inline{display:inline}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.muted{color:var(--muted)}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;
border:1px solid var(--line);color:var(--muted)}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.msg{padding:6px 0;border-bottom:1px dashed var(--line);font-size:13px}
.msg .who{color:var(--accent)}
pre{background:#101319;border:1px solid var(--line);border-radius:8px;padding:12px;
overflow:auto;font-size:12px;line-height:1.5}
footer{color:var(--muted);font-size:12px;text-align:center;padding:24px}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.list{display:flex;flex-direction:column;gap:6px}
.item{display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--line);
border-radius:8px;background:#141820}
.item .grow{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;
border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.badge.ok{border-color:#24543a;color:var(--ok)}
.badge.warn{border-color:#5a4520;color:var(--warn)}
.badge.bad{border-color:#5a2a28;color:var(--bad)}
.tabs{display:flex;gap:6px;overflow-x:auto;margin-bottom:14px}
.tabs a{padding:7px 12px;border:1px solid var(--line);border-radius:8px;color:var(--muted);
white-space:nowrap}
.tabs a.active{background:#232833;color:var(--fg);border-color:#3a4558}
.search{max-width:360px}
.compact th,.compact td{padding:6px 8px}
.warnbox{border:1px solid #5a3a20;background:#1d1710;border-radius:10px;padding:10px 12px;
margin-bottom:14px}
.slider{display:flex;align-items:center;gap:8px;min-width:190px}
.slider input[type=range]{flex:1;min-height:auto;padding:0;accent-color:var(--accent)}
.slider output{min-width:28px;text-align:right;font-variant-numeric:tabular-nums}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#1f2a3a;
border:1px solid #35506f;color:var(--fg);padding:9px 16px;border-radius:99px;font-size:13px;
opacity:0;pointer-events:none;transition:opacity .18s ease;z-index:99;max-width:90vw}
.toast.show{opacity:1}
button.small{min-height:32px;padding:5px 10px;font-size:12px}
.inline-form{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}
.hint{color:var(--muted);font-size:12px;margin-top:2px}
.empty{padding:18px;text-align:center;color:var(--muted)}
.sticker-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.sticker{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.sticker img{width:100%;height:130px;object-fit:contain;background:#0b0e14;border-radius:8px;
margin-bottom:8px}
.sticker input,.sticker select{min-height:34px;padding:5px 8px;font-size:12px}
.sticker button{min-height:32px;padding:5px 10px;font-size:12px}
@media (max-width:600px){
  main{padding:12px}
  header{gap:8px;padding-left:12px;padding-right:12px}
  header .brand span.name{display:none}
  .grid{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
  th,td{padding:7px 8px}
  table{display:block;overflow-x:auto;white-space:nowrap;border-radius:10px}
  td{white-space:normal;min-width:90px}
}
@media (display-mode:standalone){
  footer{padding-bottom:calc(24px + var(--safe-b))}
}
"""

SW_SCRIPT = """
<script>
if ('serviceWorker' in navigator) {
  addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}
</script>
"""

NAV = [
    ("/", "概览"),
    ("/groups", "会话"),
    ("/access", "白名单"),
    ("/profiles", "成员"),
    ("/memories", "记忆"),
    ("/stickers", "表情"),
    ("/personas", "人设"),
    ("/settings", "设置"),
    ("/logs", "日志"),
]


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def layout(title: str, content: str, active: str = "", bot_name: str = "ricefish") -> str:
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{esc(label)}</a>'
        for href, label in NAV
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0b1020">
<meta name="color-scheme" content="dark">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ricefish">
<title>{esc(title)} · {esc(bot_name)}</title>
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/static/favicon.ico" sizes="any">
<link rel="icon" href="/static/icon-192.png" type="image/png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<link rel="stylesheet" href="/static/style.css"></head>
<body>
<header><span class="brand"><img class="brand-icon" src="/static/icon-192.png" alt="">
<span class="name">{esc(bot_name)}</span></span><nav>{nav}</nav>
<span style="margin-left:auto"><form method="post" action="/logout" class="inline">
<button class="ghost" type="submit">退出</button></form></span></header>
<main>{content}</main>
<footer>ricefish · 本地运行 · 数据只存在这台机器上</footer>
<div id="toast" class="toast"></div>
<script src="/static/panel.js" defer></script>
{SW_SCRIPT}
</body></html>"""


def _status_card(label: str, value: str, hint: str = "", href: str = "") -> str:
    hint_html = f'<div class="muted" style="font-size:12px">{esc(hint)}</div>' if hint else ""
    body = f'<div class="k">{esc(label)}</div><div class="v">{value}</div>{hint_html}'
    if href:
        return f'<a class="card" href="{esc(href)}" style="color:inherit;text-decoration:none">{body}</a>'
    return f'<div class="card">{body}</div>'


def _event_label(kind: Any) -> str:
    return {
        "first_meet": "第一次",
        "promise": "约定",
        "conflict": "冲突",
        "apology": "和好",
        "milestone": "里程碑",
        "joke": "梗",
    }.get(str(kind or ""), "事件")


def _affinity_label(value: Any) -> str:
    try:
        score = max(0, min(100, int(value)))
    except (TypeError, ValueError):
        score = 50
    if score <= 20:
        return "陌生"
    if score <= 40:
        return "眼熟"
    if score <= 60:
        return "熟人"
    if score <= 80:
        return "亲近"
    return "很熟"


def _source_badge(app: "BotApp", key: str = "") -> str:
    if key:
        runtime = getattr(app.config, "runtime", {}) or {}
        return "面板" if key in runtime else "配置文件"
    return "面板" if app.access_source() == "panel" else "配置文件"


def _flash(ok: str = "", err: str = "") -> str:
    messages = {
        "added": ("ok", "已加入白名单，立即生效。"),
        "exists": ("ok", "这些号已经在白名单里了。"),
        "removed": ("ok", "已移出白名单；聊天记录和记忆不会删除。"),
        "cleared": ("warn", "已清空这组白名单；现在对应会话不会回复。"),
        "bad_id": ("bad", "号码不对：只填数字，一行一个或用逗号分隔。"),
        "bad_kind": ("bad", "类型不对。"),
        "confirm": ("bad", "清空需要先勾选确认。"),
        "bad_affinity": ("bad", "亲密度要是 0~100。"),
        "unknown": ("bad", "没有执行任何操作。"),
    }
    if ok and ok in messages:
        kind, text = messages[ok]
        return f'<div class="warnbox"><span class="badge {kind}">提示</span> {esc(text)}</div>'
    if err and err in messages:
        kind, text = messages[err]
        return f'<div class="warnbox"><span class="badge bad">错误</span> {esc(text)}</div>'
    return ""


def _uptime(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分钟"


def page_login(error: str = "") -> str:
    err = f'<p class="bad">{esc(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="theme-color" content="#0b1020">
<title>登录 · QQ Bot</title>
<link rel="icon" href="/static/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<link rel="stylesheet" href="/static/style.css"></head>
<body><main style="max-width:360px;margin-top:12vh">
<div class="card"><h1>管理面板</h1>
<p class="muted">输入面板 Token（启动日志里会打印，也可以在 .env 里设 RICEFISH_PANEL_TOKEN）</p>
{err}
<form method="post" action="/login"><input type="password" name="token"
placeholder="面板 Token" autofocus><p></p>
<button type="submit">进入</button></form></div></main></body></html>"""


# ---------------------------------------------------------------------------
# 概览
# ---------------------------------------------------------------------------
def page_dashboard(app: "BotApp") -> str:
    status = app.status()
    store = status["store"]
    connected = (
        '<span class="ok">已连接</span>'
        if status["connected"]
        else '<span class="bad">未连接</span>'
    )
    mode_hint = {"active": "正常", "passive": "被动", "readonly": "只读"}.get(
        status["mode"], status["mode"]
    )
    conc = status.get("concurrency") or {}
    active = int(conc.get("active", 0) or 0)
    max_parallel = int(conc.get("max_parallel", 0) or 0)
    dropped = int(conc.get("dropped_busy", 0) or 0) + int(
        conc.get("dropped_queue_full", 0) or 0
    ) + int(conc.get("timeouts", 0) or 0)
    conc_text = f"{active}/{max_parallel}" if max_parallel else "—"
    conc_hint = f"峰值 {int(conc.get('peak', 0) or 0)} · 丢弃 {dropped}" if max_parallel else ""
    assets = status.get("assets") or {}
    assets_text = (
        f"{assets.get('emoji', 0)} 表情 / {assets.get('memes', 0)} 梗图 / "
        f"{assets.get('voices', 0)} 语音"
    )
    assets_hint = "、".join(assets.get("voice_kinds") or []) or "未加载语音包"
    cards = "".join(
        [
            _status_card("协议端", connected, f"{status['nickname']} ({status['self_id']})"),
            _status_card("运行模式", esc(mode_hint), "试运行" if status["dry_run"] else "真实发送"),
            _status_card("消息总数", str(store["messages"])),
            _status_card("回复 / 主动", f"{status['dispatcher']['replies']} / {status['dispatcher']['proactive']}"),
            _status_card("档案 / 记忆", f"{store['profiles']} / {store['memories']}"),
            _status_card("24h 模型调用", str(store["usage_24h"]["calls"]),
                         f"in {store['usage_24h']['prompt_tokens']:,} / out {store['usage_24h']['completion_tokens']:,}"),
            _status_card("已运行", _uptime(status["uptime_sec"])),
            _status_card("白名单", f"{status['allowed_groups']} 群 / {status['allowed_friends']} 人", "点击管理", "/access"),
            _status_card(
                "主动陪伴",
                "开" if (status.get("companion") or {}).get("enabled") else "关",
                f"今天 {(status.get('companion') or {}).get('today', 0)} 条",
                "/settings",
            ),
            _status_card("并发", conc_text, conc_hint),
            _status_card("素材", assets_text, assets_hint),
            _status_card(
                "肥鱼的心情",
                esc((status.get("mood") or {}).get("label", "—")),
                esc((status.get("mood") or {}).get("reason", "") or "会随时间淡化"),
            ),
        ]
    )

    plugin_rows = "".join(
        f'<tr><td>{esc(item["name"])}</td><td class="muted">{esc(item["file"])}</td>'
        f'<td>{esc(item["description"] or "-")}</td>'
        f'<td>{esc(", ".join(item["commands"].keys()) or "-")}</td>'
        f'<td class="{"bad" if item["error"] else "ok"}">'
        f'{esc(item["error"] or "正常")}</td></tr>'
        for item in status.get("plugins", [])
    )
    plugins_html = (
        "<table><tr><th>插件</th><th>文件</th><th>说明</th><th>指令</th><th>状态</th></tr>"
        + plugin_rows
        + "</table>"
        if plugin_rows
        else '<p class="muted">没装插件。往 plugins/ 扔一个 .py 就能加功能，参考 plugins/example_plugin.py。</p>'
    )

    rows = []
    for msg in app.store.global_recent(25):
        who = esc(msg.get("sender_name") or msg.get("sender_qq"))
        if msg.get("is_bot"):
            who = f'<span class="ok">{esc(app.config.bot.name)}</span>'
        scope = f"{msg.get('scope_type')}:{msg.get('scope_id')}"
        when = time.strftime("%m-%d %H:%M", time.localtime(int(msg.get("ts") or 0)))
        rows.append(
            f'<tr><td class="muted">{when}</td><td>{esc(scope)}</td>'
            f"<td>{who}</td><td>{esc((msg.get('text') or '')[:160])}</td></tr>"
        )
    table = (
        "<table><tr><th>时间</th><th>会话</th><th>发送者</th><th>内容</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else '<p class="muted">还没有消息。</p>'
    )

    warnings = []
    if not status["llm_ready"]:
        warnings.append("没有可用的 DeepSeek API Key，所有回复都会失败。")
    if status["allowed_groups"] == 0 and status["allowed_friends"] == 0:
        warnings.append("白名单为空：机器人不会回复任何人。")
    if not status["connected"]:
        warnings.append("协议端未连接：确认 NapCat 已登录，且反向 WS 指向本程序。")
    warn_html = (
        '<div class="card" style="border-color:#5a3a20">'
        + "".join(f'<div class="warn">⚠ {esc(item)}</div>' for item in warnings)
        + "</div>"
        if warnings
        else ""
    )

    content = f"""<h1>概览</h1>{warn_html}
<div class="grid">{cards}</div>
<h2>最近消息</h2>{table}
<h2>插件</h2>{plugins_html}"""
    return layout("概览", content, "/", app.config.bot.name)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def page_access(app: "BotApp", ok: str = "", err: str = "") -> str:
    groups = app.known_groups()
    friends = app.known_friends()
    source = "面板（数据库）" if app.access_source() == "panel" else "配置文件（.env / bot.yaml）"
    empty = not app.config.access.allowed_groups and not app.config.access.allowed_friends
    warn = (
        '<div class="warnbox"><span class="badge bad">注意</span> '
        "白名单现在是空的：机器人不会回复任何人。添加一个群或好友就会恢复。</div>"
        if empty
        else ""
    )

    def section(kind: str, title: str, items: list[dict]) -> str:
        current = [item for item in items if item.get("allowed")]
        available = [item for item in items if not item.get("allowed")]
        current_html = "".join(
            f'<div class="item"><span class="grow">{esc(item["name"])} '
            f'<span class="muted">{esc(item["id"])}</span></span>'
            f'<form method="post" action="/api/access/remove" class="inline-form">'
            f'<input type="hidden" name="kind" value="{esc(kind)}">'
            f'<input type="hidden" name="id" value="{esc(item["id"])}">'
            f'<button class="small ghost" type="submit">移除</button></form></div>'
            for item in current
        ) or '<div class="empty">还没有加入任何' + ("群" if kind == "group" else "好友") + "。</div>"
        picker_html = "".join(
            f'<form method="post" action="/api/access/add" class="item" data-name="{esc(item["name"])} {esc(item["id"])}">'
            f'<input type="hidden" name="kind" value="{esc(kind)}">'
            f'<input type="hidden" name="ids" value="{esc(item["id"])}">'
            f'<span class="grow">{esc(item["name"])} '
            f'<span class="muted">{esc(item["id"])}</span></span>'
            f'<button class="small" type="submit">加入</button></form>'
            for item in available
        ) or '<div class="empty">该加的都在白名单里了。</div>'
        placeholder = "群号，逗号或换行分隔" if kind == "group" else "QQ 号，逗号或换行分隔"
        return f"""<div class="card">
<h2>{title} <span class="badge">{len(current)}</span></h2>
<div class="list">{current_html}</div>
<form method="post" action="/api/access/add" class="row" style="margin-top:12px">
<input type="hidden" name="kind" value="{esc(kind)}">
<input name="ids" placeholder="{placeholder}" class="search">
<button type="submit" class="small">加入</button>
</form>
<details style="margin-top:12px"><summary class="muted">从已知{title}里选择（按名字搜索）</summary>
<input data-filter="pick-{esc(kind)}" placeholder="搜名字或号码" class="search" style="margin:8px 0">
<div class="list" id="pick-{esc(kind)}">{picker_html}</div>
</details>
<form method="post" action="/api/access/clear" class="row" style="margin-top:12px">
<input type="hidden" name="kind" value="{esc(kind)}">
<label class="muted" style="font-size:12px"><input type="checkbox" name="confirm" value="1" style="width:auto;min-height:auto"> 我确认清空{title}</label>
<button type="submit" class="small danger">清空</button>
</form>
<div class="hint">移除/清空只停止回复，不删除聊天记录和记忆。</div>
</div>"""

    group_html = section("group", "群聊", groups)
    friend_html = section("friend", "好友", friends)
    content = f"""<h1>白名单</h1>{_flash(ok, err)}{warn}
<div class="warnbox"><span class="badge">当前生效</span> {esc(source)}
<span class="muted"> · 白名单在这里改完立即生效；.env 里的值只在第一次运行、面板还没保存过时使用。</span></div>
<div class="cols">{group_html}{friend_html}</div>"""
    return layout("白名单", content, "/access", app.config.bot.name)


def _scope_title(app: "BotApp", scope_type: str, scope_id: int) -> str:
    if scope_type == "group":
        return app.known_group_name(scope_id)
    return app.known_friend_name(scope_id)


def page_groups(app: "BotApp", show_all: bool = False, query: str = "") -> str:
    allowed_groups = set(app.config.access.allowed_groups)
    allowed_friends = set(app.config.access.allowed_friends)
    candidates: list[tuple[str, int, dict]] = []
    seen: set[tuple[str, int]] = set()
    for chat in app.store.list_chats():
        scope_type = str(chat.get("scope_type") or "")
        try:
            scope_id = int(chat.get("scope_id") or 0)
        except (TypeError, ValueError):
            continue
        allowed = (
            scope_id in allowed_groups
            if scope_type == "group"
            else scope_id in allowed_friends
        )
        if not show_all and not allowed:
            continue
        candidates.append((scope_type, scope_id, chat))
        seen.add((scope_type, scope_id))
    for scope_id in sorted(allowed_groups):
        if ("group", scope_id) not in seen:
            candidates.append(("group", scope_id, {}))
    for scope_id in sorted(allowed_friends):
        if ("private", scope_id) not in seen:
            candidates.append(("private", scope_id, {}))

    needle = (query or "").strip().lower()
    rows = []
    for scope_type, scope_id, chat in candidates:
        title = str(chat.get("title") or "") or _scope_title(app, scope_type, scope_id)
        if needle and needle not in title.lower() and needle not in str(scope_id):
            continue
        count = app.store.count_messages(scope_type, scope_id)
        last_ts = app.store.last_message_ts(scope_type, scope_id)
        last = (
            time.strftime("%m-%d %H:%M", time.localtime(int(last_ts)))
            if last_ts
            else "—"
        )
        enabled = bool(chat.get("enabled", 1))
        proactive = bool(chat.get("proactive_enabled", 1))
        allowed_row = scope_id in (
            allowed_groups if scope_type == "group" else allowed_friends
        )
        persona = app.has_persona(scope_type, scope_id)
        if allowed_row:
            enabled_badge = (
                f'<span class="badge {"ok" if enabled else "bad"}">'
                f'{"启用" if enabled else "关闭"}</span>'
            )
            proactive_badge = (
                f'<span class="badge {"ok" if proactive else "warn"}">'
                f'{"主动" if proactive else "被动"}</span>'
            )
        else:
            enabled_badge = '<span class="badge warn">未授权</span>'
            proactive_badge = '<span class="badge">—</span>'
        rows.append(
            f'<tr><td><a href="/group?t={esc(scope_type)}&id={esc(scope_id)}">'
            f"{esc(title)}</a></td>"
            f'<td>{"群聊" if scope_type == "group" else "私聊"}</td>'
            f"<td class=\"muted\">{esc(scope_type)}:{esc(scope_id)}</td>"
            f'<td>{"有" if persona else "—"}</td>'
            f"<td>{count}</td><td>{esc(last)}</td>"
            f"<td>{enabled_badge}</td><td>{proactive_badge}</td></tr>"
        )
    table = (
        '<div class="table-wrap"><table class="compact"><tr><th>名称</th><th>类型</th>'
        "<th>会话</th><th>人设</th><th>消息</th><th>最后</th><th>启用</th>"
        "<th>主动</th></tr>"
        + "".join(rows)
        + "</table></div>"
        if rows
        else '<div class="empty">没有匹配的会话。去「白名单」添加一个群或好友。</div>'
    )
    toggle = (
        '<a class="badge" href="/groups">只看授权</a>'
        if show_all
        else f'<a class="badge" href="/groups?all=1">显示全部（含未授权）</a>'
    )
    search = f"""<form method="get" action="/groups" class="row" style="margin-bottom:14px">
<input name="q" value="{esc(query)}" placeholder="搜群名 / 好友名 / 号码" class="search">
{'<input type="hidden" name="all" value="1">' if show_all else ''}
<button type="submit" class="small">搜索</button>
<a class="badge" href="/access">+ 添加会话</a>{toggle}</form>"""
    warn = (
        '<div class="warnbox"><span class="badge warn">注意</span> '
        "这里显示的是全部历史会话；未授权的不会回复，也不建议在这里操作。</div>"
        if show_all
        else ""
    )
    content = f"<h1>会话</h1>{warn}{search}{table}"
    return layout("会话", content, "/groups", app.config.bot.name)


def page_group(app: "BotApp", scope_type: str, scope_id: int) -> str:
    chat = app.store.get_chat(scope_type, scope_id) or {}
    profiles = app.store.list_profiles(scope_type, scope_id, limit=30)
    memories = app.store.list_memories(scope_type, scope_id, limit=40)
    messages = app.store.scope_recent(scope_type, scope_id, limit=40)
    episodes = app.store.list_episodes(scope_type, scope_id, limit=3)
    events = app.store.list_relationship_events(scope_type, scope_id, limit=8)
    title = str(chat.get("title") or "") or _scope_title(app, scope_type, scope_id)
    next_url = f"/group?t={scope_type}&id={scope_id}"

    member_rows = []
    for index, profile in enumerate(profiles):
        qq = profile.get("qq")
        affinity = max(0, min(100, int(profile.get("affinity") or 50)))
        range_id = f"aff-{scope_type}-{scope_id}-{qq}-{index}"
        display = profile.get("display_name") or profile.get("nickname") or qq
        relation = profile.get("relation") or "—"
        notes_val = profile.get("notes") or ""
        manual_checked = "checked" if profile.get("is_override") else ""
        member_rows.append(
            f"""<tr>
<td><b>{esc(display)}</b>
<div class="muted" style="font-size:12px">{esc(qq)} · {esc(_affinity_label(affinity))}</div></td>
<td>{esc(relation)}</td>
<td><form method="post" action="/api/profile/quick" class="inline-form" data-quick>
<input type="hidden" name="scope_type" value="{esc(scope_type)}">
<input type="hidden" name="scope_id" value="{esc(scope_id)}">
<input type="hidden" name="qq" value="{esc(qq)}">
<input type="hidden" name="manual_present" value="1">
<input type="hidden" name="next" value="{esc(next_url)}">
<div class="slider">
<input type="range" min="0" max="100" step="1" name="affinity" value="{affinity}"
       data-output="{range_id}" aria-label="亲密度">
<output id="{range_id}">{affinity}</output>
<button type="button" class="small ghost" data-delta="-5">-5</button>
<button type="button" class="small ghost" data-delta="-1">-1</button>
<button type="button" class="small ghost" data-delta="1">+1</button>
<button type="button" class="small ghost" data-delta="5">+5</button>
</div></td>
<td><input name="notes" value="{esc(notes_val)}" placeholder="备注（失焦自动保存）" style="min-width:180px"></td>
<td><label class="muted" style="font-size:12px"><input type="checkbox" name="manual" value="1"
 {manual_checked} style="width:auto;min-height:auto"> 人工</label></td>
<td><button type="submit" class="small">保存</button>
<span class="save-state muted" style="font-size:12px"></span>
<div><a class="muted" style="font-size:12px" href="/profile?t={esc(scope_type)}&id={esc(scope_id)}&qq={esc(qq)}">完整档案</a></div></td>
</tr>"""
        )
    member_table = (
        '<div class="table-wrap"><table class="compact"><tr><th>成员</th><th>关系</th>'
        "<th>亲密度</th><th>备注</th><th>人工</th><th></th></tr>"
        + "".join(member_rows)
        + "</table></div>"
        if member_rows
        else '<div class="empty">还没有成员档案；等对方说句话，或去「成员」页添加。</div>'
    )

    episode_rows = "".join(
        f'<tr><td>{esc((item.get("summary") or "")[:160])}</td>'
        f'<td>{esc(item.get("tone") or "—")}</td>'
        f'<td>{esc(item.get("topics") or "—")}</td></tr>'
        for item in episodes
    ) or '<tr><td colspan="3" class="muted">还没有情节记忆；多聊几轮会自动生成。</td></tr>'
    event_rows = "".join(
        f'<tr><td>{esc(_event_label(item.get("kind")))}</td>'
        f'<td>{esc(item.get("content"))}</td></tr>'
        for item in events
    ) or '<tr><td colspan="2" class="muted">还没有关系事件。</td></tr>'

    memory_rows = "".join(
        f'<tr><td>{esc(m.get("subject_qq") or "-")}</td><td>{esc(m.get("kind"))}</td>'
        f'<td>{esc((m.get("content") or "")[:140])}</td><td>{esc(m.get("importance"))}</td></tr>'
        for m in memories
    ) or '<tr><td colspan="4" class="muted">还没有长期记忆</td></tr>'

    msg_rows = "".join(
        f'<div class="msg"><span class="who">'
        f'{"机器人" if m.get("is_bot") else esc(m.get("sender_name"))}</span>: '
        f'{esc((m.get("text") or "")[:200])}</div>'
        for m in reversed(messages)
    ) or '<p class="muted">还没有消息</p>'

    enabled = "checked" if chat.get("enabled", 1) else ""
    proactive = "checked" if chat.get("proactive_enabled", 1) else ""
    kind_label = "群聊" if scope_type == "group" else "私聊"
    allowed_scope = scope_id in (
        set(app.config.access.allowed_groups)
        if scope_type == "group"
        else set(app.config.access.allowed_friends)
    )
    whitelist_warn = (
        ""
        if allowed_scope
        else '<div class="warnbox"><span class="badge warn">未授权</span> '
        "这个会话不在白名单里，改开关也不会回复；"
        '<a href="/access">去白名单添加</a>。</div>'
    )
    persona_link = f"/personas?t={esc(scope_type)}&id={esc(scope_id)}"
    persona_badge = (
        '<span class="badge ok">人设已设置</span>'
        if app.has_persona(scope_type, scope_id)
        else '<span class="badge">默认人设</span>'
    )

    content = f"""<h1>{esc(title)} <span class="tag">{kind_label} · {esc(scope_type)}:{esc(scope_id)}</span></h1>
{whitelist_warn}
<div class="card"><form method="post" action="/api/chat" class="row">
<input type="hidden" name="scope_type" value="{esc(scope_type)}">
<input type="hidden" name="scope_id" value="{esc(scope_id)}">
<label><input type="checkbox" name="enabled" value="1" {enabled}> 启用</label>
<label><input type="checkbox" name="proactive_enabled" value="1" {proactive}> 允许主动陪伴/插话</label>
<button type="submit" class="small">保存</button>
<a class="badge" href="{persona_link}">人设</a>{persona_badge}
<a class="badge" href="/profiles?t={esc(scope_type)}&id={esc(scope_id)}">成员列表</a>
<a class="badge" href="/export?t={esc(scope_type)}&id={esc(scope_id)}">导出 JSON</a>
</form></div>
<div class="tabs" data-tabs>
<a href="#members" data-tab-target="members" class="active">成员</a>
<a href="#episodes" data-tab-target="episodes">情节</a>
<a href="#timeline" data-tab-target="timeline">关系</a>
<a href="#memory" data-tab-target="memory">记忆</a>
<a href="#messages" data-tab-target="messages">消息</a>
</div>
<section class="tab-panel" data-tab="members">
<h2>成员（备注 / 亲密度可以直接改）</h2>{member_table}
</section>
<section class="tab-panel" data-tab="episodes">
<h2>最近情节</h2>
<div class="table-wrap"><table class="compact"><tr><th>这段聊了什么</th><th>情绪</th><th>话题</th></tr>{episode_rows}</table></div>
</section>
<section class="tab-panel" data-tab="timeline">
<h2>关系时间线</h2>
<div class="table-wrap"><table class="compact"><tr><th>类型</th><th>发生了什么</th></tr>{event_rows}</table></div>
</section>
<section class="tab-panel" data-tab="memory">
<h2>长期记忆</h2>
<div class="table-wrap"><table class="compact"><tr><th>主体</th><th>类型</th><th>内容</th><th>重要度</th></tr>{memory_rows}</table></div>
</section>
<section class="tab-panel" data-tab="messages">
<h2>最近消息</h2>{msg_rows}
</section>
<details style="margin-top:18px"><summary class="muted">导入 JSON（档案/记忆/情节/话题，消息不重复导入）</summary>
<form method="post" action="/api/import" style="margin-top:10px">
<input type="hidden" name="scope_type" value="{esc(scope_type)}">
<input type="hidden" name="scope_id" value="{esc(scope_id)}">
<textarea name="data" placeholder="粘贴从「导出 JSON」拿到的内容" style="min-height:120px"></textarea>
<p><button type="submit" class="small">导入</button></p></form></details>"""
    return layout("会话详情", content, "/groups", app.config.bot.name)


# ---------------------------------------------------------------------------
# 成员档案
# ---------------------------------------------------------------------------
def page_profiles(
    app: "BotApp",
    scope_type: str = "",
    scope_id: int = 0,
    query: str = "",
    show_all: bool = False,
) -> str:
    profiles = app.store.search_profiles(
        scope_type=scope_type or None,
        scope_id=scope_id or None,
        query=query,
        limit=300,
    )
    allowed_groups = set(app.config.access.allowed_groups)
    allowed_friends = set(app.config.access.allowed_friends)
    if not show_all:
        profiles = [
            item
            for item in profiles
            if int(item.get("scope_id") or 0)
            in (allowed_groups if item.get("scope_type") == "group" else allowed_friends)
        ]

    grouped: dict[tuple[str, int], list[dict]] = {}
    for item in profiles:
        grouped.setdefault(
            (str(item.get("scope_type")), int(item.get("scope_id") or 0)), []
        ).append(item)

    sections = []
    for (kind, sid), items in grouped.items():
        rows = []
        for item in items:
            qq = item.get("qq")
            affinity = int(item.get("affinity") or 50)
            rows.append(
                f'<tr><td><a href="/profile?t={esc(kind)}&id={esc(sid)}&qq={esc(qq)}">'
                f'{esc(item.get("display_name") or item.get("nickname") or qq)}</a></td>'
                f"<td>{esc(qq)}</td><td>{esc(item.get('relation') or '—')}</td>"
                f"<td>{affinity} <span class=\"muted\">{esc(_affinity_label(affinity))}</span></td>"
                f"<td>{esc((item.get('notes') or '')[:60])}</td>"
                f"<td>{'已锁定' if item.get('is_override') else '自动'}</td></tr>"
            )
        sections.append(
            f'<h2>{esc(_scope_title(app, kind, sid))} '
            f'<span class="tag">{esc(kind)}:{esc(sid)}</span> '
            f'<span class="badge">{len(items)} 人</span></h2>'
            '<div class="table-wrap"><table class="compact"><tr><th>称呼</th><th>QQ</th>'
            "<th>关系</th><th>亲密度</th><th>备注</th><th>来源</th></tr>"
            + "".join(rows)
            + "</table></div>"
        )
    body = "".join(sections) or '<div class="empty">没有匹配的成员档案。</div>'
    filter_label = ""
    if scope_type and scope_id:
        filter_label = (
            f'<div class="warnbox"><span class="badge">筛选</span> '
            f"{esc(_scope_title(app, scope_type, scope_id))} "
            f'<a class="badge" href="/profiles">清除</a></div>'
        )
    toggle = (
        '<a class="badge" href="/profiles">只看授权</a>'
        if show_all
        else '<a class="badge" href="/profiles?all=1">显示全部</a>'
    )
    search = f"""<form method="get" action="/profiles" class="row" style="margin-bottom:14px">
<input name="q" value="{esc(query)}" placeholder="搜 QQ / 称呼 / 标签 / 备注" class="search">
<input type="hidden" name="t" value="{esc(scope_type)}">
<input type="hidden" name="id" value="{esc(scope_id)}">
{'<input type="hidden" name="all" value="1">' if show_all else ''}
<button type="submit" class="small">搜索</button>{toggle}</form>"""
    content = f"<h1>成员</h1>{filter_label}{search}{body}"
    return layout("成员", content, "/profiles", app.config.bot.name)


def page_profile(app: "BotApp", scope_type: str, scope_id: int, qq: int) -> str:
    profile = app.store.get_profile(scope_type, scope_id, qq)
    if not profile:
        content = (
            "<h1>成员档案</h1><div class=\"empty\">找不到这个人的档案。"
            "<a href=\"/profiles\">返回成员列表</a></div>"
        )
        return layout("成员档案", content, "/profiles", app.config.bot.name)
    affinity = max(0, min(100, int(profile.get("affinity") or 50)))
    title = str(profile.get("display_name") or qq)
    next_url = f"/profile?t={scope_type}&id={scope_id}&qq={qq}"
    fields = [
        ("display_name", "称呼"),
        ("aliases", "别名"),
        ("identity", "身份"),
        ("relation", "关系"),
        ("tags", "标签"),
        ("preferences", "喜欢"),
        ("taboos", "雷点"),
        ("mood", "最近状态"),
    ]
    inputs = "".join(
        f'<div><div class="muted" style="font-size:12px">{label}</div>'
        f'<input name="{key}" value="{esc(profile.get(key))}"></div>'
        for key, label in fields
    )
    prompt_block = app.prompts.profile_block(profile)
    prompt_text = prompt_block or "（还没有档案内容）"
    notes_val = profile.get("notes") or ""
    manual_checked = "checked" if profile.get("is_override") else ""
    memories = app.store.list_memories(
        scope_type, scope_id, qq=int(qq), limit=200
    )
    open_loops = app.store.list_open_loops(
        scope_type, scope_id, subject_qq=int(qq), limit=20
    )
    loop_rows = "".join(
        f'<tr><td>{esc({"plan": "计划", "promise": "约定", "question": "话题"}.get(m.get("kind"), "话题"))}</td>'
        f'<td>{esc(m.get("content"))}</td>'
        f'<td><form method="post" action="/api/loop/resolve" class="inline">'
        f'<input type="hidden" name="id" value="{esc(m.get("id"))}">'
        f'<button class="small ghost" type="submit">完成</button></form>'
        f'<form method="post" action="/api/loop/delete" class="inline">'
        f'<input type="hidden" name="id" value="{esc(m.get("id"))}">'
        f'<button class="small danger" type="submit">删除</button></form></td></tr>'
        for m in open_loops
    ) or '<tr><td colspan="3" class="muted">还没有记着的约定/计划。</td></tr>'
    memory_rows = "".join(
        f'<tr><td>{esc(m.get("kind"))}</td><td>{esc((m.get("content") or "")[:160])}</td>'
        f'<td>{esc(m.get("importance"))}</td>'
        f'<td><form method="post" action="/api/memory/archive" class="inline">'
        f'<input type="hidden" name="id" value="{esc(m.get("id"))}">'
        f'<button class="small ghost" type="submit">归档</button></form>'
        f'<form method="post" action="/api/memory/delete" class="inline">'
        f'<input type="hidden" name="id" value="{esc(m.get("id"))}">'
        f'<button class="small danger" type="submit">删除</button></form></td></tr>'
        for m in memories
    ) or '<tr><td colspan="4" class="muted">还没有关于这个人的长期记忆。</td></tr>'
    recent = [
        item
        for item in app.store.scope_recent(scope_type, scope_id, limit=80)
        if int(item.get("sender_qq") or 0) == int(qq)
    ][-20:]
    msg_rows = "".join(
        f'<div class="msg"><span class="who">'
        f'{"机器人" if m.get("is_bot") else esc(m.get("sender_name"))}</span>: '
        f'{esc((m.get("text") or "")[:200])}</div>'
        for m in reversed(recent)
    ) or '<p class="muted">还没有这个人的消息。</p>'
    content = f"""<h1>{esc(title)} <span class="tag">{esc(scope_type)}:{esc(scope_id)}</span></h1>
<div class="row" style="margin-bottom:12px">
<a class="badge" href="/group?t={esc(scope_type)}&id={esc(scope_id)}">返回会话</a>
<a class="badge" href="/profiles?t={esc(scope_type)}&id={esc(scope_id)}">同会话成员</a>
<span class="badge {'ok' if profile.get('is_override') else ''}">{'人工锁定' if profile.get('is_override') else '自动更新'}</span>
</div>
<div class="card"><form method="post" action="/api/profile">
<input type="hidden" name="scope_type" value="{esc(scope_type)}">
<input type="hidden" name="scope_id" value="{esc(scope_id)}">
<input type="hidden" name="qq" value="{esc(qq)}">
<input type="hidden" name="manual_present" value="1">
<input type="hidden" name="next" value="{esc(next_url)}">
<div class="grid">{inputs}</div>
<div style="margin-top:12px"><div class="muted" style="font-size:12px">备注</div>
<textarea name="notes" style="min-height:100px">{esc(notes_val)}</textarea></div>
<div class="row" style="margin-top:12px">
<div class="slider" style="flex:1;min-width:260px">
<span class="muted">亲密度</span>
<input type="range" min="0" max="100" step="1" name="affinity" value="{affinity}"
       data-output="affinity-out" aria-label="亲密度">
<output id="affinity-out">{affinity}</output>
<span class="muted">{esc(_affinity_label(affinity))}</span>
</div>
<label class="muted" style="font-size:12px"><input type="checkbox" name="manual" value="1"
 {manual_checked} style="width:auto;min-height:auto"> 人工确认（抽取器不再改）</label>
<button type="submit">保存</button>
</div></form></div>
<h2>她眼中的你</h2>
<pre>{esc(prompt_text)}</pre>
<h2>还记着的事</h2>
<div class="table-wrap"><table class="compact"><tr><th>类型</th><th>内容</th><th></th></tr>{loop_rows}</table></div>
<h2>关于这个人的记忆</h2>
<div class="table-wrap"><table class="compact"><tr><th>类型</th><th>内容</th><th>重要</th><th></th></tr>{memory_rows}</table></div>
<h2>最近消息</h2>{msg_rows}"""
    return layout("成员档案", content, "/profiles", app.config.bot.name)


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------
def page_memories(app: "BotApp", query: str = "") -> str:
    memories = app.store.list_memories(keyword=query, limit=300)
    rows = []
    for m in memories:
        rows.append(
            f'<tr><td>{esc(m.get("id"))}</td>'
            f'<td>{esc(m.get("scope_type"))}:{esc(m.get("scope_id"))}</td>'
            f'<td>{esc(m.get("subject_qq") or "-")}</td>'
            f'<td>{esc(m.get("kind"))}</td>'
            f'<td>{esc(m.get("content"))}</td>'
            f'<td>{esc(m.get("importance"))}</td>'
            f'<td>{esc(m.get("use_count"))}</td>'
            f'<td><form method="post" action="/api/memory/archive" class="inline">'
            f'<input type="hidden" name="id" value="{esc(m.get("id"))}">'
            f'<button class="ghost" type="submit">归档</button></form> '
            f'<form method="post" action="/api/memory/delete" class="inline">'
            f'<input type="hidden" name="id" value="{esc(m.get("id"))}">'
            f'<button class="danger" type="submit">删除</button></form></td></tr>'
        )
    table = (
        "<table><tr><th>ID</th><th>会话</th><th>主体</th><th>类型</th><th>内容</th>"
        "<th>重要</th><th>用到</th><th></th></tr>" + "".join(rows) + "</table>"
        if rows
        else '<p class="muted">没有记忆。多聊几天，或者把记忆抽取打开。</p>'
    )
    search = f"""<form method="get" action="/memories" class="row" style="margin-bottom:16px">
<input name="q" value="{esc(query)}" placeholder="搜内容" style="max-width:320px">
<button type="submit">搜索</button></form>"""
    return layout("记忆", f"<h1>长期记忆</h1>{search}{table}", "/memories", app.config.bot.name)


# ---------------------------------------------------------------------------
# 人设
# ---------------------------------------------------------------------------
def page_stickers(
    app: "BotApp", query: str = "", show_all: bool = False, tagged: int = 0
) -> str:
    stickers = app.store.list_stickers(include_disabled=True, limit=300)
    needle = (query or "").strip().lower()
    if needle:
        stickers = [
            item
            for item in stickers
            if needle in str(item.get("emotion") or "").lower()
            or needle in str(item.get("tags") or "").lower()
            or needle in str(item.get("path") or "").lower()
        ]
    if not show_all:
        stickers = [item for item in stickers if item.get("enabled", 1)]
    cards = []
    for item in stickers:
        item_id = int(item.get("id") or 0)
        emotion = str(item.get("emotion") or "")
        tags = str(item.get("tags") or "")
        enabled = bool(item.get("enabled", 1))
        used = int(item.get("used_count") or 0)
        scope = (
            f"{item.get('scope_type')}:{item.get('scope_id')}"
            if item.get("scope_type")
            else "全局"
        )
        scope_type_val = str(item.get("scope_type") or "")
        scope_id_val = int(item.get("scope_id") or 0)
        license_text = str(item.get("license") or item.get("source") or "—")
        selected_on = "selected" if enabled else ""
        selected_off = "" if enabled else "selected"
        selected_global = "selected" if not scope_type_val else ""
        selected_group = "selected" if scope_type_val == "group" else ""
        selected_private = "selected" if scope_type_val == "private" else ""
        cards.append(
            f"""<div class="sticker">
<img src="/sticker/{item_id}" alt="" loading="lazy">
<form method="post" action="/api/sticker/update">
<input type="hidden" name="id" value="{item_id}">
<div class="row" style="gap:6px">
<input name="emotion" value="{esc(emotion)}" placeholder="情绪" style="min-width:80px">
<input name="tags" value="{esc(tags)}" placeholder="标签" style="min-width:110px">
</div>
<div class="row" style="gap:6px;margin-top:6px">
<select name="enabled" style="min-width:90px">
<option value="1" {selected_on}>启用</option>
<option value="0" {selected_off}>停用</option>
</select>
<button class="small" type="submit">保存</button>
</div>
<div class="row" style="gap:6px;margin-top:6px">
<select name="scope_type" style="min-width:80px">
<option value="" {selected_global}>全局池</option>
<option value="group" {selected_group}>群</option>
<option value="private" {selected_private}>私聊</option>
</select>
<input name="scope_id" value="{scope_id_val}" placeholder="群号/QQ" style="min-width:90px">
</div>
<div class="hint">{esc(scope)} · 用 {used} 次</div>
<div class="hint">许可：{esc(license_text)[:60]}</div>
</form>
<form method="post" action="/api/sticker/delete" class="inline">
<input type="hidden" name="id" value="{item_id}">
<button class="small danger" type="submit">删除</button></form>
</div>"""
        )
    body = (
        '<div class="sticker-grid">' + "".join(cards) + "</div>"
        if cards
        else '<div class="empty">表情库还是空的。群友发图会自动收进来，内置表情会在启动时导入。</div>'
    )
    toggle = (
        '<a class="badge" href="/stickers">只看启用</a>'
        if show_all
        else '<a class="badge" href="/stickers?all=1">显示停用</a>'
    )
    search = f"""<form method="get" action="/stickers" class="row" style="margin-bottom:14px">
<input name="q" value="{esc(query)}" placeholder="搜情绪 / 标签 / 文件名" class="search">
{'<input type="hidden" name="all" value="1">' if show_all else ''}
<button type="submit" class="small">搜索</button>{toggle}</form>"""
    tagged_html = (
        f'<div class="warnbox"><span class="badge ok">完成</span> 本轮自动打标 {tagged} 张。</div>'
        if tagged
        else ""
    )
    content = f"""<h1>表情库</h1>{tagged_html}
<div class="warnbox"><span class="badge">发送策略</span>
按当前情绪/场景挑一张，同一会话会避开上一张；这里可以改标签、停用或删除。
群聊的图和内置表情都会进库，默认全局可用。</div>
<div class="row" style="margin-bottom:12px">
<form method="post" action="/api/sticker/tag" class="inline">
<input type="hidden" name="limit" value="5">
<button class="small" type="submit">自动打标 5 张</button></form>
<span class="muted">用视觉模型给还没有标签的表情打情绪/标签；一次少量，避免烧钱。</span>
</div>
{search}{body}"""
    return layout("表情", content, "/stickers", app.config.bot.name)


def page_personas(app: "BotApp", kind: str = "global", scope_id: int = 0) -> str:
    kind = kind if kind in ("global", "group", "private") else "global"
    groups = [item for item in app.known_groups() if item.get("allowed")]
    friends = [item for item in app.known_friends() if item.get("allowed")]
    pool = groups if kind == "group" else friends
    if kind in ("group", "private") and not scope_id and pool:
        scope_id = int(pool[0]["id"])

    if kind == "global":
        target = app.persona_dir / "global.md"
    else:
        target = app.persona_path(kind, scope_id)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        text = ""

    tabs = "".join(
        f'<a class="{"active" if kind == key else ""}" href="/personas?t={key}">{label}</a>'
        for key, label in (
            ("global", "全局"),
            ("group", "群聊"),
            ("private", "私聊"),
        )
    )
    picker = ""
    if kind == "group":
        picker = "".join(
            f'<a class="badge {"ok" if int(item["id"]) == scope_id else ""}" '
            f'href="/personas?t=group&id={esc(item["id"])}">{esc(item["name"])}</a>'
            for item in groups
        ) or '<span class="muted">还没有授权群；先去白名单添加。</span>'
    elif kind == "private":
        picker = "".join(
            f'<a class="badge {"ok" if int(item["id"]) == scope_id else ""}" '
            f'href="/personas?t=private&id={esc(item["id"])}">{esc(item["name"])}</a>'
            for item in friends
        ) or '<span class="muted">还没有授权好友；先去白名单添加。</span>'

    title = {"global": "全局人设", "group": "群人设", "private": "私聊人设"}[kind]
    if kind != "global":
        name = _scope_title(app, kind, scope_id) if scope_id else ""
        title += f" · {name}" if name else ""
    scope_value = scope_id if kind != "global" else ""
    placeholder = {
        "global": "她是谁、性格、说话习惯、底线……",
        "group": "这个群的气氛、称呼习惯、群规、梗……",
        "private": "只对这个人：称呼、关系、说话方式、你们的约定、雷点……",
    }[kind]
    form = f"""<form method="post" action="/api/persona">
<input type="hidden" name="scope_type" value="{esc(kind)}">
<input type="hidden" name="scope_id" value="{esc(scope_value)}">
<textarea name="text" placeholder="{esc(placeholder)}">{esc(text)}</textarea>
<p><button type="submit">保存{title}</button>
<span class="muted">保存后下一条消息立即生效</span></p></form>"""
    if kind != "global" and not scope_id:
        form = '<div class="empty">先在上面的会话列表里选一个，再写人设。</div>'
    hint = (
        '<div class="warnbox"><span class="badge">说明</span> '
        "私聊人设只在这个人的私聊里生效，不会带进群聊；全局人设仍然是底座。</div>"
        if kind == "private"
        else ""
    )
    content = f"<h1>人设</h1><div class=\"tabs\">{tabs}</div>{hint}" \
              f"<div class=\"card\"><h2>{esc(title)}</h2>{form}</div>" \
              f"<h2>选择会话</h2><div class=\"row\">{picker}</div>"
    return layout("人设", content, "/personas", app.config.bot.name)


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
def page_settings(app: "BotApp") -> str:
    cfg = app.config
    mode_options = "".join(
        f'<option value="{value}" {"selected" if cfg.bot.mode == value else ""}>{label}</option>'
        for value, label in [
            ("active", "active · 正常回话 + 主动插话"),
            ("passive", "passive · 只被动回复"),
            ("readonly", "readonly · 只记录不说话"),
        ]
    )
    access_source = (
        "面板（数据库）" if app.access_source() == "panel" else "配置文件（.env / bot.yaml）"
    )
    whitelist = (
        f'<div class="row"><span class="badge ok">{len(cfg.access.allowed_groups)} 个群</span>'
        f'<span class="badge ok">{len(cfg.access.allowed_friends)} 个好友</span>'
        '<a class="badge" href="/access">管理白名单 →</a></div>'
        f'<div class="hint">当前生效：{esc(access_source)}。添加/移除请用「白名单」页，不再手打逗号。</div>'
    )
    dry_warn = (
        '<div class="warnbox"><span class="badge warn">试运行</span> '
        "现在只写日志、不真的发消息。确认无误后再关掉。</div>"
        if cfg.bot.dry_run
        else '<div class="warnbox"><span class="badge bad">真实发送</span> '
        "现在会真的把消息发到 QQ；先在测试群确认。</div>"
    )
    mode_badge = _source_badge(app, "mode")
    send_badge = _source_badge(app, "group_per_min")
    dry_on = "selected" if cfg.bot.dry_run else ""
    dry_off = "" if cfg.bot.dry_run else "selected"
    proactive_on = "selected" if cfg.trigger.proactive.enabled else ""
    proactive_off = "" if cfg.trigger.proactive.enabled else "selected"
    companion_on = "selected" if cfg.companion.enabled else ""
    companion_off = "" if cfg.companion.enabled else "selected"
    companion_badge = _source_badge(app, "companion_enabled")
    content = f"""<h1>设置</h1>{dry_warn}
<div class="card"><h2>白名单</h2>{whitelist}</div>
<div class="card" style="margin-top:14px"><form method="post" action="/api/settings">
<h2>运行模式 <span class="badge">{esc(mode_badge)}</span></h2>
<div class="grid">
<div><div class="muted">运行模式</div><select name="mode">{mode_options}</select></div>
<div><div class="muted">试运行（只打日志不发送）</div>
<select name="dry_run">
<option value="1" {dry_on}>开</option>
<option value="0" {dry_off}>关（真的会发出去）</option>
</select></div>
<div><div class="muted">群聊主动插话</div>
<select name="proactive_enabled">
<option value="1" {proactive_on}>开</option>
<option value="0" {proactive_off}>关</option>
</select></div>
<div><div class="muted">插话概率 0~1</div>
<input name="proactive_probability" type="number" min="0" max="1" step="0.01"
       value="{cfg.trigger.proactive.probability}">
<div class="hint">每条群消息命中后才会评估；0 = 不插话。想更主动可调到 0.3。</div></div>
<div><div class="muted">两次插话最小间隔（秒）</div>
<input name="proactive_min_interval" type="number" min="0" max="86400"
       value="{cfg.trigger.proactive.min_interval_sec}"></div>
<div><div class="muted">每群每天主动上限</div>
<input name="proactive_daily_quota" type="number" min="0" max="1000"
       value="{cfg.trigger.proactive.daily_quota}"></div>
<div><div class="muted">接话相关度阈值 0~1</div>
<input name="proactive_relevance_threshold" type="number" min="0" max="1" step="0.05"
       value="{cfg.trigger.proactive.relevance_threshold}">
<div class="hint">越低越容易接话；0.5 比 0.6 主动。</div></div>
<div><div class="muted">机器人名字</div>
<input name="bot_name" value="{esc(cfg.bot.name)}"></div>
</div>
<h2>发送与限流 <span class="badge">{esc(send_badge)}</span></h2>
<div class="grid">
<div><div class="muted">每群每分钟上限</div>
<input name="group_per_min" type="number" min="1" max="1000" value="{cfg.rate_limit.group_per_min}"></div>
<div><div class="muted">每群每天上限</div>
<input name="group_per_day" type="number" min="1" max="100000" value="{cfg.rate_limit.group_per_day}"></div>
<div><div class="muted">静默时段</div>
<input name="quiet_hours" value="{esc(cfg.rate_limit.quiet_hours)}" placeholder="00:30-07:30">
<div class="hint">跨零点也支持；留空表示不静默。</div></div>
<div><div class="muted">同时回复上限（并发）</div>
<input name="max_parallel" type="number" min="1" max="32" value="{cfg.concurrency.max_parallel}"></div>
</div>
<p><button type="submit">保存并立即生效</button>
<span class="muted">其余参数改 config/bot.yaml 后重启</span></p></form></div>

<div class="card" style="margin-top:14px"><form method="post" action="/api/settings">
<h2>主动陪伴 <span class="badge">{esc(companion_badge)}</span></h2>
<div class="warnbox" style="margin-bottom:12px">
<span class="badge">私聊</span> 隔一阵没说话就主动发一条；群聊主动插话在上面。
每个会话还能单独关（会话详情 / <code>/主动 关</code>）。
</div>
<div class="grid">
<div><div class="muted">总开关</div>
<select name="companion_enabled">
<option value="1" {companion_on}>开</option>
<option value="0" {companion_off}>关</option>
</select></div>
<div><div class="muted">对方没说话多久可以找（分钟）</div>
<input name="companion_idle_minutes" type="number" min="5" max="10080"
       value="{cfg.companion.idle_minutes}"></div>
<div><div class="muted">机器人说完多久可以补一句（分钟）</div>
<input name="companion_follow_up_minutes" type="number" min="5" max="10080"
       value="{cfg.companion.follow_up_minutes}"></div>
<div><div class="muted">同一会话冷却（分钟）</div>
<input name="companion_cooldown_minutes" type="number" min="5" max="10080"
       value="{cfg.companion.cooldown_minutes}"></div>
<div><div class="muted">每个会话每天上限</div>
<input name="companion_daily_per_chat" type="number" min="1" max="100"
       value="{cfg.companion.daily_per_chat}"></div>
<div><div class="muted">所有会话每天合计上限</div>
<input name="companion_daily_total" type="number" min="1" max="1000"
       value="{cfg.companion.daily_total}"></div>
</div>
<p><button type="submit">保存主动陪伴</button>
<span class="muted">默认会避开静默时段；想更主动就把间隔调小、上限调大。</span></p>
</form></div>

<h2>当前配置</h2>
<div class="table-wrap"><table class="compact">
<tr><th>项</th><th>值</th></tr>
<tr><td>模型</td><td>{esc(cfg.llm.model)}（备用 {esc(cfg.llm.pro_model)}）</td></tr>
<tr><td>视觉</td><td>{esc(cfg.llm.vision_model)} · 最多 {esc(cfg.tools.vision_max_images)} 张/轮</td></tr>
<tr><td>温度 / 最大输出</td><td>{esc(cfg.llm.temperature)} / {esc(cfg.llm.max_output_tokens)}</td></tr>
<tr><td>短期窗口</td><td>{esc(cfg.memory.window_messages)} 条 / {esc(cfg.memory.window_tokens)} tokens</td></tr>
<tr><td>长期召回</td><td>top {esc(cfg.memory.retrieve_top_k)} / ≤{esc(cfg.memory.retrieve_max_chars)} 字</td></tr>
<tr><td>抽取</td><td>每 {esc(cfg.memory.extractor.every_messages)} 条或静默 {esc(cfg.memory.extractor.idle_minutes)} 分钟</td></tr>
<tr><td>白名单来源</td><td>{esc(access_source)}</td></tr>
<tr><td>主动陪伴</td><td>{'开' if cfg.companion.enabled else '关'} · 闲聊 {esc(cfg.companion.idle_minutes)} 分钟 / 冷却 {esc(cfg.companion.cooldown_minutes)} 分钟 / 每日 {esc(cfg.companion.daily_total)} 条</td></tr>
<tr><td>协议端</td><td>ws://{esc(cfg.onebot.host)}:{esc(cfg.onebot.port)}{esc(cfg.onebot.path)}</td></tr>
</table></div>"""
    return layout("设置", content, "/settings", app.config.bot.name)


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def page_logs(app: "BotApp", lines: int = 200) -> str:
    import os

    path = os.path.join("data", "logs", "bot.log")
    text = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = "".join(fh.readlines()[-lines:])
    body = f"<pre>{esc(text)}</pre>" if text else '<p class="muted">还没有日志。</p>'
    return layout("日志", f"<h1>日志（最近 {lines} 行）</h1>{body}", "/logs", app.config.bot.name)

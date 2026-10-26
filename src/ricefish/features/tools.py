"""小工具：天气、联网搜索。

都是可选能力，失败就返回一句人话，不往群里抛异常。
只用标准库 urllib；跑在 to_thread 里，不阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from ..config import Config

log = logging.getLogger("ricefish.tools")

UA = "ricefish-bot/0.2 (+https://github.com/)"
UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _get_json(url: str, timeout: float = 8.0) -> dict | list | None:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        log.debug("请求失败 %s: %s", url, exc)
        return None


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> dict | None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
            return body if isinstance(body, dict) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        log.debug("请求失败 %s: %s", url, exc)
        return None


class Tools:
    def __init__(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------
    async def weather(self, city: str) -> str:
        if not self.config.tools.weather:
            return "天气功能关掉了。"
        city = (city or "").strip() or "北京"
        data = await asyncio.to_thread(self._weather_sync, city)
        return data

    def _weather_sync(self, city: str) -> str:
        if city.lower() in ("here", "本地"):
            url = "https://wttr.in/?format=j1"
        else:
            url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1&lang=zh"
        data = _get_json(url)
        if not isinstance(data, dict):
            return f"查不到「{city}」的天气，可能网不通或者城市名不对。"
        try:
            current = (data.get("current_condition") or [{}])[0]
            area = ((data.get("nearest_area") or [{}])[0].get("areaName") or [{}])[0]
            area_name = area.get("value") if isinstance(area, dict) else ""
            desc = ((current.get("lang_zh") or [{}])[0].get("value")) or (
                (current.get("weatherDesc") or [{}])[0].get("value") or ""
            )
            temp = current.get("temp_C", "?")
            feels = current.get("FeelsLikeC", "?")
            humidity = current.get("humidity", "?")
            wind = current.get("windspeedKmph", "?")
            today = (data.get("weather") or [{}])[0]
            high, low = today.get("maxtempC", "?"), today.get("mintempC", "?")
        except (IndexError, AttributeError, TypeError):
            return f"「{city}」的天气数据有点怪，读不出来。"
        label = area_name or city
        return (
            f"{label}：{desc}，{temp}℃（体感 {feels}℃），今天 {low}~{high}℃。"
            f"湿度 {humidity}%，风 {wind} km/h。"
        )

    # ------------------------------------------------------------------
    async def search(self, query: str) -> str:
        query = (query or "").strip()
        if not query:
            return "搜什么？"
        key = (self.config.tools.search_key or "").strip()
        if key:
            data = await asyncio.to_thread(
                _post_json,
                "https://api.tavily.com/search",
                {"api_key": key, "query": query, "max_results": 3, "search_depth": "basic"},
            )
            text = _format_tavily(data, query)
            if text:
                return text
        # 没配 Key 或 Tavily 失败：走免费网页搜索
        results = await asyncio.to_thread(self._free_search, query)
        if results:
            return "\n".join(f"· {title}：{snippet}" for title, snippet in results[:4])
        return f"没搜到「{query}」相关的东西，可能是网络或者搜索引擎拦了。"

    def _free_search(self, query: str) -> list[tuple[str, str]]:
        """依次尝试几个免费搜索页，谁先出结果用谁。"""
        for engine in (self._search_bing, self._search_ddg, self._search_baidu):
            try:
                results = engine(query)
            except Exception as exc:  # noqa: BLE001
                log.debug("%s 搜索失败: %s", engine.__name__, exc)
                continue
            if results:
                return results
        return []

    @staticmethod
    def _search_bing(query: str) -> list[tuple[str, str]]:
        url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query)
        html = _get_html(url)
        if not html:
            return []
        out: list[tuple[str, str]] = []
        for block in re.findall(r'<li class="b_algo".*?</li>', html, re.S)[:5]:
            title = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
            snippet = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
            name = _strip_tags(title.group(1)) if title else ""
            text = _strip_tags(snippet.group(1)) if snippet else ""
            if name:
                out.append((name[:60], text[:100]))
        return out

    @staticmethod
    def _search_ddg(query: str) -> list[tuple[str, str]]:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        html = _get_html(url)
        if not html:
            return []
        out: list[tuple[str, str]] = []
        for block in re.findall(r'<a rel="nofollow" class="result__a".*?</a>', html, re.S)[:5]:
            title = _strip_tags(block)
            if title:
                out.append((title[:60], ""))
        return out

    @staticmethod
    def _search_baidu(query: str) -> list[tuple[str, str]]:
        url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(query)
        html = _get_html(url)
        if not html:
            return []
        out: list[tuple[str, str]] = []
        for block in re.findall(r'<h3[^>]*class="[^"]*t[^"]*".*?</h3>', html, re.S)[:5]:
            title = _strip_tags(block)
            if title:
                out.append((title[:60], ""))
        return out

    # ------------------------------------------------------------------
    async def read_url(self, url: str, limit: int = 1800) -> str:
        """抓一个网页，抽正文给模型看（用于"先学再答"）。"""
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return "只看 http/https 开头的网址。"
        html = await asyncio.to_thread(_get_html, url)
        if not html:
            return "这个网页打不开。"
        text = _html_to_text(html)
        if not text:
            return "网页打开了，但没抽出正文。"
        return text[:limit]


def _get_html(url: str, timeout: float = 12.0) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA_BROWSER,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.debug("抓取失败 %s: %s", url, exc)
        return ""


def _strip_tags(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if len(line) >= 12]
    return "\n".join(lines).strip()


def _format_tavily(data, query: str) -> str:
    if not data:
        return ""
    results = data.get("results") or []
    if not results:
        return ""
    lines = []
    for item in results[:3]:
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip().replace("\n", " ")
        if title:
            lines.append(f"· {title}：{content[:80]}")
    return "\n".join(lines)

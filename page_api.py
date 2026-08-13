"""B站AI伴侣 拓展页 API 路由。

遵循 AstrBot 拓展页约定：
- 通过 plugin.context.register_web_api 注册路由
- 路由前缀 /astrbot_plugin_bili_ai_companion/page
- 前端 bridge endpoint 形如 "page/<route>"
- 返回统一 JSON 结构 {ok, data, error}
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .core.config import (
    PLUGIN_NAME,
    AFFECTION_FILE,
    MEMORY_FILE,
    PERMANENT_MEMORY_FILE,
    USER_PROFILE_FILE,
    PERSONALITY_FILE,
    WATCH_LOG_FILE,
    DYNAMIC_LOG_FILE,
    REPLY_LOG_FILE,
    PROACTIVE_LOG_FILE,
    BANGUMI_WATCH_LOG_FILE,
    BINDING_FILE,
    MOOD_FILE,
    MEMORY_SYNC_STATE_FILE,
    LEVEL_NAMES,
)

PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"


class PluginPageApi:
    """拓展页后端 API。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.cfg = plugin.config

    # ------------------------------------------------------------------ 注册
    def register_routes(self) -> None:
        reg = self.plugin.context.register_web_api
        routes = [
            ("/status", self.status, ["GET"], "BiliCompanion 状态总览"),
            ("/memory", self.memory_list, ["GET"], "记忆列表"),
            ("/permanent-memory", self.permanent_memory_list, ["GET"], "永久记忆列表"),
            ("/affection", self.affection_list, ["GET"], "好感度列表"),
            ("/profiles", self.profiles_list, ["GET"], "用户画像列表"),
            ("/personality", self.personality_detail, ["GET"], "性格演化详情"),
            ("/watch-log", self.watch_log, ["GET"], "看视频日志"),
            ("/dynamic-log", self.dynamic_log, ["GET"], "动态日志"),
            ("/reply-log", self.reply_log, ["GET"], "回复日志"),
            ("/bangumi-log", self.bangumi_log, ["GET"], "番剧日志"),
            ("/schedule", self.schedule, ["GET"], "今日计划"),
            ("/mood", self.mood_detail, ["GET"], "心情详情"),
            ("/bindings", self.bindings_list, ["GET"], "QQ-B站绑定列表"),
            ("/sync-status", self.sync_status, ["GET"], "memory_companion 同步状态"),
            ("/sync/run", self.sync_run, ["POST"], "手动触发一次同步"),
            ("/config", self.config_detail, ["GET"], "插件配置概览"),
            ("/actions/start", self.action_start, ["POST"], "启动后台任务"),
            ("/actions/stop", self.action_stop, ["POST"], "停止后台任务"),
            ("/actions/refresh-cookie", self.action_refresh_cookie, ["POST"], "刷新Cookie"),
        ]
        for route, handler, methods, desc in routes:
            reg(f"{PAGE_API_PREFIX}{route}", handler, methods, desc)
        logger.info(f"[BiliCompanion] 拓展页 API 已注册 {len(routes)} 个路由")

    # ------------------------------------------------------------------ 工具
    def _ok(self, data: Any = None, **extra):
        payload = {"ok": True, "data": data}
        if extra:
            payload["data"] = {**(payload["data"] or {}), **extra} if isinstance(payload["data"], dict) else extra
        return json_response(payload)

    def _fail(self, msg: str, status: int = 400):
        return error_response(msg, status_code=status)

    def _load(self, path, default):
        return self.plugin._load_json(path, default)

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _today_prefix(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ 路由
    async def status(self):
        """状态总览。"""
        p = self.plugin
        try:
            cookie_valid, cookie_info = await p.check_cookie()
        except Exception as e:
            cookie_valid, cookie_info = False, f"检查失败: {e}"
        mood, _ = p._get_today_mood()
        env = p._get_environment_status()
        memory = self._load(MEMORY_FILE, [])
        permanent = self._load(PERMANENT_MEMORY_FILE, [])
        profiles = self._load(USER_PROFILE_FILE, {})
        personality = self._load(PERSONALITY_FILE, {})
        watch_log = self._load(WATCH_LOG_FILE, [])
        dynamic_log = self._load(DYNAMIC_LOG_FILE, [])
        reply_log = self._load(REPLY_LOG_FILE, [])
        today = self._today_prefix()
        sync_state = self._load(MEMORY_SYNC_STATE_FILE, {})
        companion = p._get_memory_companion()

        # 记忆分级统计
        level_counts = {"today": 0, "recent": 0, "long_term": 0}
        aged_count = 0
        for m in memory:
            lv = m.get("level", "today")
            if lv in level_counts:
                level_counts[lv] += 1
            if m.get("aged"):
                aged_count += 1

        data = {
            "version": "1.0.0",
            "running": p._running,
            "cookie_valid": cookie_valid,
            "cookie_info": str(cookie_info),
            "now": self._now_str(),
            "mood": mood,
            "memory_count": len(memory),
            "memory_levels": level_counts,
            "aged_count": aged_count,
            "permanent_count": len(permanent),
            "profile_count": len(profiles),
            "personality_version": personality.get("version", 0),
            "personality_last_evolve": personality.get("last_evolve", "从未"),
            "today_watched": len([l for l in watch_log if l.get("time", "").startswith(today)]),
            "today_dynamic": len([l for l in dynamic_log if l.get("time", "").startswith(today)]),
            "today_replies": len([l for l in reply_log if l.get("time", "").startswith(today)]),
            "total_watched": len(watch_log),
            "total_dynamic": len(dynamic_log),
            "total_replies": len(reply_log),
            "features": {
                "reply": self.cfg.get("ENABLE_REPLY", True),
                "affection": self.cfg.get("ENABLE_AFFECTION", True),
                "mood": self.cfg.get("ENABLE_MOOD", True),
                "proactive": self.cfg.get("ENABLE_PROACTIVE", False),
                "dynamic": self.cfg.get("ENABLE_DYNAMIC", False),
                "bangumi": self.cfg.get("SPECIAL_FOLLOW_ENABLED", False),
                "private_messages": self.cfg.get("ENABLE_PRIVATE_MESSAGES", False),
                "personality_evolution": self.cfg.get("ENABLE_PERSONALITY_EVOLUTION", True),
                "llm_tools": self.cfg.get("ENABLE_LLM_TOOLS", True),
                "memory_sync_mode": self.plugin._memory_sync_mode(),
                "bili_share_parse": self.cfg.get("ENABLE_BILI_SHARE_PARSE", False),
            },
            "env": {
                "yt_dlp": env["external_commands"]["yt-dlp"],
                "ffmpeg": env["external_commands"]["ffmpeg"],
                "ffprobe": env["external_commands"]["ffprobe"],
                "video_provider": bool(env["llm"]["video_provider"]),
                "image_provider": bool(env["llm"]["image_provider"]),
                "web_search": env["features"]["web_search"],
            },
            "sync": {
                "enabled": self.plugin._is_sync_to_companion_enabled(),
                "mode": self.plugin._memory_sync_mode(),
                "mode_label": self.plugin._memory_sync_mode_label(),
                "local_writable": self.plugin._is_local_memory_writable(),
                "companion_available": companion is not None,
                "last_sync": sync_state.get("last_sync", ""),
                "synced_count": sync_state.get("synced_count", 0),
            },
            "owner_mid": self.cfg.get("OWNER_MID", ""),
        }
        return self._ok(data)

    async def memory_list(self):
        """记忆列表，支持分级筛选与分页。"""
        level = (request.query.get("level", "") or "").strip()
        q = (request.query.get("q", "") or "").strip()
        try:
            limit = int(request.query.get("limit", 100) or 100)
        except ValueError:
            limit = 100
        try:
            offset = int(request.query.get("offset", 0) or 0)
        except ValueError:
            offset = 0
        memory = self._load(MEMORY_FILE, [])
        items = []
        for m in memory:
            if level and m.get("level", "today") != level:
                continue
            if q:
                text = (m.get("text", "") or "") + " " + (m.get("content", "") or "")
                if q.lower() not in text.lower():
                    continue
            items.append({
                "id": m.get("id", ""),
                "level": m.get("level", "today"),
                "text": m.get("text", "") or m.get("content", ""),
                "time": m.get("time", ""),
                "user_id": m.get("user_id", ""),
                "username": m.get("username", ""),
                "aged": bool(m.get("aged", False)),
                "type": m.get("type", ""),
            })
        total = len(items)
        items.sort(key=lambda x: x.get("time", ""), reverse=True)
        items = items[offset: offset + limit]
        return self._ok({"total": total, "items": items})

    async def permanent_memory_list(self):
        """永久记忆列表。"""
        try:
            limit = int(request.query.get("limit", 100) or 100)
        except ValueError:
            limit = 100
        items = self._load(PERMANENT_MEMORY_FILE, [])
        items = items[:limit]
        return self._ok({"total": len(self._load(PERMANENT_MEMORY_FILE, [])), "items": items})

    async def affection_list(self):
        """好感度列表。"""
        affection = self._load(AFFECTION_FILE, {})
        items = []
        for uid, score in affection.items():
            items.append({"uid": str(uid), "score": score, "level": self._affection_level(score)})
        items.sort(key=lambda x: x["score"], reverse=True)
        return self._ok({"total": len(items), "items": items})

    @staticmethod
    def _affection_level(score):
        if score >= 80:
            return "special"
        if score >= 60:
            return "close"
        if score >= 30:
            return "friend"
        if score >= 10:
            return "normal"
        if score > 0:
            return "stranger"
        return "cold"

    async def profiles_list(self):
        """用户画像列表。"""
        profiles = self._load(USER_PROFILE_FILE, {})
        items = []
        for uid, p in profiles.items():
            items.append({
                "uid": str(uid),
                "username": p.get("username", ""),
                "summary": p.get("summary", "") or p.get("impression", ""),
                "tags": p.get("tags", []),
                "updated_at": p.get("updated_at", ""),
            })
        items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return self._ok({"total": len(items), "items": items})

    async def personality_detail(self):
        """性格演化详情。"""
        personality = self._load(PERSONALITY_FILE, {})
        return self._ok(personality)

    async def watch_log(self):
        """看视频日志。"""
        try:
            limit = int(request.query.get("limit", 50) or 50)
        except ValueError:
            limit = 50
        log = self._load(WATCH_LOG_FILE, [])
        total = len(log)
        items = log[:limit]
        return self._ok({"total": total, "items": items})

    async def dynamic_log(self):
        """动态日志。"""
        try:
            limit = int(request.query.get("limit", 50) or 50)
        except ValueError:
            limit = 50
        log = self._load(DYNAMIC_LOG_FILE, [])
        total = len(log)
        items = log[:limit]
        return self._ok({"total": total, "items": items})

    async def reply_log(self):
        """回复日志。"""
        try:
            limit = int(request.query.get("limit", 50) or 50)
        except ValueError:
            limit = 50
        log = self._load(REPLY_LOG_FILE, [])
        total = len(log)
        items = log[:limit]
        return self._ok({"total": total, "items": items})

    async def bangumi_log(self):
        """番剧日志。"""
        try:
            limit = int(request.query.get("limit", 50) or 50)
        except ValueError:
            limit = 50
        log = self._load(BANGUMI_WATCH_LOG_FILE, [])
        total = len(log)
        items = log[:limit]
        return self._ok({"total": total, "items": items})

    async def schedule(self):
        """今日计划。"""
        schedule = self.plugin._get_schedule_snapshot()
        return self._ok(schedule)

    async def mood_detail(self):
        """心情详情。"""
        mood, mood_text = self.plugin._get_today_mood()
        mood_data = self._load(MOOD_FILE, {})
        return self._ok({"mood": mood, "mood_text": mood_text, "history": mood_data})

    async def bindings_list(self):
        """QQ-B站绑定列表。"""
        bindings = self._load(BINDING_FILE, {})
        items = [{"qq_id": k, "bili_uid": v} for k, v in bindings.items()]
        return self._ok({"total": len(items), "items": items})

    async def sync_status(self):
        """memory_companion 同步状态。"""
        p = self.plugin
        sync_state = self._load(MEMORY_SYNC_STATE_FILE, {})
        companion = p._get_memory_companion()
        data = {
            "enabled": p._is_sync_to_companion_enabled(),
            "mode": p._memory_sync_mode(),
            "mode_label": p._memory_sync_mode_label(),
            "local_writable": p._is_local_memory_writable(),
            "recall_enabled": p._is_recall_enabled(),
            "companion_available": companion is not None,
            "bridge_available": p._get_companion_bridge() is not None,
            "last_sync": sync_state.get("last_sync", ""),
            "synced_count": sync_state.get("synced_count", 0),
            "now": self._now_str(),
        }
        return self._ok(data)

    async def sync_run(self):
        """手动触发一次同步（写入一条测试事件）。"""
        p = self.plugin
        if not p._is_sync_to_companion_enabled():
            return self._fail(f"当前记忆模式为「{p._memory_sync_mode_label()}」，未启用同步", 400)
        if not p._get_memory_companion():
            return self._fail("memory_companion 插件不可用", 400)
        try:
            ok = await p._sync_to_memory_companion(
                "comment_replied",
                text=f"[手动测试] WebUI 触发同步 {self._now_str()}",
                user_id=str(self.cfg.get("DEDE_USER_ID", "")),
                username="webui_admin",
            )
        except Exception as e:
            return self._fail(f"同步失败: {e}", 500)
        if ok:
            state = p._load_sync_state()
            state["last_sync"] = self._now_str()
            state["synced_count"] = state.get("synced_count", 0) + 1
            p._save_sync_state(state)
            return self._ok({"synced": True, "last_sync": state["last_sync"]})
        return self._fail("同步未成功（接口不可用或被拒绝）", 500)

    async def config_detail(self):
        """插件配置概览（脱敏）。"""
        cfg = dict(self.cfg) if self.cfg else {}
        # 脱敏：隐藏 cookie / token 类字段
        sensitive_keys = {"COOKIE", "SESSDATA", "BILI_JCT", "DEDE_USER_ID", "BUVID3"}
        safe = {}
        for k, v in cfg.items():
            if k.upper() in sensitive_keys:
                safe[k] = "***" if v else ""
            else:
                safe[k] = v
        return self._ok(safe)

    async def action_start(self):
        """启动后台任务。"""
        p = self.plugin
        if p._running:
            return self._ok({"running": True, "msg": "已在运行中"})
        try:
            await p._start_bot()
        except Exception as e:
            return self._fail(f"启动失败: {e}", 500)
        return self._ok({"running": p._running, "msg": "已启动"})

    async def action_stop(self):
        """停止后台任务。"""
        p = self.plugin
        if not p._running:
            return self._ok({"running": False, "msg": "未在运行"})
        try:
            await p._stop_bot()
        except Exception as e:
            return self._fail(f"停止失败: {e}", 500)
        return self._ok({"running": p._running, "msg": "已停止"})

    async def action_refresh_cookie(self):
        """刷新 Cookie。"""
        p = self.plugin
        if not self.cfg.get("COOKIE_AUTO_REFRESH", True):
            return self._fail("Cookie 自动刷新已关闭", 400)
        try:
            ok, msg = await p.refresh_cookie()
        except Exception as e:
            return self._fail(f"刷新失败: {e}", 500)
        return self._ok({"ok": ok, "msg": msg})

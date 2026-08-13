"""双轨记忆同步：将B站关键事件同步到 memory_companion 插件。"""
import asyncio
from datetime import datetime
from astrbot.api import logger
from .config import MEMORY_COMPANION_PLUGIN_NAME, MEMORY_SYNC_STATE_FILE

# ── 记忆模式常量 ──
MEMORY_MODE_STANDALONE = "standalone"  # 仅本地B站记忆，不同步
MEMORY_MODE_DUAL = "dual"              # 本地 + 同步到 memory_companion（默认）
MEMORY_MODE_COMPANION = "companion"    # 优先 memory_companion，本地仅缓存
_MEMORY_MODE_VALID = {MEMORY_MODE_STANDALONE, MEMORY_MODE_DUAL, MEMORY_MODE_COMPANION}

# 模式中文标签
MEMORY_MODE_LABELS = {
    MEMORY_MODE_STANDALONE: "独立模式（仅本地）",
    MEMORY_MODE_DUAL: "双轨模式（本地+同步）",
    MEMORY_MODE_COMPANION: "伴侣模式（优先memory_companion）",
}


class MemorySyncMixin:
    """提供与 memory_companion 的双轨记忆同步能力，支持三种记忆模式可选。"""

    def _get_memory_companion(self):
        """获取 memory_companion 插件实例。"""
        try:
            return self.context.get_registered_star(MEMORY_COMPANION_PLUGIN_NAME)
        except Exception:
            return None

    def _memory_sync_mode(self):
        """返回当前记忆模式（带向后兼容迁移）。

        旧配置 ENABLE_MEMORY_SYNC(bool) 会自动映射：
            True  → dual
            False → standalone
        """
        # 新配置优先
        mode = str(self.config.get("MEMORY_SYNC_MODE", "") or "").strip().lower()
        if mode in _MEMORY_MODE_VALID:
            return mode
        # 向后兼容：旧 bool 开关
        if "ENABLE_MEMORY_SYNC" in self.config:
            return MEMORY_MODE_DUAL if self.config.get("ENABLE_MEMORY_SYNC", True) else MEMORY_MODE_STANDALONE
        return MEMORY_MODE_DUAL

    def _memory_sync_mode_label(self):
        """返回当前记忆模式的中文标签。"""
        return MEMORY_MODE_LABELS.get(self._memory_sync_mode(), MEMORY_MODE_LABELS[MEMORY_MODE_DUAL])

    def _is_sync_to_companion_enabled(self):
        """是否需要同步写入 memory_companion（dual/companion 模式为 True）。"""
        return self._memory_sync_mode() in (MEMORY_MODE_DUAL, MEMORY_MODE_COMPANION)

    def _is_local_memory_writable(self):
        """是否主动写入本地B站记忆（standalone/dual 模式为 True，companion 模式为 False）。"""
        return self._memory_sync_mode() in (MEMORY_MODE_STANDALONE, MEMORY_MODE_DUAL)

    async def _sync_to_memory_companion(self, event_type, *, text, user_id="", username="", extra=None):
        """将一条事件同步写入 memory_companion。

        Args:
            event_type: 事件类型 (video_watched/comment_replied/dynamic_posted/bangumi_watched/affection_changed/private_message)
            text: 事件文本描述
            user_id: 相关用户ID
            username: 相关用户名
            extra: 额外元数据
        """
        if not self._is_sync_to_companion_enabled():
            return False
        companion = self._get_memory_companion()
        if not companion:
            return False
        try:
            # 尝试通过 memory_companion 的 bridge 接口写入
            bridge = getattr(companion, "bridge", None) or getattr(companion, "_bridge", None)
            if bridge and hasattr(bridge, "submit_emotion_event"):
                event = self._build_sync_event(event_type, text, user_id, username, extra)
                await bridge.submit_emotion_event(event)
                return True
            # 退回：尝试通过 memory_companion 的 remember 工具接口
            memory_api = getattr(companion, "memory_api", None) or getattr(companion, "_memory_api", None)
            if memory_api and hasattr(memory_api, "record"):
                await memory_api.record(
                    text,
                    user_id=user_id or "bili_bot",
                    username=username,
                    source="bilibili",
                    memory_type="chat",
                    level="today",
                    importance=7,
                    extra=extra or {},
                )
                return True
            # 再退回：直接调用 LLM 工具 memory_companion_remember
            # 但这需要 LLM 上下文，不适合后台调用，所以只记录日志
            logger.debug("[BiliCompanion] memory_companion 无可用同步接口，跳过")
            return False
        except Exception as e:
            logger.debug(f"[BiliCompanion] 同步到 memory_companion 失败: {e}")
            return False

    def _build_sync_event(self, event_type, text, user_id, username, extra):
        """构建同步事件对象。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        return {
            "producer_plugin": "astrbot_plugin_bili_ai_companion",
            "origin_kind": "interaction",
            "platform": "bilibili",
            "bot_id": str(self.config.get("DEDE_USER_ID", "")),
            "scope": "private",
            "session_id": f"bili:{event_type}:{now}",
            "actor_ref": {"kind": "bilibili_user", "id": str(user_id), "role": "user"},
            "target_ref": {"kind": "bot", "id": str(self.config.get("DEDE_USER_ID", "")), "role": "bot"},
            "event_type": self._map_event_type(event_type),
            "intensity": 50.0,
            "confidence": 0.8,
            "occurred_at": now,
            "status": "observed",
            "dedupe_key": f"bili:{event_type}:{user_id}:{now}",
            "payload": {"text": text, "username": username, "extra": extra or {}},
        }

    @staticmethod
    def _map_event_type(event_type):
        """将B站事件类型映射到 memory_companion 情绪事件类型。"""
        mapping = {
            "video_watched": "play",
            "comment_replied": "play",
            "dynamic_posted": "play",
            "bangumi_watched": "play",
            "affection_changed": "intimacy",
            "private_message": "play",
        }
        return mapping.get(event_type, "neutral")

    # ══════════════════════════════════════
    #  读取 memory_companion 记忆（跨平台共同记忆）
    # ══════════════════════════════════════

    def _get_companion_bridge(self):
        """获取 memory_companion 的 bridge 对象（用于读取记忆）。"""
        companion = self._get_memory_companion()
        if not companion:
            return None
        # 优先取 bridge 属性（memory_companion main.py 中 self.memory_companion = MemoryCompanionBridge(...)）
        bridge = getattr(companion, "memory_companion", None) or getattr(companion, "bridge", None) or getattr(companion, "_bridge", None)
        return bridge

    def _is_recall_enabled(self):
        """是否启用从 memory_companion 读取记忆（dual/companion 模式为 True）。"""
        return self._is_sync_to_companion_enabled()

    def _build_companion_session_context(self, *, user_id="", username="", scope="private", query_text="", group_id="", group_name=""):
        """构建传给 memory_companion bridge 的 session_context 字典。

        Args:
            user_id: B站用户UID
            username: B站用户名
            scope: "private"(私信/评论线) 或 "group"(公开评论区，按oid聚合)
            query_text: 当前消息文本（bridge会用它做语义检索）
            group_id: 评论区的oid（公开场景作为会话隔离维度）
            group_name: 评论区标题/视频标题

        Note:
            私聊 session_id 使用 "bl:" 前缀（bl=bilibili），
            以便 memory_companion 区分B站私聊与其他平台私聊会话。
        """
        bot_mid = str(self.config.get("DEDE_USER_ID", "") or "")
        uid = str(user_id or "")
        if scope == "private":
            # 私聊加 "bl" 前缀，区分B站私聊与其他平台私聊
            session_id = f"bl:private:{uid}"
        else:
            # 公开评论区按 oid 聚合
            session_id = f"bili:{scope}:{uid}:{group_id}" if group_id else f"bili:{scope}:{uid}"
        return {
            "session_id": session_id,
            "scope": scope,
            "platform": "bilibili",
            "user_id": uid,
            "user_name": str(username or ""),
            "group_id": str(group_id or ""),
            "group_name": str(group_name or ""),
            "bot_id": bot_mid,
            "message_id": "",
            "message_text": str(query_text or "")[:1400],
        }

    async def _recall_from_memory_companion(self, query_text, *, user_id="", username="", scope="private", group_id="", group_name="", top_k=6, max_chars=1500):
        """从 memory_companion 读取相关记忆，返回可直接注入LLM的文本块。

        Args:
            query_text: 用于语义检索的查询文本（通常是当前用户消息）
            user_id: B站用户UID（用于会话隔离与关系感知）
            username: B站用户名
            scope: "private" 或 "group"
            group_id: 评论区oid（公开场景）
            group_name: 视频标题
            top_k: 检索记忆条数上限
            max_chars: 注入文本字符上限

        Returns:
            str: 格式化好的记忆文本块（已带【跨平台共同记忆】标题），失败返回空字符串
        """
        if not self._is_recall_enabled():
            return ""
        if not query_text or not str(query_text).strip():
            return ""
        bridge = self._get_companion_bridge()
        if not bridge:
            return ""
        try:
            session_ctx = self._build_companion_session_context(
                user_id=user_id, username=username, scope=scope,
                query_text=query_text, group_id=group_id, group_name=group_name,
            )
            # 优先使用 compose_injection：直接返回可注入LLM的文本
            compose = getattr(bridge, "compose_injection", None)
            if callable(compose):
                injection = await compose(
                    query_text,
                    session_context=session_ctx,
                    top_k=top_k,
                    max_chars=max_chars,
                )
                if injection and str(injection).strip():
                    return self._wrap_companion_injection(injection)
            # 退回：使用 search 获取原始记忆列表，自行格式化
            search_fn = getattr(bridge, "search", None)
            if callable(search_fn):
                results = await search_fn(
                    query_text,
                    session_context=session_ctx,
                    top_k=top_k,
                )
                if results and isinstance(results, list):
                    formatted = self._format_companion_search_results(results, max_chars)
                    if formatted:
                        return self._wrap_companion_injection(formatted)
            logger.debug("[BiliCompanion] memory_companion bridge 无可用的记忆读取接口")
            return ""
        except Exception as e:
            logger.debug(f"[BiliCompanion] 从 memory_companion 读取记忆失败: {e}")
            return ""

    @staticmethod
    def _wrap_companion_injection(text):
        """给 memory_companion 返回的记忆文本加上统一的标题与说明。"""
        text = str(text or "").strip()
        if not text:
            return ""
        return (
            "【跨平台共同记忆（来自memory_companion）】\n"
            "以下是你和其他平台（如QQ）与该用户/相关话题的跨平台共同记忆。\n"
            "这些是次要参考，不是当前B站对话的一部分；请自行判断相关性，无关的忽略。\n"
            f"{text}"
        )

    @staticmethod
    def _format_companion_search_results(results, max_chars=1500):
        """把 bridge.search() 返回的记忆列表格式化为文本。"""
        lines = []
        total = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            # memory_companion serialize_memory 的常见字段
            text = str(item.get("text") or item.get("content") or item.get("summary") or "").strip()
            if not text:
                continue
            score = item.get("score", "")
            occurred_at = item.get("occurred_at") or item.get("time") or item.get("created_at") or ""
            scope = item.get("scope") or ""
            meta_parts = []
            if occurred_at:
                meta_parts.append(str(occurred_at)[:16])
            if scope:
                meta_parts.append(f"范围:{scope}")
            if score != "":
                meta_parts.append(f"相关度:{score}")
            meta = f"（{'，'.join(meta_parts)}）" if meta_parts else ""
            line = f"- {text}{meta}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    async def _recall_companion_for_context(self, query_text, *, user_id="", username="", scope="private", group_id="", group_name=""):
        """便捷封装：读取 memory_companion 记忆，用于 _build_memory_context 注入。

        与 _recall_from_memory_companion 相同，但额外做 mood 注入。
        """
        mood, _ = "", ""
        try:
            mood, _ = self._get_today_mood()
        except Exception:
            pass
        return await self._recall_from_memory_companion(
            query_text,
            user_id=user_id, username=username, scope=scope,
            group_id=group_id, group_name=group_name,
            top_k=6, max_chars=1500,
        )

    def _load_sync_state(self):
        """加载同步状态。"""
        return self._load_json(MEMORY_SYNC_STATE_FILE, {"last_sync": "", "synced_count": 0})

    def _save_sync_state(self, state):
        """保存同步状态。"""
        self._save_json(MEMORY_SYNC_STATE_FILE, state)

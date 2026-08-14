"""B站 API 交互：Cookie管理、WBI签名、扫码登录、评论、视频信息、互动。"""
import json
import re
import time
import hashlib
import uuid
import os
import aiohttp
from functools import reduce
from urllib.parse import urlparse, parse_qs
from astrbot.api import logger
from .config import (
    BILI_COOKIE_CONFIRM_URL, BILI_COOKIE_INFO_URL, BILI_COOKIE_REFRESH_URL,
    BILI_DYNAMIC_IMAGE_URL, BILI_DYNAMIC_TEXT_URL, BILI_NAV_URL,
    BILI_PRIVATE_MSG_SEND_URL, BILI_PRIVATE_SESSIONS_URL, BILI_PRIVATE_MESSAGES_URL,
    BILI_NOTIFY_URL, BILI_AT_NOTIFY_URL,
    BILI_QR_GENERATE_URL, BILI_QR_POLL_URL, BILI_REPLY_URL,
    BILI_RSA_PUBLIC_KEY, BILI_UPLOAD_IMAGE_URL,
    MIXIN_KEY_ENC_TAB, USER_AGENT, TEMP_VIDEO_DIR,
)


class BilibiliAPIMixin:
    """所有 B站 HTTP API 调用。"""

    # ── Cookie / Header ──
    def _has_cookie(self):
        """检测当前是否已配置 SESSDATA。"""
        return bool(self.config.get("SESSDATA", ""))

    def _headers(self):
        """构造带 Cookie 的请求头。"""
        cookie_parts = (
            f"SESSDATA={self.config.get('SESSDATA', '')}; "
            f"bili_jct={self.config.get('BILI_JCT', '')}; "
            f"DedeUserID={self.config.get('DEDE_USER_ID', '')}"
        )
        buvid3 = self.config.get("BUVID3", "")
        if buvid3:
            cookie_parts += f"; buvid3={buvid3}"
        buvid4 = self.config.get("BUVID4", "")
        if buvid4:
            cookie_parts += f"; buvid4={buvid4}"
        return {
            "Cookie": cookie_parts,
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com",
            "Accept-Encoding": "gzip, deflate",
        }

    # ── B站 API 请求（_http_get / _http_post 的语义化别名）──
    async def _bili_api_get(self, url, headers=None, params=None, timeout=10, retries=2):
        """B站 API GET 请求，转发到通用 _http_get。"""
        return await self._http_get(
            url, headers=headers, params=params, timeout=timeout, retries=retries,
        )

    async def _bili_api_post(self, url, headers=None, data=None, timeout=10, retries=2):
        """B站 API POST 请求，转发到通用 _http_post。"""
        return await self._http_post(
            url, headers=headers, data=data, timeout=timeout, retries=retries,
        )

    # ── buvid3 设备标识 ──
    async def _ensure_buvid(self):
        """获取并缓存 buvid3，减少风控触发概率"""
        if self.config.get("BUVID3", ""):
            return
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/frontend/finger/spi")
            if d.get("code") == 0:
                buvid3 = d["data"].get("b_3", "")
                buvid4 = d["data"].get("b_4", "")
                if buvid3:
                    self.config["BUVID3"] = buvid3
                    if buvid4:
                        self.config["BUVID4"] = buvid4
                    self.config.save_config()
                    logger.info(f"[BiliBot] 已获取 buvid3: {buvid3[:16]}...")
        except Exception as e:
            logger.warning(f"[BiliBot] 获取 buvid3 失败（不影响基本功能）: {e}")

    # ── Cookie 检查 / 刷新 ──
    async def check_cookie(self):
        s = self.config.get("SESSDATA", "")
        if not s:
            return False, "SESSDATA 为空"
        try:
            d, _ = await self._http_get(BILI_NAV_URL)
            if d["code"] == 0:
                return True, f"✅ {d['data'].get('uname', '?')} (UID:{d['data'].get('mid', '')}) LV{d['data'].get('level_info', {}).get('current_level', 0)}"
            return False, f"❌ Cookie 已失效 (code:{d['code']})"
        except Exception as e:
            return False, f"❌ 检查失败: {e}"

    async def check_need_refresh(self):
        # 返回 (True, msg)=需要刷新 / (False, msg)=确认无需刷新 / (None, msg)=检查出错，无法判断
        try:
            d, _ = await self._http_get(BILI_COOKIE_INFO_URL, params={"csrf": self.config.get("BILI_JCT", "")})
            if d["code"] != 0:
                return None, f"检查失败: {d.get('message', '')}"
            return (True, "需要刷新") if d["data"].get("refresh", False) else (False, "Cookie 仍然有效")
        except Exception as e:
            return None, f"检查出错: {e}"

    def _generate_correspond_path(self, ts):
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes, serialization
        pk = serialization.load_pem_public_key(BILI_RSA_PUBLIC_KEY.encode())
        return pk.encrypt(
            f"refresh_{ts}".encode(),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        ).hex()

    async def refresh_cookie(self):
        rt = self.config.get("REFRESH_TOKEN", "")
        if not rt:
            return False, "没有 REFRESH_TOKEN"
        bjct = self.config.get("BILI_JCT", "")
        if not self.config.get("SESSDATA", ""):
            return False, "SESSDATA 为空"
        try:
            need, msg = await self.check_need_refresh()
            if need is None:
                return False, f"无法确认是否需要刷新（{msg}），本次跳过"
            if not need:
                return True, msg
            cp = self._generate_correspond_path(int(time.time() * 1000))
            html, _ = await self._http_get_text(f"https://www.bilibili.com/correspond/1/{cp}")
            m = re.search(r'<div\s+id="1-name"\s*>([^<]+)</div>', html)
            if not m:
                return False, "无法提取 refresh_csrf"
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    BILI_COOKIE_REFRESH_URL,
                    headers=self._headers(),
                    data={"csrf": bjct, "refresh_csrf": m.group(1).strip(), "source": "main_web", "refresh_token": rt},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    result = await resp.json(content_type=None)
                    if result["code"] != 0:
                        return False, f"刷新失败: {result.get('message', result['code'])}"
                    updates = {}
                    nrt = result["data"].get("refresh_token", "")
                    if nrt:
                        updates["REFRESH_TOKEN"] = nrt
                    for k, cookie in resp.cookies.items():
                        if k == "SESSDATA":
                            updates["SESSDATA"] = cookie.value
                        elif k == "bili_jct":
                            updates["BILI_JCT"] = cookie.value
                        elif k == "DedeUserID":
                            updates["DEDE_USER_ID"] = cookie.value
            if "SESSDATA" not in updates:
                return False, "刷新响应中未找到新 SESSDATA"
            try:
                ch = dict(self._headers())
                ch["Cookie"] = f"SESSDATA={updates['SESSDATA']}; bili_jct={updates.get('BILI_JCT', bjct)}"
                await self._http_post(BILI_COOKIE_CONFIRM_URL, headers=ch, data={"csrf": updates.get("BILI_JCT", bjct), "refresh_token": rt})
            except Exception:
                pass
            for k, v in updates.items():
                self.config[k] = v
            self.config.save_config()
            return True, "✅ Cookie 刷新成功！"
        except Exception as e:
            return False, f"刷新出错: {e}"

    async def _check_and_refresh_cookie(self):
        """启动时检查 Cookie 有效性，失效则按配置自动刷新。"""
        valid, info = await self.check_cookie()
        if valid:
            logger.info(f"[BiliBot] Cookie OK: {info}")
            return
        logger.warning(f"[BiliBot] Cookie 失效: {info}")
        if self.config.get("COOKIE_AUTO_REFRESH", True):
            ok, msg = await self.refresh_cookie()
            logger.info(f"[BiliBot] 刷新{'成功' if ok else '失败'}: {msg}")

    # ── WBI 签名 ──
    async def _get_wbi_keys(self):
        # wbi key 官方约一天一换，缓存 6 小时避免每次签名都请求 nav 接口
        cached = getattr(self, "_wbi_keys_cache", None)
        if cached and time.time() - cached[0] < 6 * 3600:
            return cached[1], cached[2]
        d, _ = await self._http_get(BILI_NAV_URL)
        d = d["data"]["wbi_img"]
        ik = d["img_url"].rsplit("/", 1)[1].split(".")[0]
        sk = d["sub_url"].rsplit("/", 1)[1].split(".")[0]
        self._wbi_keys_cache = (time.time(), ik, sk)
        return ik, sk

    def _get_mixin_key(self, orig):
        return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]

    def _enc_wbi(self, orig):
        """WBI 混淆密钥生成（_get_mixin_key 的语义化别名）。"""
        return self._get_mixin_key(orig)

    async def sign_wbi_params(self, params):
        try:
            ik, sk = await self._get_wbi_keys()
            mk = self._get_mixin_key(ik + sk)
            params["wts"] = int(time.time())
            params = dict(sorted(params.items()))
            params["w_rid"] = hashlib.md5(("&".join(f"{k}={v}" for k, v in params.items()) + mk).encode()).hexdigest()
            return params
        except Exception as e:
            logger.warning(f"[BiliBot] WBI 签名失败，将以未签名参数请求（接口大概率返回 -352）: {e}")
            return params

    async def _sign_wbi(self, params):
        """对参数进行 WBI 签名（sign_wbi_params 的语义化别名）。"""
        return await self.sign_wbi_params(params)

    async def _wbi_get(self, url, params):
        """签名并请求 wbi 接口；遇 -352 时清缓存、用原始参数重新签名并立即重试一次。"""
        raw = dict(params)
        d, r = await self._http_get(url, params=await self.sign_wbi_params(dict(raw)))
        if isinstance(d, dict) and d.get("code") == -352:
            self._wbi_keys_cache = None
            logger.warning("[BiliBot] wbi 接口返回 -352，重新签名重试一次")
            d, r = await self._http_get(url, params=await self.sign_wbi_params(dict(raw)))
        return d, r

    # ── 扫码登录 ──
    async def _qr_login_generate(self):
        try:
            d, _ = await self._http_get(BILI_QR_GENERATE_URL, headers={"User-Agent": USER_AGENT})
            if d["code"] == 0:
                return d["data"]["url"], d["data"]["qrcode_key"]
        except Exception as e:
            logger.error(f"生成二维码失败: {e}")
        return None, None

    async def _generate_qrcode(self):
        """生成扫码登录二维码，返回 (qr_url, qrcode_key)（_qr_login_generate 别名）。"""
        return await self._qr_login_generate()

    async def _qr_login_poll(self, qrcode_key):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    BILI_QR_POLL_URL,
                    params={"qrcode_key": qrcode_key},
                    headers={"User-Agent": USER_AGENT},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    d_full = await resp.json(content_type=None)
                    d = d_full["data"]
                    code = d["code"]
                    mm = {0: "登录成功", 86038: "二维码已失效", 86090: "已扫码，请在手机上确认", 86101: "等待扫码中..."}
                    cookies = {}
                    if code == 0:
                        url = d.get("url", "")
                        rt = d.get("refresh_token", "")
                        if url:
                            p = parse_qs(urlparse(url).query)
                            cookies = {"SESSDATA": p.get("SESSDATA", [""])[0], "bili_jct": p.get("bili_jct", [""])[0], "DedeUserID": p.get("DedeUserID", [""])[0], "REFRESH_TOKEN": rt}
                        for k, cookie in resp.cookies.items():
                            if k in ("SESSDATA", "bili_jct", "DedeUserID"):
                                cookies[k] = cookie.value
                    return code, mm.get(code, f"未知({code})"), cookies
        except Exception as e:
            return -1, f"轮询失败: {e}", {}

    async def _poll_qrcode(self, qrcode_key):
        """轮询扫码登录状态（_qr_login_poll 别名）。"""
        return await self._qr_login_poll(qrcode_key)

    async def _login_qrcode(self, max_wait=180, interval=2):
        """完整的扫码登录流程：生成二维码 → 轮询直到成功/超时 → 写回 Cookie。
        返回 (success: bool, message: str, cookies: dict)。"""
        qr_url, qrcode_key = await self._qr_login_generate()
        if not qr_url or not qrcode_key:
            return False, "生成二维码失败", {}
        logger.info("[BiliBot] 扫码登录二维码已生成，等待扫描...")
        start = time.time()
        while time.time() - start < max_wait:
            code, msg, cookies = await self._qr_login_poll(qrcode_key)
            if code == 0 and cookies:
                for k, v in cookies.items():
                    if k == "DedeUserID":
                        self.config["DEDE_USER_ID"] = v
                    elif k == "REFRESH_TOKEN":
                        self.config["REFRESH_TOKEN"] = v
                    else:
                        self.config[k] = v
                self.config.save_config()
                logger.info("[BiliBot] ✅ 扫码登录成功")
                return True, msg, cookies
            if code == 86038:
                return False, msg, {}
            logger.info(f"[BiliBot] 扫码登录状态: {msg}")
            await __import__("asyncio").sleep(interval)
        return False, "扫码登录超时", {}

    # ── 评论 ──
    async def _send_reply(self, oid, rpid, reply_type, content):
        msg = (content or "").strip()
        # B站不接受空内容 / 纯标点 / 纯符号（会报「不可发送单个标点符号」），
        # 至少要含一个文字或数字，否则提前拦掉，省一次必然失败的请求
        if not msg or not re.search(r'[0-9A-Za-z一-鿿]', msg):
            logger.warning(f"[BiliBot] 回复内容无效（空/纯标点），跳过发送：{content!r}")
            return False
        try:
            d, _ = await self._http_post(
                BILI_REPLY_URL,
                data={"oid": oid, "type": reply_type, "root": rpid, "parent": rpid, "message": msg, "csrf": self.config.get("BILI_JCT", "")},
            )
            if d["code"] == 0:
                return True
            elif d["code"] == -101:
                logger.error("[BiliBot] SESSDATA 失效！")
            elif d["code"] == -111:
                logger.error("[BiliBot] bili_jct 错误！")
            else:
                logger.warning(f"[BiliBot] 回复失败: {d.get('message', d['code'])}")
            return False
        except Exception as e:
            logger.error(f"[BiliBot] 回复出错: {e}")
            return False

    def _strip_at_prefix(self, content):
        content = (content or "").strip()
        content = re.sub(r'^@[^ \t\n\r]+\s*', '', content)
        return content.strip()

    async def _send_comment(self, oid, comment_text, oid_type=1):
        try:
            d, _ = await self._http_post(
                BILI_REPLY_URL,
                data={"oid": oid, "type": oid_type, "message": comment_text, "csrf": self.config.get("BILI_JCT", "")},
            )
            code = d.get("code", -1)
            if code == 0:
                return True
            if code == -101:
                logger.warning("[BiliBot] 发评论失败：Cookie 已失效")
            elif code == -403 or code == 12015:
                logger.warning("[BiliBot] 发评论失败：访问被限制（可能触发风控）")
            elif code == 12002:
                logger.warning("[BiliBot] 发评论失败：评论区已关闭")
            elif code == 12025:
                logger.warning("[BiliBot] 发评论失败：评论内容包含敏感词")
            else:
                logger.warning(f"[BiliBot] 发评论失败({oid}): code={code} {d.get('message', '')}")
            return False
        except Exception as e:
            logger.error(f"[BiliBot] 发送评论异常: {e}")
            return False

    async def _like_comment(self, oid, rpid, reply_type=1, action=1):
        """点赞/取消点赞评论。action: 1=点赞 0=取消。"""
        try:
            d, _ = await self._http_post(
                "https://api.bilibili.com/x/v2/reply/like",
                data={
                    "oid": oid, "type": reply_type, "rpid": rpid,
                    "action": action, "csrf": self.config.get("BILI_JCT", ""),
                },
                retries=0,
            )
            code = d.get("code", -1) if isinstance(d, dict) else -1
            if code == 0:
                return True
            if code == -101:
                logger.warning("[BiliBot] 点赞评论失败：Cookie 已失效")
            elif code == -403:
                logger.warning("[BiliBot] 点赞评论失败：访问被限制（可能触发风控）")
            else:
                logger.warning(f"[BiliBot] 点赞评论失败({oid}/{rpid}): code={code} {d.get('message', '') if isinstance(d, dict) else ''}")
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 点赞评论异常({oid}/{rpid}): {e}")
            return False

    async def _get_unified_notifications(self, limit=20):
        """获取统一通知（回复 + @我），合并后按时间倒序返回前 limit 条。"""
        results = []
        try:
            # 回复我的
            d, _ = await self._http_get(
                BILI_NOTIFY_URL,
                params={"platform": "web", "build": 0, "web_location": "333.788"},
            )
            if isinstance(d, dict) and d.get("code") == 0:
                for item in ((d.get("data") or {}).get("items") or [])[:limit]:
                    results.append({
                        "type": "reply",
                        "mid": str(item.get("user", {}).get("mid", "")),
                        "uname": item.get("user", {}).get("uname", ""),
                        "content": item.get("item", {}).get("source_content", "") or item.get("item", {}).get("message", ""),
                        "oid": item.get("item", {}).get("oid", 0),
                        "rpid": item.get("item", {}).get("rp_id", 0) or item.get("item", {}).get("id", 0),
                        "reply_type": item.get("item", {}).get("type", 0) or item.get("item", {}).get("business", 0),
                        "time": item.get("item", {}).get("ctime", 0),
                    })
        except Exception as e:
            logger.debug(f"[BiliBot] 获取回复通知失败: {e}")
        try:
            # @我的
            d, _ = await self._http_get(
                BILI_AT_NOTIFY_URL,
                params={"platform": "web", "build": 0, "web_location": "333.789"},
            )
            if isinstance(d, dict) and d.get("code") == 0:
                for item in ((d.get("data") or {}).get("items") or [])[:limit]:
                    results.append({
                        "type": "at",
                        "mid": str(item.get("user", {}).get("mid", "")),
                        "uname": item.get("user", {}).get("uname", ""),
                        "content": item.get("item", {}).get("source_content", "") or item.get("item", {}).get("message", ""),
                        "oid": item.get("item", {}).get("oid", 0),
                        "rpid": item.get("item", {}).get("rp_id", 0) or item.get("item", {}).get("id", 0),
                        "reply_type": item.get("item", {}).get("type", 0) or item.get("item", {}).get("business", 0),
                        "time": item.get("item", {}).get("ctime", 0),
                    })
        except Exception as e:
            logger.debug(f"[BiliBot] 获取@通知失败: {e}")
        results.sort(key=lambda x: x.get("time", 0), reverse=True)
        return results[:limit]

    # ── 私信 ──
    async def _send_bili_private_payload(self, receiver_mid, msg_type, content, label="私信"):
        """发送 B站网页私信载荷；写操作不自动重试，避免重复发送。"""
        sender_mid = str(self.config.get("DEDE_USER_ID", "") or "").strip()
        receiver_mid = str(receiver_mid or "").strip()
        csrf = str(self.config.get("BILI_JCT", "") or "").strip()
        if not self._has_cookie() or not csrf or not sender_mid.isdigit():
            logger.warning(f"[BiliBot] 发送B站{label}失败：登录 Cookie、CSRF 或 Bot UID 不完整")
            return False
        if not receiver_mid.isdigit():
            logger.warning(f"[BiliBot] 发送B站{label}失败：接收 UID 未填写或格式错误")
            return False
        if sender_mid == receiver_mid:
            logger.warning(f"[BiliBot] 发送B站{label}失败：接收 UID 与 Bot UID 相同")
            return False
        if not isinstance(content, dict) or not content:
            logger.warning(f"[BiliBot] 发送B站{label}失败：消息内容为空")
            return False
        payload = {
            "msg[sender_uid]": sender_mid,
            "msg[receiver_id]": receiver_mid,
            "msg[receiver_type]": 1,
            "msg[msg_type]": int(msg_type),
            "msg[msg_status]": 0,
            "msg[dev_id]": str(uuid.uuid4()).upper(),
            "msg[timestamp]": int(time.time()),
            "msg[new_face_version]": 0,
            "msg[content]": json.dumps(
                content,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "csrf": csrf,
            "csrf_token": csrf,
            "from_firework": 0,
            "build": 0,
            "mobi_app": "web",
        }
        headers = {
            **self._headers(),
            "Origin": "https://message.bilibili.com",
            "Referer": "https://message.bilibili.com/",
        }
        try:
            result, _ = await self._http_post(
                BILI_PRIVATE_MSG_SEND_URL,
                headers=headers,
                data=payload,
                retries=0,
            )
            code = result.get("code", -1)
            if code == 0:
                return True
            logger.warning(
                f"[BiliBot] 发送B站{label}失败({receiver_mid}): "
                f"code={code} {result.get('message', '')}"
            )
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 发送B站{label}异常({receiver_mid}): {e}")
            return False

    async def _send_bili_private_message(self, receiver_mid, text):
        """通过 B站网页私信发送纯文本；写操作不自动重试，避免重复发送。"""
        message = str(text or "").strip()
        if not message:
            logger.warning("[BiliBot] 发送B站私信失败：消息为空")
            return False
        return await self._send_bili_private_payload(
            receiver_mid,
            1,
            {"content": message},
        )

    async def _send_private_msg(self, receiver_mid, text):
        """发送纯文本私信（_send_bili_private_message 的语义化别名）。"""
        return await self._send_bili_private_message(receiver_mid, text)

    async def _send_bili_private_video_share(self, receiver_mid, video_info):
        """通过 B站网页私信发送原生视频分享卡片。"""
        info = video_info if isinstance(video_info, dict) else {}
        bvid = str(info.get("bvid", "") or "").strip()
        aid = info.get("aid") or info.get("oid") or info.get("id")
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            aid = 0
        if not bvid or aid <= 0:
            logger.warning("[BiliBot] 发送B站视频私信失败：视频 BV 号或 aid 无效")
            return False
        content = {
            "author": str(info.get("owner_name", "") or ""),
            "headline": "",
            "id": aid,
            "source": 5,
            "thumb": str(info.get("pic", "") or ""),
            "title": str(info.get("title", "") or bvid),
            "bvid": bvid,
        }
        return await self._send_bili_private_payload(
            receiver_mid,
            7,
            content,
            label="视频私信",
        )

    async def _get_sessions(self, limit=20, session_type=None):
        """获取私信会话列表，返回会话数组。"""
        try:
            params = {
                "session_type": session_type if session_type is not None else 1,
                "group_fold": 1,
                "unfollow_filter": 0,
                "sort_rule": 2,
                "build": 0,
                "mobi_app": "web",
            }
            d, _ = await self._http_get(
                BILI_PRIVATE_SESSIONS_URL,
                params=params,
            )
            if not isinstance(d, dict) or d.get("code") != 0:
                logger.debug(f"[BiliBot] 获取私信会话失败: {d.get('code') if isinstance(d, dict) else type(d)}")
                return []
            sessions = (d.get("data") or {}).get("session_list") or []
            results = []
            for s in sessions[:limit]:
                results.append({
                    "talker_id": str(s.get("talker_id", "")),
                    "session_type": s.get("session_type", 1),
                    "last_msg": (s.get("last_msg") or {}).get("content", "") if isinstance(s.get("last_msg"), dict) else "",
                    "last_time": s.get("last_msg", {}).get("timestamp", 0) if isinstance(s.get("last_msg"), dict) else 0,
                    "unread": s.get("unread_count", 0),
                    "ustate": s.get("ustate", 0),
                })
            return results
        except Exception as e:
            logger.warning(f"[BiliBot] 获取私信会话异常: {e}")
            return []

    async def _fetch_session_msgs(self, talker_id, session_type=1, limit=20):
        """获取指定会话的消息记录，返回消息数组。"""
        try:
            sender_uid = str(self.config.get("DEDE_USER_ID", "") or "").strip()
            params = {
                "sender_uid": sender_uid,
                "receiver_id": str(talker_id),
                "talker_id": str(talker_id),
                "session_type": session_type,
                "size": limit,
                "build": 0,
                "mobi_app": "web",
            }
            d, _ = await self._http_get(
                BILI_PRIVATE_MESSAGES_URL,
                params=params,
            )
            if not isinstance(d, dict) or d.get("code") != 0:
                logger.debug(f"[BiliBot] 获取会话消息失败: {d.get('code') if isinstance(d, dict) else type(d)}")
                return []
            messages = (d.get("data") or {}).get("messages") or []
            results = []
            for m in messages:
                content_raw = m.get("content", "") or ""
                try:
                    content_obj = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
                    text = content_obj.get("content", "") if isinstance(content_obj, dict) else str(content_raw)
                except Exception:
                    text = str(content_raw)
                results.append({
                    "msg_id": m.get("msg_id", 0),
                    "msg_type": m.get("msg_type", 1),
                    "sender_uid": str(m.get("sender_uid", "")),
                    "receiver_id": str(m.get("receiver_id", "")),
                    "timestamp": m.get("timestamp", 0),
                    "content": text,
                })
            return results
        except Exception as e:
            logger.warning(f"[BiliBot] 获取会话消息异常: {e}")
            return []

    # ── 关注列表 ──
    async def get_followings(self, mid=None):
        target = mid or self.config.get("DEDE_USER_ID", "")
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/relation/followings", params={"vmid": target, "ps": 50, "pn": 1})
            if d["code"] == 0:
                return [i["mid"] for i in d.get("data", {}).get("list", [])]
        except Exception as e:
            logger.error(f"[BiliBot] 获取关注列表失败: {e}")
        return []

    # ── 视频信息 ──
    async def _oid_to_bvid(self, oid):
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/web-interface/view", params={"aid": oid})
            if d["code"] == 0:
                return d["data"].get("bvid", "")
        except Exception:
            pass
        return ""

    async def _get_video_info(self, oid):
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/web-interface/view", params={"aid": oid})
            if d["code"] == 0:
                v = d["data"]
                return {
                    "bvid": v.get("bvid", ""), "title": v.get("title", ""), "desc": v.get("desc", ""),
                    "owner_name": v.get("owner", {}).get("name", ""), "owner_mid": v.get("owner", {}).get("mid", ""),
                    "tname": v.get("tname", ""), "duration": v.get("duration", 0), "pic": v.get("pic", ""),
                    "cid": v.get("cid", 0),
                }
        except Exception as e:
            logger.error(f"[BiliBot] 获取视频信息失败：{e}")
        return None

    async def _get_video_subtitles(self, bvid, cid):
        """获取视频字幕文本"""
        if not bvid or not cid:
            return ""
        try:
            d, _ = await self._http_get(
                "https://api.bilibili.com/x/player/v2",
                params={"bvid": bvid, "cid": cid},
            )
            if not isinstance(d, dict) or d.get("code") != 0:
                return ""
            subtitles = (d.get("data") or {}).get("subtitle", {}).get("subtitles", [])
            if not subtitles:
                return ""
            # 优先中文字幕，并保留轨道信息供日志排查 B站自动字幕错配。
            selected_subtitle = None
            for s in subtitles:
                lan = s.get("lan", "")
                if "zh" in lan or "cn" in lan:
                    selected_subtitle = s
                    break
            if selected_subtitle is None and subtitles:
                selected_subtitle = subtitles[0]
            sub_url = (selected_subtitle or {}).get("subtitle_url", "")
            if not sub_url:
                return ""
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            # 获取字幕JSON
            sub_data, _ = await self._http_get(sub_url)
            if not isinstance(sub_data, dict):
                return ""
            body = sub_data.get("body", [])
            if not body:
                return ""
            # 拼接字幕文本，限制长度
            lines = [item.get("content", "") for item in body if item.get("content")]
            full_text = " ".join(lines)
            if len(full_text) > 2000:
                full_text = full_text[:2000] + "…（字幕过长已截断）"
            lan = (selected_subtitle or {}).get("lan", "?")
            subtitle_id = (selected_subtitle or {}).get("id_str") or (selected_subtitle or {}).get("id", "?")
            logger.info(
                f"[BiliBot] 📝 获取候选字幕: bvid={bvid} cid={cid} "
                f"lan={lan} id={subtitle_id} {len(lines)}条 {len(full_text)}字"
            )
            return full_text
        except Exception as e:
            logger.debug(f"[BiliBot] 字幕获取失败: {e}")
            return ""

    async def _get_video_tags(self, bvid):
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/tag/archive/tags", params={"bvid": bvid})
            if d["code"] == 0:
                return [t.get("tag_name", "") for t in d.get("data", []) if t.get("tag_name")]
        except Exception:
            pass
        return []

    async def _get_hot_comments(self, oid, limit=10):
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/v2/reply/main", params={"oid": oid, "type": 1, "mode": 3, "ps": limit})
            if d["code"] == 0:
                replies = d.get("data", {}).get("replies", []) or []
                return [r.get("content", {}).get("message", "")[:100] for r in replies if r.get("content", {}).get("message")]
        except Exception:
            pass
        return []

    async def _get_video_oid(self, bvid):
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid})
            if d.get("code") == 0:
                return d["data"]["aid"]
        except Exception:
            pass
        return None

    # ── 互动 ──
    async def _like_video(self, aid):
        try:
            d, _ = await self._http_post("https://api.bilibili.com/x/web-interface/archive/like", data={"aid": aid, "like": 1, "csrf": self.config.get("BILI_JCT", "")})
            code = d.get("code", -1)
            if code == 0:
                return True
            if code == -101:
                logger.warning("[BiliBot] 点赞失败：Cookie 已失效")
            elif code == -403:
                logger.warning("[BiliBot] 点赞失败：访问被限制（可能触发风控）")
            else:
                logger.warning(f"[BiliBot] 点赞失败({aid}): code={code} {d.get('message', '')}")
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 点赞异常({aid}): {e}")
            return False

    async def _coin_video(self, aid, num=1):
        try:
            d, _ = await self._http_post("https://api.bilibili.com/x/web-interface/coin/add", data={"aid": aid, "multiply": num, "select_like": 0, "csrf": self.config.get("BILI_JCT", "")})
            code = d.get("code", -1)
            if code == 0:
                return True
            if code == -101:
                logger.warning("[BiliBot] 投币失败：Cookie 已失效")
            elif code == -403:
                logger.warning("[BiliBot] 投币失败：访问被限制（可能触发风控）")
            else:
                logger.warning(f"[BiliBot] 投币失败({aid}): code={code} {d.get('message', '')}")
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 投币异常({aid}): {e}")
            return False

    async def _fav_video(self, aid):
        try:
            d, _ = await self._http_get("https://api.bilibili.com/x/v3/fav/folder/created/list-all", params={"up_mid": self.config.get("DEDE_USER_ID", ""), "type": 2})
            if d["code"] != 0:
                if d["code"] == -101:
                    logger.warning("[BiliBot] 获取收藏夹失败：Cookie 已失效")
                return False
            fav_list = d.get("data", {}).get("list") or []
            if not fav_list:
                logger.warning("[BiliBot] 收藏夹列表为空，无法收藏")
                return False
            fav_id = fav_list[0]["id"]
            d2, _ = await self._http_post("https://api.bilibili.com/x/v3/fav/resource/deal", data={"rid": aid, "type": 2, "add_media_ids": fav_id, "csrf": self.config.get("BILI_JCT", "")})
            code = d2.get("code", -1)
            if code == 0:
                return True
            logger.warning(f"[BiliBot] 收藏失败({aid}): code={code} {d2.get('message', '')}")
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 收藏异常({aid}): {e}")
            return False

    async def _follow_user(self, mid):
        try:
            d, _ = await self._http_post("https://api.bilibili.com/x/relation/modify", data={"fid": mid, "act": 1, "re_src": 11, "csrf": self.config.get("BILI_JCT", "")})
            code = d.get("code", -1)
            if code == 0:
                return True
            if code == -101:
                logger.warning("[BiliBot] 关注失败：Cookie 已失效")
            elif code == -403:
                logger.warning("[BiliBot] 关注失败：访问被限制（可能触发风控）")
            else:
                logger.warning(f"[BiliBot] 关注失败({mid}): code={code} {d.get('message', '')}")
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 关注异常({mid}): {e}")
            return False

    # ── 拉黑管理 ──
    async def _block_user(self, mid):
        """拉黑用户（act=5）。"""
        try:
            d, _ = await self._http_post(
                "https://api.bilibili.com/x/relation/modify",
                data={"fid": mid, "act": 5, "re_src": 11, "csrf": self.config.get("BILI_JCT", "")},
                retries=0,
            )
            if isinstance(d, dict) and d.get("code") == 0:
                return True
            if isinstance(d, dict):
                logger.warning(f"[BiliBot] 拉黑失败({mid}): code={d.get('code')} {d.get('message', '')}")
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 拉黑异常({mid}): {e}")
            return False

    async def _unblock_user(self, mid):
        """取消拉黑（act=6，转为普通关注；act=7 取消关注并移除拉黑）。"""
        try:
            d, _ = await self._http_post(
                "https://api.bilibili.com/x/relation/modify",
                data={"fid": mid, "act": 6, "re_src": 11, "csrf": self.config.get("BILI_JCT", "")},
                retries=0,
            )
            if isinstance(d, dict) and d.get("code") == 0:
                return True
            if isinstance(d, dict):
                logger.warning(f"[BiliBot] 取消拉黑失败({mid}): code={d.get('code')} {d.get('message', '')}")
            return False
        except Exception as e:
            logger.warning(f"[BiliBot] 取消拉黑异常({mid}): {e}")
            return False

    async def _get_blacklist(self, pn=1, ps=50):
        """获取黑名单列表。"""
        try:
            d, _ = await self._http_get(
                "https://api.bilibili.com/x/relation/blacks",
                params={"pn": pn, "ps": ps, "re_src": 11},
            )
            if not isinstance(d, dict) or d.get("code") != 0:
                logger.debug(f"[BiliBot] 获取黑名单失败: {d.get('code') if isinstance(d, dict) else type(d)}")
                return []
            return [
                {
                    "mid": str(item.get("mid", "")),
                    "uname": item.get("uname", ""),
                    "usign": item.get("sign", "") or item.get("usign", ""),
                    "face": item.get("face", ""),
                }
                for item in ((d.get("data") or {}).get("list") or [])
            ]
        except Exception as e:
            logger.warning(f"[BiliBot] 获取黑名单异常: {e}")
            return []

    # ── 图片上传 ──
    async def _upload_image_to_bilibili(self, image_path):
        try:
            with open(image_path, "rb") as f:
                img_data = f.read()
            form = aiohttp.FormData()
            form.add_field('file_up', img_data, filename='image.png', content_type='image/png')
            form.add_field('category', 'daily')
            form.add_field('csrf', self.config.get("BILI_JCT", ""))
            headers = {"Cookie": self._headers()["Cookie"], "User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com"}
            async with aiohttp.ClientSession() as s:
                async with s.post(BILI_UPLOAD_IMAGE_URL, headers=headers, data=form, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    result = await r.json()
            if result.get("code") == 0:
                img_info = result["data"]
                logger.info("[BiliBot] 📤 图片上传成功")
                return {"img_src": img_info["image_url"], "img_width": img_info["image_width"], "img_height": img_info["image_height"], "img_size": os.path.getsize(image_path) / 1024}
            else:
                logger.warning(f"[BiliBot] 图片上传失败: {result}")
                return None
        except Exception as e:
            logger.error(f"[BiliBot] 图片上传异常: {e}")
            return None

    async def _upload_image(self, image_path):
        """上传图片到 B站（_upload_image_to_bilibili 的语义化别名）。"""
        return await self._upload_image_to_bilibili(image_path)

    # ── 动态发送 ──
    async def _post_dynamic_text(self, text):
        data = {
            "dynamic_id": 0, "type": 4, "rid": 0, "content": text,
            "up_choose_comment": 0, "up_close_comment": 0,
            "extension": '{"emoji_type":1,"from":{"emoji_type":1},"flag_cfg":{}}',
            "at_uids": "", "ctrl": "[]",
            "csrf_token": self.config.get("BILI_JCT", ""), "csrf": self.config.get("BILI_JCT", ""),
        }
        try:
            result, _ = await self._http_post(BILI_DYNAMIC_TEXT_URL, data=data)
            if result.get("code") == 0:
                logger.info("[BiliBot] ✅ 纯文字动态发送成功")
                return True
            else:
                logger.warning(f"[BiliBot] 动态发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"[BiliBot] 动态发送异常: {e}")
            return False

    async def _post_text_dynamic(self, text):
        """发布纯文字动态（_post_dynamic_text 的语义化别名）。"""
        return await self._post_dynamic_text(text)

    async def _post_dynamic_with_image(self, text, img_info):
        params = {"csrf": self.config.get("BILI_JCT", "")}
        payload = {"dyn_req": {"content": {"contents": [{"raw_text": text, "type": 1, "biz_id": ""}]}, "pics": [img_info], "scene": 2}}
        try:
            headers = {**self._headers(), "Content-Type": "application/json"}
            async with aiohttp.ClientSession() as s:
                async with s.post(BILI_DYNAMIC_IMAGE_URL, params=params, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    result = await r.json()
            if result.get("code") == 0:
                logger.info("[BiliBot] ✅ 带图动态发送成功")
                return True
            else:
                logger.warning(f"[BiliBot] 带图动态失败: {result}，尝试纯文字...")
                return await self._post_dynamic_text(text)
        except Exception as e:
            logger.error(f"[BiliBot] 带图动态异常: {e}，尝试纯文字...")
            return await self._post_dynamic_text(text)

    async def _post_image_dynamic(self, text, img_info):
        """发布带图动态（_post_dynamic_with_image 的语义化别名）。"""
        return await self._post_dynamic_with_image(text, img_info)

    # ── 视频下载 ──
    async def _download_video(self, bvid, max_height=480):
        """通过 yt-dlp 下载视频到临时目录，返回本地文件路径或 None。"""
        if not bvid:
            return None
        os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)
        output_template = os.path.join(TEMP_VIDEO_DIR, f"{bvid}.%(ext)s")
        # 生成 Netscape 格式 cookie 文件，兼容新版 yt-dlp
        cookie_file = os.path.join(TEMP_VIDEO_DIR, f"{bvid}_cookies.txt")
        sessdata = self.config.get('SESSDATA', '')
        bili_jct = self.config.get('BILI_JCT', '')
        dede_uid = self.config.get('DEDE_USER_ID', '')
        buvid3 = self.config.get('BUVID3', '')
        cookie_content = (
            "# Netscape HTTP Cookie File\n"
            f".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\t{sessdata}\n"
            f".bilibili.com\tTRUE\t/\tFALSE\t0\tbili_jct\t{bili_jct}\n"
            f".bilibili.com\tTRUE\t/\tFALSE\t0\tDedeUserID\t{dede_uid}\n"
        )
        if buvid3:
            cookie_content += f".bilibili.com\tTRUE\t/\tFALSE\t0\tbuvid3\t{buvid3}\n"
        try:
            fd = os.open(cookie_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(cookie_content)
        except Exception as e:
            logger.warning(f"[BiliBot] Cookie文件写入失败: {e}")
            return None

        # 格式回退：优先保证同时带有视频 + 音频流，避免命中 B 站 DASH 纯视频 mp4 流
        fallback_formats = self._video_download_format_fallbacks(max_height)

        last_err = ""
        try:
            for fmt in fallback_formats:
                # 清理上一轮可能残留的部分文件
                self._cleanup_partial_downloads(bvid)
                code, _, stderr = await self._run_process(
                    "yt-dlp", "-o", output_template,
                    "--format", fmt,
                    "--no-playlist", "--merge-output-format", "mp4",
                    "--recode-video", "mp4",
                    "--cookies", cookie_file,
                    "--add-header", "Referer: https://www.bilibili.com",
                    "--limit-rate", "2M",
                    f"https://www.bilibili.com/video/{bvid}",
                    timeout=600,
                )
                if code == 0:
                    fp = self._pick_downloaded_video_file(bvid)
                    if fp:
                        # ✅ 关键：下载后校验是否存在音频流，没有则继续尝试下一个格式
                        has_audio = await self._has_audio_stream(fp)
                        if not has_audio:
                            last_err = f"文件 {os.path.basename(fp)} 缺少音频流（纯视频DASH流），已丢弃"
                            logger.warning(f"[BiliBot] {last_err}({bvid})，格式: {fmt}")
                            try:
                                os.remove(fp)
                            except OSError:
                                pass
                            continue
                        logger.info(f"[BiliBot] 视频下载成功({bvid})，格式: {fmt}，文件: {os.path.basename(fp)}")
                        return fp
                    last_err = "yt-dlp 成功退出，但没有产出可发送的视频文件（可能只下载到音频）"
                    logger.info(f"[BiliBot] {last_err}({bvid})，尝试下一个格式")
                    continue
                last_err = stderr[:200] if stderr else "unknown error"
                logger.info(f"[BiliBot] 格式 {fmt} 下载失败({bvid})，尝试下一个: {last_err[:80]}")
        finally:
            try:
                os.remove(cookie_file)
            except OSError:
                pass

        logger.warning(f"[BiliBot] 视频下载全部失败({bvid}): {last_err}")
        return None

    def _cleanup_partial_downloads(self, bvid):
        """清理某个 bvid 的残留下载文件（不删 cookie）"""
        if not os.path.isdir(TEMP_VIDEO_DIR):
            return
        for name in os.listdir(TEMP_VIDEO_DIR):
            if name.startswith(bvid) and not name.endswith("_cookies.txt"):
                try:
                    fp = os.path.join(TEMP_VIDEO_DIR, name)
                    if os.path.isfile(fp):
                        os.remove(fp)
                except OSError:
                    pass

    def _pick_downloaded_video_file(self, bvid):
        """从临时目录挑选出 bvid 对应的可发送视频文件。"""
        if not os.path.isdir(TEMP_VIDEO_DIR):
            return None
        video_exts = (".mp4", ".mkv", ".flv", ".webm", ".avi", ".mov")
        candidates = []
        for name in os.listdir(TEMP_VIDEO_DIR):
            if not name.startswith(bvid):
                continue
            fp = os.path.join(TEMP_VIDEO_DIR, name)
            if not os.path.isfile(fp):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in video_exts:
                continue
            size = os.path.getsize(fp)
            if size > 0:
                candidates.append((ext == ".mp4", size, fp))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][2]

    # ── UP主最新视频 ──
    async def _get_up_latest_video(self, mid):
        try:
            d, _ = await self._wbi_get("https://api.bilibili.com/x/space/wbi/arc/search", {"mid": mid, "ps": 1, "pn": 1, "order": "pubdate"})
            if d.get("code") != 0:
                return None
            vlist = d.get("data", {}).get("list", {}).get("vlist", [])
            if not vlist:
                return None
            v = vlist[0]
            return {"bvid": v["bvid"], "title": v["title"], "desc": v.get("description", ""), "up_name": v["author"], "up_mid": mid, "pubdate": v["created"], "pic": v.get("pic", "")}
        except Exception as e:
            logger.error(f"[BiliBot] 获取UP主最新视频失败: {e}")
            return None

    # ── B站搜索 & UP主查询 API ──

    async def search_bilibili_videos(self, keyword, ps=5):
        """搜索B站视频，返回视频列表"""
        try:
            d, _ = await self._wbi_get("https://api.bilibili.com/x/web-interface/wbi/search/type", {
                "keyword": keyword, "search_type": "video",
                "page": 1, "page_size": ps, "order": "totalrank",
            })
            if d.get("code") != 0:
                logger.debug(f"[BiliBot] 搜索视频失败: code={d.get('code')} msg={d.get('message')}")
                return []
            results = []
            for v in (d.get("data") or {}).get("result", [])[:ps]:
                title = re.sub(r"<[^>]+>", "", v.get("title", ""))
                results.append({
                    "bvid": v.get("bvid", ""),
                    "title": title,
                    "author": v.get("author", ""),
                    "mid": v.get("mid", ""),
                    "play": v.get("play", 0),
                    "danmaku": v.get("video_review", 0),
                    "desc": v.get("description", "")[:100],
                    "duration": v.get("duration", ""),
                    "pubdate": v.get("pubdate", 0),
                })
            return results
        except Exception as e:
            logger.error(f"[BiliBot] 搜索视频异常: {e}")
            return []

    async def search_bilibili_users(self, keyword, ps=3):
        """搜索B站用户/UP主"""
        try:
            d, _ = await self._wbi_get("https://api.bilibili.com/x/web-interface/wbi/search/type", {
                "keyword": keyword, "search_type": "bili_user",
                "page": 1, "page_size": ps,
            })
            if d.get("code") != 0:
                return []
            results = []
            for u in (d.get("data") or {}).get("result", [])[:ps]:
                results.append({
                    "mid": u.get("mid", ""),
                    "uname": u.get("uname", ""),
                    "fans": u.get("fans", 0),
                    "videos": u.get("videos", 0),
                    "sign": u.get("usign", "")[:80],
                    "level": u.get("level", 0),
                })
            return results
        except Exception as e:
            logger.error(f"[BiliBot] 搜索用户异常: {e}")
            return []

    async def get_up_info(self, mid):
        """获取UP主详细信息"""
        try:
            d, _ = await self._wbi_get("https://api.bilibili.com/x/space/wbi/acc/info", {"mid": mid})
            if d.get("code") != 0:
                return None
            data = d.get("data") or {}
            return {
                "mid": data.get("mid"),
                "name": data.get("name", ""),
                "sign": data.get("sign", ""),
                "level": data.get("level", 0),
                "fans_badge": data.get("fans_badge", False),
                "official_title": (data.get("official") or {}).get("title", ""),
                "vip_label": (data.get("vip") or {}).get("label", {}).get("text", ""),
            }
        except Exception as e:
            logger.error(f"[BiliBot] 获取UP主信息失败: {e}")
            return None

    async def get_up_recent_videos(self, mid, ps=5):
        """获取UP主最近的N个视频"""
        try:
            d, _ = await self._wbi_get("https://api.bilibili.com/x/space/wbi/arc/search", {
                "mid": mid, "ps": ps, "pn": 1, "order": "pubdate",
            })
            if d.get("code") != 0:
                return []
            vlist = (d.get("data") or {}).get("list", {}).get("vlist", [])
            results = []
            for v in vlist[:ps]:
                results.append({
                    "bvid": v.get("bvid", ""),
                    "title": v.get("title", ""),
                    "desc": v.get("description", "")[:80],
                    "play": v.get("play", 0),
                    "created": v.get("created", 0),
                })
            return results
        except Exception as e:
            logger.error(f"[BiliBot] 获取UP主视频列表失败: {e}")
            return []

    async def get_up_recent_dynamics(self, mid, limit=5):
        """获取UP主最近的动态"""
        try:
            d, _ = await self._http_get(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                params={
                    "host_mid": mid, "offset": "",
                    "timezone_offset": -480,
                    "features": "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote",
                },
            )
            if d.get("code") != 0:
                return []
            results = []
            for item in ((d.get("data") or {}).get("items") or [])[:limit]:
                modules = item.get("modules") or {}
                author = modules.get("module_author") or {}
                dynamic = modules.get("module_dynamic") or {}
                desc = (dynamic.get("desc") or {}).get("text", "")
                # opus格式
                major = dynamic.get("major") or {}
                if not desc and major.get("type") == "MAJOR_TYPE_OPUS":
                    opus = major.get("opus") or {}
                    desc = (opus.get("summary") or {}).get("text", "") or opus.get("title", "")
                results.append({
                    "dynamic_id": item.get("id_str", ""),
                    "type": item.get("type", ""),
                    "text": desc[:120] if desc else "",
                    "pub_time": author.get("pub_time", ""),
                })
            return results
        except Exception as e:
            logger.error(f"[BiliBot] 获取UP主动态失败: {e}")
            return []

    async def get_following_updates(self, limit=20):
        """获取关注列表的最新动态流（今日更新）"""
        try:
            d, _ = await self._http_get(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all",
                params={
                    "timezone_offset": -480, "type": "all", "offset": "",
                    "features": "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote",
                },
            )
            if d.get("code") != 0:
                logger.debug(f"[BiliBot] 关注动态流获取失败: code={d.get('code')}")
                return []
            results = []
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            for item in ((d.get("data") or {}).get("items") or [])[:limit]:
                modules = item.get("modules") or {}
                author = modules.get("module_author") or {}
                dynamic = modules.get("module_dynamic") or {}
                # 时间戳
                pub_ts = author.get("pub_ts", 0)
                if pub_ts:
                    pub_date = datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d")
                    if pub_date != today:
                        continue  # 只要今天的
                pub_time = author.get("pub_time", "")
                up_name = author.get("name", "")
                up_mid = str(author.get("mid", ""))
                # 动态文字
                desc = (dynamic.get("desc") or {}).get("text", "")
                major = dynamic.get("major") or {}
                major_type = major.get("type", "")
                if not desc and (major_type == "MAJOR_TYPE_OPUS" or "opus" in major):
                    opus = major.get("opus") or {}
                    desc = (opus.get("summary") or {}).get("text", "") or opus.get("title", "")
                # 视频投稿
                video_title = ""
                video_bvid = ""
                if major_type == "MAJOR_TYPE_ARCHIVE":
                    archive = major.get("archive") or {}
                    video_title = archive.get("title", "")
                    video_bvid = archive.get("bvid", "")
                # 直播动态
                live_title = ""
                if major_type in ("MAJOR_TYPE_LIVE", "MAJOR_TYPE_LIVE_RCMD"):
                    live = major.get("live") or major.get("live_rcmd") or {}
                    # live_rcmd 的内容可能嵌套在 content 里（JSON字符串）
                    if "content" in live:
                        try:
                            live_content = json.loads(live["content"]) if isinstance(live["content"], str) else live["content"]
                            live_title = live_content.get("title", "") or live_content.get("live_play_info", {}).get("title", "")
                        except Exception:
                            live_title = ""
                    else:
                        live_title = live.get("title", "")
                dyn_type = item.get("type", "")
                # 未识别的类型打日志
                if not desc and not video_title and not live_title:
                    logger.debug(f"[BiliBot] 关注动态无内容: up={up_name} type={dyn_type} major_type={major_type} keys={list(major.keys())}")
                results.append({
                    "up_name": up_name,
                    "up_mid": up_mid,
                    "type": dyn_type,
                    "text": desc[:120] if desc else "",
                    "video_title": video_title,
                    "video_bvid": video_bvid,
                    "live_title": live_title,
                    "pub_time": pub_time,
                })
            return results
        except Exception as e:
            logger.error(f"[BiliBot] 获取关注动态流失败: {e}")
            return []

    async def get_following_live(self):
        """查看关注的人谁在直播"""
        try:
            d, _ = await self._http_get(
                "https://api.live.bilibili.com/xlive/web-ucenter/v1/xfetter/FeedList",
                params={"page": 1, "pagesize": 20},
            )
            if not isinstance(d, dict) or d.get("code") != 0:
                logger.debug(f"[BiliBot] 直播列表获取失败: {d.get('code') if isinstance(d, dict) else type(d)}")
                return []
            results = []
            for item in ((d.get("data") or {}).get("list") or []):
                results.append({
                    "uname": item.get("uname", ""),
                    "mid": str(item.get("uid", "")),
                    "title": item.get("title", ""),
                    "room_id": item.get("roomid", ""),
                    "area_name": item.get("area_v2_name", "") or item.get("area_name", ""),
                    "online": item.get("online", 0),
                    "link": f"https://live.bilibili.com/{item.get('roomid', '')}",
                })
            return results
        except Exception as e:
            logger.error(f"[BiliBot] 获取关注直播列表失败: {e}")
            return []

    # ── 番剧 API ──

    @staticmethod
    def _pgc_ok(d, label="PGC"):
        """检查 PGC API 响应是否为有效 dict 且 code==0。"""
        if not isinstance(d, dict):
            logger.warning(f"[BiliBot] {label}返回非dict: type={type(d).__name__} val={str(d)[:200]}")
            return False
        code = d.get("code", -1)
        if code != 0:
            logger.debug(f"[BiliBot] {label}失败: code={code} msg={d.get('message', '')}")
            return False
        return True

    async def _pgc_get(self, url, params=None, label="PGC"):
        """专用于 PGC API 的 GET：用 text+json.loads 避免 r.json() 的玄学问题。"""
        try:
            text, _ = await self._http_get_text(url, params=params, timeout=10)
            if not text:
                logger.warning(f"[BiliBot] {label} 返回空响应")
                return None
            d = json.loads(text)
            if not isinstance(d, dict):
                logger.warning(f"[BiliBot] {label} JSON非dict: type={type(d).__name__} val={str(d)[:200]}")
                return None
            return d
        except json.JSONDecodeError as e:
            logger.warning(f"[BiliBot] {label} JSON解析失败: {e} text={str(text)[:200]}")
            return None
        except Exception as e:
            logger.warning(f"[BiliBot] {label} 请求失败: {e}")
            return None

    async def _search_bangumi(self, keyword, ps=5):
        """搜索B站番剧，返回番剧列表。"""
        try:
            d, _ = await self._wbi_get("https://api.bilibili.com/x/web-interface/wbi/search/type", {
                "keyword": keyword, "search_type": "media_bangumi",
                "page": 1, "page_size": ps,
            })
            if not self._pgc_ok(d, "搜索番剧"):
                return []
            results = []
            for item in (d.get("data") or {}).get("result", [])[:ps]:
                title = re.sub(r"<[^>]+>", "", item.get("title", ""))
                score_info = item.get("media_score") or {}
                results.append({
                    "media_id": item.get("media_id", 0),
                    "season_id": item.get("season_id", 0),
                    "title": title,
                    "org_title": item.get("org_title", ""),
                    "season_type_name": item.get("season_type_name", "番剧"),
                    "areas": item.get("areas", ""),
                    "styles": item.get("styles", ""),
                    "cv": item.get("cv", ""),
                    "staff": item.get("staff", ""),
                    "desc": item.get("desc", "")[:150],
                    "score": score_info.get("score", 0),
                    "user_count": score_info.get("user_count", 0),
                    "ep_size": item.get("ep_size", 0),
                    "pubtime": item.get("pubtime", 0),
                    "url": item.get("url", ""),
                    "cover": item.get("cover", ""),
                })
            return results
        except Exception as e:
            logger.error(f"[BiliBot] 搜索番剧异常: {e}")
            return []

    async def _get_bangumi_info(self, season_id=None, ep_id=None):
        """获取番剧详情（剧集列表、评分、简介等）。"""
        try:
            params = {}
            if season_id:
                params["season_id"] = season_id
            elif ep_id:
                params["ep_id"] = ep_id
            else:
                return None
            d = await self._pgc_get(
                "https://api.bilibili.com/pgc/view/web/season", params=params, label="番剧详情",
            )
            if not d or not self._pgc_ok(d, "番剧详情"):
                return None
            result = d.get("result", {})
            if not isinstance(result, dict):
                logger.warning(f"[BiliBot] 番剧详情 result 非dict: type={type(result).__name__} val={str(result)[:200]}")
                return None
            rating = result.get("rating") or {}
            if not isinstance(rating, dict):
                rating = {}
            stat = result.get("stat") or {}
            if not isinstance(stat, dict):
                stat = {}
            episodes = []
            for ep in (result.get("episodes") or []):
                if not isinstance(ep, dict):
                    continue
                episodes.append({
                    "ep_id": ep.get("ep_id") or ep.get("id", 0),
                    "title": ep.get("share_copy", "") or ep.get("long_title", "") or f"第{ep.get('title', '?')}话",
                    "long_title": ep.get("long_title", ""),
                    "ep_index": ep.get("title", ""),
                    "badge": ep.get("badge", ""),
                    "duration": ep.get("duration", 0) // 60000 if ep.get("duration") else 0,
                    "aid": ep.get("aid", 0),
                    "cid": ep.get("cid", 0),
                })
            areas_raw = result.get("areas") or []
            styles_raw = result.get("styles") or []
            return {
                "season_id": result.get("season_id", 0),
                "media_id": result.get("media_id", 0),
                "title": result.get("season_title", "") or result.get("title", ""),
                "evaluate": str(result.get("evaluate", ""))[:300],
                "score": rating.get("score", 0),
                "count": rating.get("count", 0),
                "areas": ", ".join(a.get("name", "") if isinstance(a, dict) else str(a) for a in areas_raw),
                "styles": ", ".join(s.get("name", "") if isinstance(s, dict) else str(s) for s in styles_raw),
                "total_ep": result.get("total", 0),
                "new_ep_desc": (result.get("new_ep") or {}).get("desc", "") if isinstance(result.get("new_ep"), dict) else "",
                "stat_views": stat.get("views", 0),
                "stat_danmakus": stat.get("danmakus", 0),
                "stat_favorites": stat.get("favorites", 0),
                "episodes": episodes,
                "link": result.get("link", ""),
                "cover": result.get("cover", ""),
            }
        except Exception as e:
            logger.error(f"[BiliBot] 番剧详情异常: {e}")
            return None

    async def _get_bangumi_episodes(self, season_id):
        """获取番剧剧集列表（_get_bangumi_info 的轻量封装，仅返回 episodes）。"""
        detail = await self._get_bangumi_info(season_id=season_id)
        if not detail:
            return []
        return detail.get("episodes", []) or []

    # ── 特别关注巡视相关 API ──

    async def _get_special_follows(self):
        """获取特别关注列表，返回 mid 数组。"""
        try:
            d, _ = await self._http_get(
                "https://api.bilibili.com/x/relation/tag",
                params={"tagid": -3, "pn": 1, "ps": 50},  # -3 是特别关注标签
            )
            if not isinstance(d, dict) or d.get("code") != 0:
                logger.debug(f"[BiliBot] 获取特别关注失败: {d.get('code') if isinstance(d, dict) else type(d)}")
                return []
            return [
                {
                    "mid": str(item.get("mid", "")),
                    "uname": item.get("uname", ""),
                    "sign": item.get("sign", ""),
                    "face": item.get("face", ""),
                }
                for item in ((d.get("data") or {}).get("list") or [])
            ]
        except Exception as e:
            logger.warning(f"[BiliBot] 获取特别关注异常: {e}")
            return []

    async def _patrol_special_follows(self, limit_each=3):
        """巡视特别关注列表，返回每个特别关注 UP 最近动态。"""
        specials = await self._get_special_follows()
        if not specials:
            return []
        results = []
        for up in specials:
            mid = up.get("mid", "")
            if not mid:
                continue
            dynamics = await self.get_up_recent_dynamics(mid, limit=limit_each)
            if not dynamics:
                continue
            results.append({
                "mid": mid,
                "uname": up.get("uname", ""),
                "dynamics": dynamics,
            })
        return results

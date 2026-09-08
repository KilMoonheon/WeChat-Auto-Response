# -*- coding: utf-8 -*-
"""微信自动回复 — 适配微信 4.1.12+，按联系人昵称配置回复范围与内容。"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from wechatauto import WeChatDB
from wechatauto.guia import WeChatGUI, quick_send

CONFIG_PATH = Path(__file__).with_name("config.yaml")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("auto_reply")


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"找不到配置文件: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data.get("contacts"), dict):
        raise ValueError("config.yaml 中 contacts 必须是对象（昵称 -> 规则）")
    return data


def pick_reply(rule: dict[str, Any], content: str) -> str | None:
    mode = rule.get("reply_mode", "fixed")
    if mode == "fixed":
        return rule.get("default")

    if mode == "keyword":
        keywords = rule.get("keywords") or {}
        for keyword, reply in keywords.items():
            if keyword and keyword in content:
                return str(reply)
        default = rule.get("default")
        return str(default) if default else None

    log.warning("未知 reply_mode=%s，跳过回复", mode)
    return None


def collect_bot_replies(rule: dict[str, Any]) -> set[str]:
    texts: set[str] = set()
    default = rule.get("default")
    if default:
        texts.add(str(default))
    for reply in (rule.get("keywords") or {}).values():
        if reply:
            texts.add(str(reply))
    return texts


def bust_session_cache(db: WeChatDB) -> None:
    """强制重新合并 session.db 的 WAL，便于读到手机刚同步的消息。"""
    try:
        names = os.listdir(db.workdir)
    except OSError:
        return
    for name in names:
        if "session" in name.lower() and (name.endswith(".db") or name.endswith(".stamp")):
            try:
                os.remove(os.path.join(db.workdir, name))
            except OSError:
                pass


def minimize_wechat() -> bool:
    """发送完成后将微信主窗口最小化到任务栏。"""
    try:
        gui = WeChatGUI()
        if not gui.main_hwnd:
            return False
        gui.restore_zorder()
        SW_MINIMIZE = 6
        gui._input._user32.ShowWindow(gui.main_hwnd, SW_MINIMIZE)
        return True
    except Exception as exc:
        log.warning("最小化微信窗口失败: %s", exc)
        return False


def resolve_username(db: WeChatDB, nickname: str) -> str | None:
    username = db.username_by_nickname(nickname)
    if username:
        return username

    for contact in db.search_contact(nickname):
        display = contact.get("remark") or contact.get("nick_name")
        if display == nickname:
            return contact["username"]
    return None


def is_incoming_session(
    session: dict[str, Any],
    *,
    allow_self: bool,
    self_names: set[str],
) -> bool:
    if allow_self:
        return True

    if session.get("unread", 0) > 0:
        return True

    sender = (session.get("last_sender") or "").strip()
    if sender and sender not in self_names:
        return True

    return False


class WatchedContact:
    def __init__(self, nickname: str, username: str, rule: dict[str, Any]) -> None:
        self.nickname = nickname
        self.username = username
        self.rule = rule
        self.last_time = 0
        self.last_summary = ""
        self.last_unread = 0
        self.last_reply_at = 0.0
        self.bot_replies = collect_bot_replies(rule)
        self.processed_keys: set[tuple[str, int]] = set()


class AutoReplyBot:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.db = WeChatDB()
        self.self_info = self.db.get_self_info()
        self.self_username = self.self_info.get("username") or ""
        self.self_names = {
            n
            for n in (
                self.self_info.get("nick_name"),
                self.self_info.get("remark"),
                self.self_username,
            )
            if n
        }
        self.watchlist: list[WatchedContact] = []

    def _cooldown_seconds(self, item: WatchedContact) -> float:
        if "cooldown_seconds" in item.rule:
            return float(item.rule["cooldown_seconds"])
        return float(self.config.get("settings", {}).get("reply_cooldown", 180))

    def _cooldown_remaining(self, item: WatchedContact) -> float:
        cooldown = self._cooldown_seconds(item)
        if cooldown <= 0 or item.last_reply_at <= 0:
            return 0.0
        return max(0.0, cooldown - (time.time() - item.last_reply_at))

    def _bootstrap_contact(self, item: WatchedContact, session: dict[str, Any] | None) -> None:
        if not session:
            return
        item.last_time = int(session.get("last_time") or 0)
        item.last_summary = (session.get("summary") or "").strip()
        item.last_unread = int(session.get("unread") or 0)
        if item.last_time:
            item.processed_keys.add((item.username, item.last_time))

    def _should_skip_summary(self, item: WatchedContact, summary: str) -> bool:
        if not summary:
            return True
        if summary.startswith("["):
            return True
        if summary in item.bot_replies:
            return True
        return False

    def _handle_session(self, item: WatchedContact, session: dict[str, Any]) -> None:
        last_time = int(session.get("last_time") or 0)
        summary = (session.get("summary") or "").strip()
        if last_time <= 0:
            return

        event_key = (item.username, last_time)
        if event_key in item.processed_keys:
            return

        # 首次启动：只记录基线，不回复历史消息
        if item.last_time == 0:
            self._bootstrap_contact(item, session)
            log.info("[%s] 监听基线: %s", item.nickname, summary[:40] or "(空)")
            return

        if last_time <= item.last_time and summary == item.last_summary:
            return

        item.processed_keys.add(event_key)
        if len(item.processed_keys) > 5000:
            item.processed_keys.clear()

        if not is_incoming_session(
            session,
            allow_self=item.rule.get("allow_self", False),
            self_names=self.self_names,
        ):
            item.last_time = last_time
            item.last_summary = summary
            return

        if item.rule.get("only_text", True) and self._should_skip_summary(item, summary):
            item.last_time = last_time
            item.last_summary = summary
            return

        reply = pick_reply(item.rule, summary)
        if not reply:
            item.last_time = last_time
            item.last_summary = summary
            return

        remaining = self._cooldown_remaining(item)
        if remaining > 0:
            log.info(
                "[%s] 冷却中，跳过回复（距上次自动回复 %.0f 秒后可再回复）",
                item.nickname,
                remaining,
            )
            item.last_time = last_time
            item.last_summary = summary
            return

        log.info("[%s] 收到: %s", item.nickname, summary[:80])
        result = quick_send(reply, item.nickname)
        if result and result.get("status") == "成功":
            log.info("[%s] 已回复: %s", item.nickname, reply[:80])
            item.last_reply_at = time.time()
            if self.config.get("settings", {}).get("minimize_after_reply", True):
                if minimize_wechat():
                    log.info("已最小化微信窗口")
            item.bot_replies.add(reply)
            refreshed = self._find_session(item.username)
            if refreshed:
                rt = int(refreshed.get("last_time") or last_time)
                rs = (refreshed.get("summary") or reply).strip()
                item.processed_keys.add((item.username, rt))
                item.last_time = rt
                item.last_summary = rs
                return
        else:
            log.error("[%s] 发送失败: %s", item.nickname, result)

        item.last_time = last_time
        item.last_summary = summary

    def _find_session(self, username: str) -> dict[str, Any] | None:
        for session in self.db.get_sessions(limit=500):
            if session.get("username") == username:
                return session
        return None

    def _load_sessions(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        if refresh:
            bust_session_cache(self.db)
        return {s["username"]: s for s in self.db.get_sessions(limit=500)}

    def _sync_mobile_session(self, item: WatchedContact) -> dict[str, Any] | None:
        """手机消息常延迟写入 PC 本地库，打开会话可触发同步。"""
        try:
            gui = WeChatGUI()
            gui.open_chat(item.nickname)
            time.sleep(float(self.config.get("settings", {}).get("sync_wait", 0.8)))
        except Exception as exc:
            log.warning("[%s] 打开会话同步失败: %s", item.nickname, exc)
        bust_session_cache(self.db)
        return self._find_session(item.username)

    def _prepare_session(self, item: WatchedContact, session: dict[str, Any]) -> dict[str, Any]:
        settings = self.config.get("settings") or {}
        unread = int(session.get("unread") or 0)
        sync_mobile = settings.get("sync_mobile", True)

        # 仅在「有未读」时打开会话拉取手机消息，不做定时打扰
        if sync_mobile and unread > item.last_unread:
            log.info("[%s] 检测到未读消息，正在从手机同步…", item.nickname)
            synced = self._sync_mobile_session(item)
            if synced:
                session = synced

        item.last_unread = int(session.get("unread") or 0)
        return session

    def run(self) -> None:
        settings = self.config.get("settings") or {}
        contacts: dict[str, Any] = self.config.get("contacts") or {}
        interval = float(settings.get("poll_interval", 1.0))

        enabled = {
            name: rule
            for name, rule in contacts.items()
            if isinstance(rule, dict) and rule.get("enabled", True)
        }
        if not enabled:
            log.error("没有启用的联系人，请在 config.yaml 的 contacts 中配置并设置 enabled: true")
            sys.exit(1)

        log.info("正在连接微信（需 PC 微信已登录）…")
        log.info(
            "当前账号: %s",
            self.self_info.get("remark") or self.self_info.get("nick_name") or self.self_username,
        )

        sessions = self._load_sessions()
        failed: list[str] = []

        for nickname, rule in enabled.items():
            username = resolve_username(self.db, nickname)
            if not username:
                log.error("找不到联系人 [%s]，请确认昵称与微信聊天列表显示名完全一致", nickname)
                failed.append(nickname)
                continue

            if username == self.self_username:
                log.error(
                    "联系人 [%s] 是你自己的账号，不能作为聊天对象。"
                    "请填写给你发消息的好友/群聊昵称。",
                    nickname,
                )
                failed.append(nickname)
                continue

            item = WatchedContact(nickname, username, rule)
            self._bootstrap_contact(item, sessions.get(username))
            self.watchlist.append(item)
            log.info("已开始监听: %s (%s)", nickname, username)

        if not self.watchlist:
            log.error("没有成功注册任何联系人，请检查 config.yaml")
            sys.exit(1)

        if failed:
            log.warning("以下联系人未启用: %s", ", ".join(failed))

        log.info(
            "自动回复已启动，共监听 %d 个联系人。按 Ctrl+C 退出。",
            len(self.watchlist),
        )
        log.info("提示：仅回复程序启动后收到的新消息。")

        try:
            while True:
                sessions = self._load_sessions(refresh=True)
                for item in self.watchlist:
                    session = sessions.get(item.username)
                    if not session:
                        continue
                    session = self._prepare_session(item, session)
                    self._handle_session(item, session)
                time.sleep(interval)
        except KeyboardInterrupt:
            log.info("正在停止…")


def main() -> None:
    if sys.version_info >= (3, 14):
        log.error("请使用 Python 3.13 或更低版本运行: py -3.13 main.py")
        sys.exit(1)

    config = load_config()
    AutoReplyBot(config).run()


if __name__ == "__main__":
    main()

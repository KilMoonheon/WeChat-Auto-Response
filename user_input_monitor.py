# -*- coding: utf-8 -*-
"""微信被程序拉起后，监听用户鼠标移动以结束本轮自动回复（不退出程序）。"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Callable, Optional

log = logging.getLogger("auto_reply")

user32 = ctypes.windll.user32

# 程序调用 SetCursorPos 后的忽略窗口（秒），覆盖 wechatauto 点击前后的 sleep
_BOT_CURSOR_GRACE = 0.45
_orig_set_cursor_pos: Optional[Callable[..., bool]] = None


def note_bot_cursor_move(x: int, y: int) -> None:
    """由 SetCursorPos 钩子或自动化补丁调用，标记本次为程序移动。"""
    global _bot_ignore_until, _bot_last_pos
    now = time.monotonic()
    _bot_ignore_until = now + _BOT_CURSOR_GRACE
    _bot_last_pos = (int(x), int(y))


_bot_ignore_until = 0.0
_bot_last_pos: Optional[tuple[int, int]] = None


def _install_setcursorpos_hook() -> None:
    global _orig_set_cursor_pos
    if _orig_set_cursor_pos is not None:
        return

    _orig_set_cursor_pos = user32.SetCursorPos
    _orig_set_cursor_pos.argtypes = [ctypes.c_int, ctypes.c_int]
    _orig_set_cursor_pos.restype = wintypes.BOOL

    @ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_int, ctypes.c_int)
    def _hook(x: int, y: int) -> bool:
        note_bot_cursor_move(x, y)
        return bool(_orig_set_cursor_pos(x, y))

    user32.SetCursorPos = _hook


_install_setcursorpos_hook()


class UserInputMonitor:
    """仅在 arm_takeover_watch() 之后监听（微信窗口已被程序拉起之后）。"""

    def __init__(
        self,
        enabled: bool = True,
        move_threshold: int = 8,
        watch_timeout: float = 600.0,
        foreground_grace: float = 2.0,
        bot_cursor_grace: float = 0.45,
        bot_teleport_threshold: int = 80,
        bot_speed_threshold: float = 3500.0,
    ) -> None:
        self.enabled = enabled
        self.move_threshold = move_threshold
        self.watch_timeout = watch_timeout
        self.foreground_grace = foreground_grace
        self.bot_teleport_threshold = bot_teleport_threshold
        self.bot_speed_threshold = bot_speed_threshold
        global _BOT_CURSOR_GRACE
        _BOT_CURSOR_GRACE = bot_cursor_grace
        self._armed = False
        self._takeover_round = False
        self._reason: Optional[str] = None
        self._armed_at = 0.0
        self._wechat_hwnd: Optional[int] = None
        self._last_pos: Optional[tuple[int, int]] = None
        self._last_poll_at = 0.0
        self._foreground_lost_at: Optional[float] = None

    @property
    def takeover_this_round(self) -> bool:
        return self._takeover_round

    @property
    def reason(self) -> str:
        return self._reason or "用户输入"

    @property
    def takeover_armed(self) -> bool:
        return self._armed

    def start(self) -> None:
        """兼容旧接口，轮询模式无需后台线程。"""

    def stop(self) -> None:
        self.disarm_takeover_watch()

    def clear_takeover(self) -> None:
        self._takeover_round = False
        self._reason = None

    def arm_takeover_watch(self, wechat_hwnd: Optional[int] = None) -> None:
        """在程序已拉起微信窗口之后调用，开始监听人工接管。"""
        if not self.enabled:
            return
        self._armed = True
        self._armed_at = time.monotonic()
        self._last_poll_at = self._armed_at
        self._wechat_hwnd = wechat_hwnd
        self._foreground_lost_at = None
        self._last_pos = self._cursor_pos()
        log.info("人工接管监听已开启（移动鼠标将跳过本轮自动回复，程序继续运行）")

    def disarm_takeover_watch(self, reason: str = "") -> None:
        if not self._armed:
            return
        self._armed = False
        self._wechat_hwnd = None
        self._last_pos = None
        self._last_poll_at = 0.0
        self._foreground_lost_at = None
        if reason:
            log.debug("人工接管监听已关闭：%s", reason)

    def _is_bot_cursor_move(
        self, pos: tuple[int, int], dist: float, dt: float
    ) -> bool:
        now = time.monotonic()
        if now < _bot_ignore_until:
            return True
        if _bot_last_pos and pos == _bot_last_pos:
            return True
        if dist >= self.bot_teleport_threshold:
            return True
        if dt > 0 and dist / dt >= self.bot_speed_threshold:
            return True
        return False

    def poll(self) -> bool:
        """检查一次鼠标移动。返回 True 表示用户已接管本轮。"""
        if not self._armed:
            return self._takeover_round

        now = time.monotonic()
        if self.watch_timeout > 0 and now - self._armed_at >= self.watch_timeout:
            self.disarm_takeover_watch("超时")
            return False

        if self._wechat_hwnd and user32.IsWindow(self._wechat_hwnd):
            fg = user32.GetForegroundWindow()
            if fg != self._wechat_hwnd:
                if self._foreground_lost_at is None:
                    self._foreground_lost_at = now
                elif now - self._foreground_lost_at >= self.foreground_grace:
                    self.disarm_takeover_watch("微信已不在前台")
                    return False
            else:
                self._foreground_lost_at = None

        pos = self._cursor_pos()
        if pos and self._last_pos:
            dx = abs(pos[0] - self._last_pos[0])
            dy = abs(pos[1] - self._last_pos[1])
            dist = float(dx + dy)
            dt = now - self._last_poll_at if self._last_poll_at else 0.05
            dt = max(dt, 1e-6)

            if self._is_bot_cursor_move(pos, dist, dt):
                log.debug(
                    "忽略程序鼠标移动 dist=%.0f speed=%.0f px/s",
                    dist,
                    dist / dt,
                )
                self._last_pos = pos
                self._last_poll_at = now
                return self._takeover_round

            if dist >= self.move_threshold:
                self._trigger("鼠标移动")
                return True

        if pos:
            self._last_pos = pos
        self._last_poll_at = now

        return self._takeover_round

    def _trigger(self, reason: str) -> None:
        if self._takeover_round:
            return
        self._reason = reason
        self._takeover_round = True
        self._armed = False
        log.info("检测到人工接管（%s），本轮自动回复结束，程序继续监听", reason)

    @staticmethod
    def _cursor_pos() -> Optional[tuple[int, int]]:
        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None
        return pt.x, pt.y

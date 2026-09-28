"""Browser viewer. Keys work on the page, same as the WRS example."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import wrs.scene.scene_object_primitive as wssop
import wrs.viewer.key as key
import wrs.viewer.world as wvw

from rpent_wrs.control import Inbox
from rpent_wrs.executor import WrsExecutor

VIEWER_URL = "http://127.0.0.1:8000/"


class ViewerBridge:
    """World starts empty; objects are attached when a session is launched."""

    def __init__(self) -> None:
        self.base = None
        self.scene = None
        self.attached = False
        self.pending = False
        self._lock = threading.Lock()

    def bind(self, base, scene=None) -> None:
        with self._lock:
            self.base = base
            if scene is not None:
                self.scene = scene
            if self.pending and not self.attached and self.scene is not None:
                attach_scene(base, self.scene)
                self.attached = True
                base.scene.dirty = True

    def reveal(self) -> None:
        with self._lock:
            self.pending = True
            if self.attached or self.base is None or self.scene is None:
                return
            attach_scene(self.base, self.scene)
            self.attached = True
            self.base.scene.dirty = True


def _cursor_browser_sh() -> Path | None:
    hinted = os.environ.get("BROWSER", "")
    if hinted.endswith("browser.sh") and Path(hinted).is_file():
        return Path(hinted)
    matches = sorted(
        Path.home().glob(".cursor-server/bin/**/helpers/browser.sh"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def _fresh_ipc_env() -> dict[str, str]:
    env = os.environ.copy()
    socks = sorted(
        Path(f"/run/user/{os.getuid()}").glob("vscode-ipc-*.sock"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if socks:
        env["VSCODE_IPC_HOOK_CLI"] = str(socks[0])
    return env


def open_in_cursor_browser(url: str) -> None:
    """Ask Cursor (Windows) to open the page; do not use the Linux browser."""
    time.sleep(1.2)
    helper = _cursor_browser_sh()
    if helper is None:
        print(f"未找到 Cursor browser.sh，请在 Ports 里打开 {url}")
        return
    try:
        subprocess.Popen(
            [str(helper), url],
            env=_fresh_ipc_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(f"自动打开页面失败: {exc}")


def _open_viewer_on_laptop() -> None:
    open_in_cursor_browser(VIEWER_URL)


def attach_scene(base: wvw.World, scene) -> None:
    if hasattr(scene, "attach"):
        scene.attach(base)
        return
    wssop.frame().add_to_scene(base.scene)
    scene.robot.add_to_scene(base.scene)
    scene.ground.add_to_scene(base.scene)


def run_viewer(
    scene,
    executor: WrsExecutor,
    inbox: Inbox | None = None,
    *,
    open_browser: bool = False,
    bridge: ViewerBridge | None = None,
    attach_now: bool = False,
) -> None:
    base = wvw.World(cam_pos=(2.2, 2.2, 1.7), cam_lookat_pos=(0.1, 0.1, 0.6))
    executor.view_scene = base.scene
    if bridge is None:
        attach_scene(base, scene)
    else:
        bridge.bind(base, scene)
        if attach_now:
            bridge.reveal()
    base.set_caption("rpent_wrs  G play/pause  F step  R reset  P 暂停管家")
    if open_browser:
        print("正在通过 Cursor 打开 WRS 画面…")
        threading.Thread(target=_open_viewer_on_laptop, daemon=True).start()

    def show_reset():
        if executor.motion is None:
            return
        executor.play_i = 0
        executor.playing = False
        if hasattr(executor, "reset_visual"):
            executor.reset_visual()
        executor.apply_frame(0)
        base.scene.dirty = True

    def tick(_dt):
        if base.is_key_pressed_edge(key.R):
            show_reset()
            return
        if base.is_key_pressed_edge(key.P) and inbox is not None:
            inbox.push("resume" if inbox.paused else "pause")
        if base.is_key_pressed_edge(key.G):
            executor.playing = not executor.playing
        if base.is_key_pressed_edge(key.F):
            executor.playing = False
            if executor.motion is not None and executor.play_i < len(executor.motion):
                executor.apply_frame(executor.play_i)
                executor.play_i += 1
                base.scene.dirty = True
            return
        if executor.tick_play():
            base.scene.dirty = True

    base.schedule_interval(tick, interval=0.03)
    base.run()

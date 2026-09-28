"""HTTP dashboard: launcher + live monitor + inbox."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from rpent_wrs.control import Inbox, check_instruction
from rpent_wrs.executor import WrsExecutor
from rpent_wrs.hub import EventHub
from rpent_wrs.planner import resolve_model, run_steward
from rpent_wrs.scene import DEFAULT_SCENE_ID, SCENE_CATALOG, build_scene, scene_spec
from rpent_wrs.store import RunStore
from rpent_wrs.viewer import VIEWER_URL, ViewerBridge

STATIC = Path(__file__).resolve().parent / "static"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


class Session:
    def __init__(
        self,
        executor: WrsExecutor,
        inbox: Inbox,
        hub: EventHub,
        *,
        animate: bool,
        defaults: dict[str, Any],
        viewer: ViewerBridge | None = None,
    ) -> None:
        self.executor = executor
        self.inbox = inbox
        self.hub = hub
        self.animate = animate
        self.defaults = defaults
        self.viewer = viewer
        self.status = "idle"
        self.result: dict[str, Any] | None = None
        self.store: RunStore | None = None
        self.scene_id = DEFAULT_SCENE_ID
        self.opened = False
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        model = resolve_model(self.defaults.get("model"))
        state = self.executor.view_state()
        spec = scene_spec(self.scene_id)
        revealed = (
            self.opened
            or bool(self.viewer and self.viewer.attached)
            or self.status != "idle"
        )
        return {
            "status": self.status,
            "model": model,
            "defaults": self.defaults,
            "scene_id": self.scene_id,
            "scene_title": spec["title"],
            "scenes": SCENE_CATALOG,
            "revealed": revealed,
            "scene": state,
            "inbox": self.inbox.snapshot(),
            "result": self.result,
            "run_dir": str(self.store.run_dir) if self.store else None,
            "viewer_url": VIEWER_URL,
        }

    def ensure_scene(self, scene_id: str | None = None) -> dict[str, Any]:
        spec = scene_spec(scene_id or self.scene_id)
        current = getattr(self.executor.scene, "scene_id", None) if self.executor.scene else None
        if current == spec["id"]:
            self.scene_id = spec["id"]
            self.inbox.scene_id = spec["id"]
            return spec
        if self.viewer and self.viewer.attached and current and current != spec["id"]:
            raise ValueError("换场景请先停掉 python main.py 再重新开。WRS 画面不能中途拆掉重搭。")
        scene = build_scene(spec["id"])
        self.executor.adopt_scene(scene, spec)
        self.scene_id = spec["id"]
        self.inbox.scene_id = spec["id"]
        if self.viewer is not None:
            self.viewer.scene = scene
            self.viewer.reveal()
        return spec

    def open_scene(
        self,
        scene_id: str | None = None,
        *,
        max_turns: int | None = None,
        timeout_s: float | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.status == "running":
                return {"ok": False, "error": "already running"}
            if max_turns is not None:
                self.defaults["max_turns"] = int(max_turns)
            if timeout_s is not None:
                self.defaults["timeout"] = float(timeout_s)
            if model:
                self.defaults["model"] = resolve_model(model)
        try:
            spec = self.ensure_scene(scene_id or self.scene_id)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        self.opened = True
        return {"ok": True, "scene_id": self.scene_id, "scene_title": spec["title"]}

    def start(
        self,
        *,
        instruction: str,
        max_turns: int,
        timeout_s: float,
        scripted: bool,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            spec = scene_spec(scene_id or self.scene_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        checked = check_instruction(instruction, spec)
        if not checked.get("ok"):
            return checked
        try:
            spec = self.ensure_scene(spec["id"])
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        with self._lock:
            if self.status == "running":
                return {"ok": False, "error": "already running"}
            self.scene_id = spec["id"]
            self.status = "running"
            self.result = None
            self.inbox.reset()
            self.inbox.scene_id = spec["id"]
            self.store = RunStore.create()
            self.executor.clear_clips()
            self.executor.apply_instruction(instruction)
        if self.viewer is not None:
            self.viewer.reveal()
        self.hub.emit(
            {
                "type": "start",
                "instruction": instruction,
                "scripted": scripted,
                "scene_id": self.scene_id,
                "scene_title": spec["title"],
                "goal": {
                    "object": self.executor.goal_object,
                    "place": self.executor.goal_place,
                },
            }
        )

        def work() -> None:
            store = self.store
            assert store is not None
            try:
                if scripted:
                    obj = self.executor.goal_object
                    place = self.executor.goal_place
                    tool_result = self.executor.pick_and_place(
                        obj, place, animate=self.animate
                    )
                    self.hub.emit(
                        {
                            "type": "tool_call",
                            "tool": "pick_and_place",
                            "args": {"object": obj, "place": place},
                        }
                    )
                    store.log_tool(
                        "pick_and_place",
                        {"object": obj, "place": place},
                        tool_result,
                    )
                    self.hub.emit(
                        {"type": "tool_result", "tool": "pick_and_place", "result": tool_result}
                    )
                    store.write_audit(
                        {
                            "ok": bool(tool_result.get("ok")),
                            "instruction": instruction,
                            "mode": "scripted",
                            "scene_success": self.executor.is_success(),
                            "solved": self.executor.is_success(),
                            "run_dir": str(store.run_dir),
                        }
                    )
                    result = {
                        "ok": bool(tool_result.get("ok")),
                        "scene_success": self.executor.is_success(),
                        "solved": self.executor.is_success(),
                        **tool_result,
                    }
                else:
                    result = run_steward(
                        self.executor,
                        instruction,
                        store,
                        self.inbox,
                        animate=self.animate,
                        max_turns=max_turns,
                        timeout_s=timeout_s,
                        model=self.defaults.get("model"),
                        on_event=self.hub.emit,
                    )
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "scene_success": self.executor.is_success(),
                    "solved": False,
                }
            self.result = result
            self.status = "succeeded" if result.get("solved") else "failed"
            self.hub.emit({"type": "done", **result, "status": self.status})

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "status": "running"}


def start_dashboard(session: Session, port: int = 8001) -> str:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            self._send(
                code,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
            data = json.loads(raw or "{}")
            return data if isinstance(data, dict) else {}

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in {"/", "/index.html"}:
                self._send(200, (STATIC / "index.html").read_bytes(), MIME[".html"])
                return
            if path.startswith("/static/"):
                name = Path(path).name
                target = (STATIC / name).resolve()
                if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
                    self._send(404, b"missing", "text/plain")
                    return
                self._send(200, target.read_bytes(), MIME.get(target.suffix, "application/octet-stream"))
                return
            if path == "/api/state":
                self._json(200, session.snapshot())
                return
            if path == "/api/events":
                after = int(parse_qs(parsed.query).get("after", ["0"])[0] or 0)
                self._json(200, {"events": session.hub.wait(after)})
                return
            self._send(404, b"missing", "text/plain")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/open":
                data = self._read_json()
                result = session.open_scene(
                    str(data.get("scene_id") or session.scene_id),
                    max_turns=int(data["max_turns"]) if data.get("max_turns") else None,
                    timeout_s=float(data["timeout"]) if data.get("timeout") else None,
                    model=str(data.get("model") or "") or None,
                )
                self._json(200 if result.get("ok") else 409, result)
                return
            if parsed.path == "/api/start":
                data = self._read_json()
                instruction = str(data.get("instruction") or "").strip()
                if not instruction:
                    self._json(400, {"ok": False, "error": "请先在监控页输入任务指令"})
                    return
                result = session.start(
                    instruction=instruction,
                    max_turns=int(data.get("max_turns") or session.defaults["max_turns"]),
                    timeout_s=float(data.get("timeout") or session.defaults["timeout"]),
                    scripted=bool(data.get("scripted")),
                    scene_id=str(data.get("scene_id") or session.scene_id),
                )
                self._json(200 if result.get("ok") else 409, result)
                return
            if parsed.path == "/api/inbox":
                data = self._read_json()
                result = session.inbox.push(str(data.get("text") or ""))
                if result.get("ok") and result.get("action") == "note":
                    session.hub.emit({"type": "operator", "text": result.get("text")})
                self._json(200, result)
                return
            if parsed.path == "/api/replay":
                data = self._read_json()
                if data.get("whole"):
                    result = session.executor.replay_clip(whole=True)
                else:
                    result = session.executor.replay_clip(int(data.get("id", -1)))
                self._json(200 if result.get("ok") else 400, result)
                return
            self._send(404, b"missing", "text/plain")

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"看板 {url}")
    return url

"""Operator inbox: pause / resume / change goal from terminal or a tiny web page."""

from __future__ import annotations

import html
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

OBJECT_ALIASES = {
    "bunny": "bunny",
    "兔子": "bunny",
    "cube": "cube",
    "方块": "cube",
    "立方体": "cube",
}
PLACE_ALIASES = {
    "place_a": "place_A",
    "placea": "place_A",
    "place_b": "place_B",
    "placeb": "place_B",
}

PAUSE_WORDS = {"pause", "暂停", "停"}
RESUME_WORDS = {"resume", "继续", "走"}
STOP_WORDS = {"quit", "stop", "结束", "退出"}

CONTROL_URL = "http://127.0.0.1:8001/"


PLACE_TYPOS = {
    "palce_a": "place_A",
    "palcea": "place_A",
    "plcae_a": "place_A",
    "palce_b": "place_B",
    "palceb": "place_B",
    "plcae_b": "place_B",
}
GOAL_HINT = re.compile(r"放|place|palce|plcae|兔子|方块|bunny|cube|立方体", re.I)


def _scene_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    if spec:
        return spec
    from rpent_wrs.scene import scene_spec

    return scene_spec()


def parse_goal(text: str, spec: dict[str, Any] | None = None) -> dict[str, str]:
    spec = _scene_spec(spec)
    raw = (text or "").strip()
    lower = raw.lower().replace(" ", "")
    found: dict[str, str] = {}
    object_aliases = spec.get("object_aliases") or OBJECT_ALIASES
    place_aliases = spec.get("place_aliases") or PLACE_ALIASES
    for alias, name in object_aliases.items():
        if alias.lower() in lower or alias in raw:
            found["object"] = name
    for alias, name in place_aliases.items():
        if alias.lower().replace(" ", "") in lower or alias in raw:
            found["place"] = name
    for place in spec.get("place_names") or ():
        if place in raw:
            found["place"] = place
    if spec.get("id") != "l1_cylinder":
        if "place_A" in raw:
            found["place"] = "place_A"
        if "place_B" in raw:
            found["place"] = "place_B"
    if "object" in found and "place" not in found and not spec.get("require_place", True):
        default_place = (spec.get("default_goal") or {}).get("place")
        if default_place:
            found["place"] = default_place
    return found


def check_instruction(text: str, spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reject incomplete or misspelled goals before the arm moves."""
    spec = _scene_spec(spec)
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "error": "指令是空的。"}
    lower = raw.lower().replace(" ", "")
    parsed = parse_goal(raw, spec)
    if spec.get("id") == "l1_cylinder" and re.search(r"place[_ ]?[ab]|placea|placeb", lower):
        return {
            "ok": False,
            "error": "这个场景没有 place_A / place_B。请写「拿起圆柱」或「把圆柱举起来」。还没有启动。",
        }
    if spec.get("id") != "l1_cylinder":
        for typo, correct in PLACE_TYPOS.items():
            if typo in lower:
                return {
                    "ok": False,
                    "error": f"放置点写错了：你写的是 {typo}，请改成 {correct}。还没有启动。",
                }
    need_place = bool(spec.get("require_place", True))
    if "object" in parsed and (not need_place or "place" in parsed):
        return {"ok": True, "goal": parsed}
    missing = []
    if "object" not in parsed:
        missing.append(str(spec.get("missing_object") or "物体"))
    if need_place and "place" not in parsed:
        missing.append(str(spec.get("missing_place") or "放置点"))
    return {
        "ok": False,
        "error": (
            "指令没认全，缺少"
            + "、".join(missing)
            + f"。你输入的是「{raw}」。请改完再启动，机械臂不会动。"
        ),
        "parsed": parsed,
    }


class Inbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.paused = False
        self.stop = False
        self.notes: list[str] = []
        self.status = "running"
        self.scene_id = "rs007l_pickplace"

    def push(self, text: str) -> dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            return {"ok": False, "error": "empty"}
        key = raw.lower()
        with self._lock:
            if key in PAUSE_WORDS:
                self.paused = True
                self.status = "paused"
                return {"ok": True, "action": "pause", "paused": True}
            if key in RESUME_WORDS:
                self.paused = False
                self.status = "running"
                return {"ok": True, "action": "resume", "paused": False}
            if key in STOP_WORDS:
                self.stop = True
                self.paused = False
                self.status = "stop"
                return {"ok": True, "action": "stop"}
            from rpent_wrs.scene import scene_spec

            spec = scene_spec(self.scene_id)
            pattern = spec.get("goal_pattern")
            hint = re.compile(pattern, re.I) if pattern else GOAL_HINT
            if hint.search(raw):
                checked = check_instruction(raw, spec)
                if not checked.get("ok"):
                    return checked
            self.notes.append(raw)
            self.paused = False
            self.status = "running"
            return {"ok": True, "action": "note", "text": raw}

    def drain(self) -> list[str]:
        with self._lock:
            notes = list(self.notes)
            self.notes.clear()
            return notes

    def reset(self) -> None:
        with self._lock:
            self.paused = False
            self.stop = False
            self.notes.clear()
            self.status = "running"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "paused": self.paused,
                "stop": self.stop,
                "pending": len(self.notes),
                "status": self.status,
            }

    def wait_if_paused(self) -> None:
        while True:
            with self._lock:
                if self.stop or not self.paused:
                    return
            time.sleep(0.2)


def start_terminal_reader(inbox: Inbox) -> None:
    def loop() -> None:
        print("插话：终端输入 pause / resume / quit，或直接打新目标（例如 把方块放到 place_B）")
        while not inbox.stop:
            try:
                line = input()
            except EOFError:
                return
            except Exception:
                return
            if inbox.stop:
                return
            result = inbox.push(line)
            print(f"inbox {result}")

    threading.Thread(target=loop, daemon=True).start()


_PAGE = """<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8"/>
  <title>rpent_wrs 插话</title>
  <style>
    body {{ font-family: sans-serif; max-width: 36rem; margin: 2rem auto; }}
    input[type=text] {{ width: 100%; padding: .4rem; }}
    button {{ margin: .3rem .3rem 0 0; padding: .3rem .7rem; }}
    .s {{ color: #444; }}
  </style>
</head>
<body>
  <h1>管家插话</h1>
  <p class="s">状态：{status}　画面仍是 :8000，这里只改任务。</p>
  <form method="post">
    <input type="text" name="text" placeholder="把方块放到 place_B" autofocus/>
    <p>
      <button type="submit">发送</button>
      <button name="text" value="pause">暂停</button>
      <button name="text" value="resume">继续</button>
      <button name="text" value="quit">结束</button>
    </p>
  </form>
  <p class="s">上次：{last}</p>
</body>
</html>
"""


def start_control_server(inbox: Inbox, port: int = 8001) -> str:
    last = {"text": "（还没有）"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _page(self) -> bytes:
            snap = inbox.snapshot()
            return _PAGE.format(
                status=html.escape(str(snap["status"])),
                last=html.escape(last["text"]),
            ).encode("utf-8")

        def do_GET(self) -> None:
            body = self._page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            text = (parse_qs(raw).get("text") or [""])[0]
            result = inbox.push(text)
            last["text"] = f"{text} -> {result.get('action')}"
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"插话页 {url}  （pause / 改目标）")
    return url

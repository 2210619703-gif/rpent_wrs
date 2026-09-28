"""Entry logic. Prefer: python main.py"""

from __future__ import annotations

import argparse
import json
import threading
import time

from rpent_wrs.control import Inbox, start_terminal_reader
from rpent_wrs.dashboard import Session, start_dashboard
from rpent_wrs.executor import WrsExecutor
from rpent_wrs.hub import EventHub
from rpent_wrs.scene import DEFAULT_SCENE_ID
from rpent_wrs.settings import apply_local_config
from rpent_wrs.viewer import ViewerBridge, open_in_cursor_browser


def _parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="管家 + WRS 抓放")
    ap.add_argument("--instruction", default=None, help="跳过启动页，直接用这条指令")
    ap.add_argument("--scene", default=None, help="场景 id，例如 rs007l_pickplace / l1_cylinder")
    ap.add_argument("--scripted", action="store_true", help="不调管家，直接抓放")
    ap.add_argument("--no-viewer", action="store_true", help="不要网页")
    ap.add_argument("--no-dashboard", action="store_true", help="只要 WRS 画面，不要看板")
    ap.add_argument("--max-turns", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=120.0, help="LLM HTTP timeout seconds")
    ap.add_argument("--dashboard-port", type=int, default=8001, help="看板端口")
    return ap.parse_args()


def _default_instruction(scene_id: str) -> str:
    return "拿起圆柱" if scene_id == "l1_cylinder" else "把兔子放到 place_A"


def _ask_instruction(scene_id: str) -> str:
    try:
        text = input("请输入指令: ").strip()
    except EOFError:
        text = ""
    return text or _default_instruction(scene_id)


def main() -> int:
    apply_local_config()
    args = _parse()
    print("正在准备场景目录…")
    inbox = Inbox()
    hub = EventHub()
    viewer = ViewerBridge()
    executor = WrsExecutor(None, inbox=inbox)
    animate = not args.no_viewer
    session = Session(
        executor,
        inbox,
        hub,
        animate=animate,
        defaults={"max_turns": args.max_turns, "timeout": args.timeout},
        viewer=viewer,
    )
    session.scene_id = args.scene or DEFAULT_SCENE_ID
    inbox.scene_id = session.scene_id

    if args.no_viewer:
        opened = session.open_scene(session.scene_id)
        if not opened.get("ok"):
            print(opened)
            return 1
        instruction = args.instruction or _default_instruction(session.scene_id)
        started = session.start(
            instruction=instruction,
            max_turns=args.max_turns,
            timeout_s=args.timeout,
            scripted=args.scripted,
        )
        if not started.get("ok"):
            print(started)
            return 1
        while session.status == "running":
            time.sleep(0.2)
        print(json.dumps(session.result or {}, ensure_ascii=False, indent=2))
        return 0 if (session.result or {}).get("solved") else 1

    if not args.no_dashboard:
        dash_url = start_dashboard(session, port=args.dashboard_port)
        start_terminal_reader(inbox)
        print("正在通过 Cursor 打开看板…")
        threading.Thread(target=open_in_cursor_browser, args=(dash_url,), daemon=True).start()
        if args.instruction or args.scripted:
            instruction = args.instruction or _default_instruction(session.scene_id)
            session.start(
                instruction=instruction,
                max_turns=args.max_turns,
                timeout_s=args.timeout,
                scripted=args.scripted,
            )
    else:
        instruction = (
            _default_instruction(session.scene_id)
            if args.scripted
            else (args.instruction or _ask_instruction(session.scene_id))
        )
        start_terminal_reader(inbox)
        session.start(
            instruction=instruction,
            max_turns=args.max_turns,
            timeout_s=args.timeout,
            scripted=args.scripted,
        )

    from rpent_wrs.viewer import run_viewer

    run_viewer(
        None,
        executor,
        inbox,
        open_browser=args.no_dashboard,
        bridge=viewer,
        attach_now=args.no_dashboard,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

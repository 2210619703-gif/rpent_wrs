"""OpenAI-compatible tool loop (same GLM API style as the RPent steward)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from rpent_wrs import prompts
from rpent_wrs.control import Inbox
from rpent_wrs.executor import WrsExecutor
from rpent_wrs.store import RunStore

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "view_state",
            "description": "Read object/place poses, held state, goal, and scene_success.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pose",
            "description": "World xyz of a named object or place.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "bunny | cube | place_A | place_B | flange | wall | ground",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files under memory/ (default) or this run/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "memory | memory/<name> | run",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": "Read a UTF-8 note or run file. Paths stay under memory/ or run/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_text_file",
            "description": "Write a memory note or run artifact. Does not create new skills.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "e.g. memory/wall.md or a file under run/",
                    },
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_grasp",
            "description": "Sample antipodal grasps for a named object.",
            "parameters": {
                "type": "object",
                "properties": {
                    "object": {"type": "string", "description": "bunny | cube"},
                },
                "required": ["object"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_to",
            "description": (
                "Move the arm. If target is an object, approach a grasp. "
                "If target is a place and something is held, carry it there."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "bunny | cube | place_A | place_B",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_gripper",
            "description": "Hold the approached object (WRS hold).",
            "parameters": {
                "type": "object",
                "properties": {
                    "object": {"type": "string", "description": "bunny | cube; default last move_to object"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "release",
            "description": "Drop the held object (WRS release).",
            "parameters": {
                "type": "object",
                "properties": {
                    "object": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pick_and_place",
            "description": "One-shot WRS pick-and-place. Prefer primitives when composing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "object": {"type": "string", "description": "bunny | cube"},
                    "place": {"type": "string", "description": "place_A | place_B"},
                },
                "required": ["object", "place"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "End the run. success=true is rejected unless scene_success is true. "
                "If unsuccessful, message must say why."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string"},
                },
                "required": ["success"],
            },
        },
    },
]

NUDGE = (
    "You must call a tool. Use view_state, get_pose, plan_grasp, move_to, "
    "close_gripper, release, pick_and_place, the file tools, or finish. "
    "A text-only reply does not complete the task."
)


class StewardFinished(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__(payload.get("message", ""))
        self.payload = payload


def _tool_call_id(item: dict[str, Any], index: int) -> str:
    return str(item.get("id") or f"call_{index}")


def _tool_name_and_args(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if "function" in item:
        fn = item["function"]
        name = fn.get("name", "")
        raw = fn.get("arguments") or "{}"
    else:
        name = item.get("name", "")
        raw = item.get("arguments") or item.get("input") or "{}"
    if isinstance(raw, dict):
        args = raw
    else:
        args = json.loads(raw) if str(raw).strip() else {}
    return name, args


def dispatch(
    executor: WrsExecutor,
    store: RunStore,
    name: str,
    args: dict[str, Any],
    *,
    animate: bool,
) -> dict[str, Any]:
    if name == "view_state":
        return executor.view_state()
    if name == "get_pose":
        return executor.get_pose(str(args.get("name", "")))
    if name == "list_dir":
        return store.list_dir(str(args.get("path") or "memory"))
    if name == "read_text_file":
        return store.read_text(
            str(args.get("path") or ""),
            int(args.get("max_chars") or 0) or 40_000,
        )
    if name == "write_text_file":
        return store.write_text(str(args.get("path") or ""), str(args.get("content") or ""))
    if name == "plan_grasp":
        return executor.plan_grasp(str(args.get("object") or args.get("obj") or ""))
    if name == "move_to":
        target = str(args.get("target") or args.get("name") or args.get("place") or "")
        return executor.move_to(target, animate=animate)
    if name == "close_gripper":
        return executor.close_gripper(str(args.get("object") or ""), animate=animate)
    if name == "release":
        return executor.release(str(args.get("object") or ""), animate=animate)
    if name == "pick_and_place":
        obj = str(args.get("object") or args.get("obj") or "")
        place = str(args.get("place") or "")
        return executor.pick_and_place(obj, place, animate=animate)
    if name == "finish":
        claimed = bool(args.get("success"))
        scene = executor.is_success()
        message = str(args.get("message") or "").strip()
        if claimed and not scene:
            return {
                "error": "scene_success is false; finish.success=true was rejected",
                "scene_success": False,
                "claimed_success": True,
            }
        if not claimed and not message:
            return {
                "error": "finish.success=false requires message with the reason",
                "scene_success": scene,
            }
        raise StewardFinished(
            {
                "_finish": True,
                "claimed_success": claimed,
                "scene_success": scene,
                "solved": scene,
                "message": message,
            }
        )
    return {"error": f"unknown tool: {name}"}


def _post_chat(url: str, key: str, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(2):
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("choices"):
                return data
            last = RuntimeError(f"empty planner response: {data}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last = RuntimeError(f"planner HTTP {exc.code}: {detail}")
            if exc.code not in {429, 500, 502, 503}:
                raise last from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = RuntimeError(f"planner request failed: {exc}")
        if attempt == 0:
            time.sleep(1.0)
    assert last is not None
    raise last


def _message_from_choice(choice: dict[str, Any]) -> dict[str, Any]:
    return dict(choice.get("message") or {})


def _end(
    store: RunStore,
    executor: WrsExecutor,
    instruction: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "instruction": instruction,
        "run_dir": str(store.run_dir),
        "scene_success": executor.is_success(),
        "solved": False,
        **extra,
    }
    payload["solved"] = bool(payload.get("scene_success"))
    store.write_audit(payload)
    payload["audit"] = str(store.audit_path)
    if payload["solved"] and store.recipe_path.exists():
        payload["recipe"] = str(store.recipe_path)
    payload["transcript"] = str(store.transcript_path)
    return payload


def _note(store: RunStore, on_event: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
    store.log_event(payload)
    if on_event is not None:
        on_event(payload)


def resolve_model(name: str | None = None) -> str:
    raw = (name or "").strip()
    if raw.startswith("openai-chat:"):
        raw = raw.split(":", 1)[1]
    elif raw.startswith("openai:") or raw.startswith("anthropic:"):
        raw = raw.split(":", 1)[1]
    if raw:
        return raw
    return os.environ.get("RPENT_WRS_MODEL") or os.environ.get("OPENAI_MODEL") or "glm-4.5"


def run_steward(
    executor: WrsExecutor,
    instruction: str,
    store: RunStore,
    inbox: Inbox | None = None,
    *,
    animate: bool,
    max_turns: int = 16,
    timeout_s: float = 120.0,
    model: str | None = None,
    log: Callable[[str], None] = print,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    from rpent_wrs.settings import apply_local_config

    apply_local_config()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY 为空。请编辑 /home/fafu/Fishlj/rpent_wrs/config.py"
        )
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = resolve_model(model)
    url = f"{base}/chat/completions"

    from rpent_wrs.scene import scene_spec

    spec = scene_spec(getattr(executor.scene, "scene_id", None) if executor.scene else None)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompts.system_for(spec)},
        {"role": "user", "content": prompts.user_message(instruction, store.memory_listing(), spec)},
    ]
    _note(store, on_event, {"type": "start", "model": model, "instruction": instruction})
    log(f"steward model={model}  instruction={instruction!r}")
    log(f"run dir {store.run_dir}")

    for turn in range(1, max_turns + 1):
        if inbox is not None:
            inbox.wait_if_paused()
            if inbox.stop:
                return _end(
                    store,
                    executor,
                    instruction,
                    {"ok": False, "error": "operator stop"},
                )
            notes = inbox.drain()
            for note in notes:
                goal = executor.apply_instruction(note)
                injected = prompts.operator_message(note, goal)
                messages.append({"role": "user", "content": injected})
                _note(store, on_event, {"type": "operator", "text": note, "goal": goal})
                log(f"operator: {note} -> goal {goal}")
        log(f"--- turn {turn}/{max_turns} ---")
        _note(store, on_event, {"type": "turn", "n": turn, "max": max_turns})
        data = _post_chat(
            url,
            key,
            {
                "model": model,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
            },
            timeout_s,
        )
        choice = (data.get("choices") or [None])[0]
        if not choice:
            raise RuntimeError(f"empty planner response: {data}")
        message = _message_from_choice(choice)
        messages.append(message)
        text = message.get("content")
        if text:
            rendered = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
            log(f"steward: {rendered}")
            _note(store, on_event, {"type": "model", "content": rendered})
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            log("steward returned no tool call; nudging")
            _note(store, on_event, {"type": "nudge"})
            messages.append({"role": "user", "content": NUDGE})
            continue
        for i, call in enumerate(tool_calls):
            name, args = _tool_name_and_args(call)
            log(f"tool {name} {json.dumps(args, ensure_ascii=False)}")
            if on_event is not None:
                on_event({"type": "tool_call", "tool": name, "args": args})
            if inbox is not None:
                inbox.wait_if_paused()
                if inbox.stop:
                    return _end(
                        store,
                        executor,
                        instruction,
                        {"ok": False, "error": "operator stop"},
                    )
            try:
                result = dispatch(executor, store, name, args, animate=animate)
            except StewardFinished as done:
                store.log_tool(name, args, done.payload)
                if on_event is not None:
                    on_event({"type": "tool_result", "tool": name, "result": done.payload})
                log(f"finish {json.dumps(done.payload, ensure_ascii=False)}")
                return _end(
                    store,
                    executor,
                    instruction,
                    {"ok": True, **done.payload},
                )
            store.log_tool(name, args, result)
            if on_event is not None:
                on_event({"type": "tool_result", "tool": name, "result": result})
            log(f"  -> {json.dumps(result, ensure_ascii=False)}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": _tool_call_id(call, i),
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
    return _end(
        store,
        executor,
        instruction,
        {"ok": False, "error": "max turns reached"},
    )

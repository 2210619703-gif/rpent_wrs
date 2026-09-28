SYSTEM_HEAD = """You are a robot steward. Execution is WRS (geometry planning), not a VLA.
You may read object poses from the scene. There is no camera and no SAM3.
"""

SYSTEM_TOOLS_COMMON = """
Tools:
- view_state: poses, held, current goal, scene_success
- get_pose: xyz of a named object or place
- list_dir / read_text_file / write_text_file: memory notes and this run's files
- finish: call when done or when you cannot continue
"""

SYSTEM_TAIL = """
Memory:
- Notes live under memory/. They do not create new skills.
- If notes exist, read the relevant ones before acting.
- After a failure you cannot recover from, write a short markdown note
  (what failed, what to try next time), then finish.

Rules:
- Do not invent object or place names. Names come from view_state.
- An operator may inject a new instruction mid-run. Switch to that goal.
- scene_success is ground truth for the CURRENT goal.
- finish.success=true is only accepted when scene_success is true.
- If unsuccessful, finish.message must say why.
"""


def _spec(spec: dict | None) -> dict:
    if spec:
        return spec
    from rpent_wrs.scene import scene_spec

    return scene_spec()


def system_for(spec: dict | None = None) -> str:
    spec = _spec(spec)
    scene = spec.get("prompt_scene") or (
        "- One arm (RS007L) with a gripper\n"
        "- Objects: bunny, cube\n"
        "- Places: place_A, place_B\n"
        "- A wall sits between some transfers; WRS will route around it"
    )
    tools = spec.get("prompt_tools") or (
        "- plan_grasp: sample grasps for bunny or cube\n"
        "- move_to: approach an object, or carry/hover to a place\n"
        "- close_gripper: hold the approached object\n"
        "- release: drop the held object\n"
        "- pick_and_place: one-shot shortcut (still valid)"
    )
    typical = spec.get("prompt_typical") or (
        "plan_grasp -> move_to(object) -> close_gripper -> move_to(place) -> release\n"
        "Use pick_and_place if you want WRS to do the whole transfer."
    )
    return (
        SYSTEM_HEAD
        + "\nScene:\n"
        + scene
        + SYSTEM_TOOLS_COMMON
        + tools
        + "\n\nTypical composition:\n"
        + typical
        + SYSTEM_TAIL
    )


SYSTEM = system_for()


def user_message(
    instruction: str,
    memory_files: list[dict[str, str]] | None = None,
    spec: dict | None = None,
) -> str:
    spec = _spec(spec)
    lines = [
        f"Task: {instruction}",
        "Inspect the scene, compose primitives or pick_and_place, then finish.",
        spec.get("prompt_objects") or "Objects: bunny, cube. Places: place_A, place_B.",
    ]
    files = memory_files or []
    if files:
        lines.append("Existing memory files:")
        for item in files:
            preview = item.get("preview") or ""
            extra = f" — {preview}" if preview else ""
            lines.append(f"- {item.get('path')}{extra}")
        lines.append("Read any that might apply before acting.")
    else:
        lines.append("No memory notes yet.")
    return "\n".join(lines)


def operator_message(text: str, goal: dict[str, str]) -> str:
    return (
        f"[operator] {text}\n"
        f"Updated goal: move {goal.get('object')} to {goal.get('place')}. "
        "scene_success is now this goal. Continue or finish."
    )

"""Split a WRS pick-and-place path into named clips for replay."""

from __future__ import annotations

from typing import Any

import numpy as np
from wrs.motion.core.motion_data import MotionData

CLIP_TITLES = {
    "approach": "approach 接近",
    "pick": "pick 抓取",
    "depart": "depart 抬起",
    "move": "move 搬运",
    "place": "place 放下",
    "retreat": "retreat 退回",
    "hover": "hover 悬停",
}


def slice_motion(motion: MotionData, start: int, end: int) -> MotionData:
    start = max(0, start)
    end = min(len(motion), end)
    if end <= start:
        return MotionData()
    return MotionData(
        motion.robot_qpos_list[start:end],
        motion.ee_qpos_list[start:end],
        motion.obj_pose_list[start:end],
    )


def concat_motions(parts: list[MotionData]) -> MotionData | None:
    out = None
    for part in parts:
        if part is None or part.is_empty():
            continue
        out = part if out is None else out + part
    return out


def _jaw(ee: Any) -> float | None:
    if ee is None:
        return None
    arr = np.asarray(ee, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        return None
    return float(arr[0])


def _z_at(motion: MotionData, i: int) -> float | None:
    pose = motion.obj_pose_list[i]
    if pose is None:
        return None
    return float(np.asarray(pose, dtype=np.float32)[2, 3])


def split_pick_place(motion: MotionData) -> list[tuple[str, int, int]]:
    n = len(motion)
    if n <= 0:
        return []
    if n < 8:
        return [("move", 0, n)]

    jaws = [_jaw(ee) for ee in motion.ee_qpos_list]
    known = [j for j in jaws if j is not None]
    if len(known) < 4:
        return _even_split(n)

    hi, lo = max(known), min(known)
    if hi - lo < 1e-4:
        return _even_split(n)
    closed_at = lo + 0.4 * (hi - lo)
    closed = [j is not None and j <= closed_at for j in jaws]
    if not any(closed):
        return _even_split(n)

    grasp_idx = closed.index(True)
    release_idx = n - 1 - closed[::-1].index(True)
    if release_idx <= grasp_idx:
        release_idx = min(n - 1, grasp_idx + max(2, (n - grasp_idx) // 2))

    mid_start = min(n, grasp_idx + 1)
    mid_end = max(mid_start, release_idx)
    peak = mid_start
    best_z = -1e9
    for i in range(mid_start, mid_end):
        z = _z_at(motion, i)
        if z is not None and z >= best_z:
            best_z = z
            peak = i
    if peak <= mid_start:
        peak = mid_start + max(1, (mid_end - mid_start) // 3)
    descend = peak
    start_z = _z_at(motion, peak)
    if start_z is not None:
        for i in range(peak, mid_end):
            z = _z_at(motion, i)
            if z is not None and z < start_z - 0.02:
                descend = i
                break
        else:
            descend = mid_start + (2 * (mid_end - mid_start)) // 3
    else:
        descend = mid_start + (2 * (mid_end - mid_start)) // 3

    raw = [
        ("approach", 0, grasp_idx),
        ("pick", grasp_idx, min(n, grasp_idx + 2)),
        ("depart", mid_start, peak),
        ("move", peak, descend),
        ("place", descend, min(n, release_idx + 1)),
        ("retreat", min(n, release_idx + 1), n),
    ]
    return _clean(raw, n)


def _even_split(n: int) -> list[tuple[str, int, int]]:
    names = ("approach", "pick", "depart", "move", "place")
    cuts = [round(i * n / len(names)) for i in range(len(names) + 1)]
    return _clean([(names[i], cuts[i], cuts[i + 1]) for i in range(len(names))], n)


def _clean(raw: list[tuple[str, int, int]], n: int) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for name, start, end in raw:
        start = max(0, min(n, start))
        end = max(0, min(n, end))
        if end - start < 2:
            continue
        if out and out[-1][2] > start:
            start = out[-1][2]
        if end - start < 2:
            continue
        out.append((name, start, end))
    if not out:
        return [("move", 0, n)]
    return out

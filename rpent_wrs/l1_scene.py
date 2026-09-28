"""L1O6 table + cylinder, same layout as WRS examples/l1picking/l1picking.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

import numpy as np

from rpent_wrs.scene import default_wrs_root

SCENE_ID = "l1_cylinder"


def _l1picking(wrs_root: Path | None = None):
    path = (wrs_root or default_wrs_root()) / "examples/l1picking/l1picking.py"
    if not path.is_file():
        raise FileNotFoundError(f"missing {path}")
    return SourceFileLoader("wrs_l1picking_mod", str(path)).load_module()


@dataclass
class L1CylinderScene:
    scene_id: str
    robot: object
    cylinder: object
    ground: object
    table: list
    collider: object
    ctx: object
    planner: object
    jaw: object
    grasps: list
    mod: Any
    places: dict = field(default_factory=dict)
    movables: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    gripper: object | None = None
    bunny: object | None = None
    cube: object | None = None
    wall: object | None = None
    tf_pick: np.ndarray | None = None
    tf_place: np.ndarray | None = None

    def attach(self, base) -> None:
        import wrs.scene.scene_object_primitive as wssop

        wssop.frame().add_to_scene(base.scene)
        self.robot.add_to_scene(base.scene)
        self.ground.add_to_scene(base.scene)
        for part in self.table:
            part.add_to_scene(base.scene)
        self.cylinder.add_to_scene(base.scene)

    def plan_pick(self) -> dict[str, Any]:
        gi, motion = self.mod.first_reachable(
            self.robot, self.ctx, self.planner, self.jaw, self.grasps, start=0
        )
        if motion is None:
            return {"ok": False, "error": "no feasible L1 cylinder grasp"}
        traj, grasp_idx, amount = motion
        return {
            "ok": True,
            "traj": traj,
            "grasp_idx": int(grasp_idx),
            "amount": float(amount),
            "grasp": int(gi),
            "waypoints": len(traj),
        }


def build_l1_cylinder_scene(wrs_root: Path | None = None) -> L1CylinderScene:
    json_path = (wrs_root or default_wrs_root()) / "examples/l1picking/o6_cylinder_grasps.json"
    if not json_path.is_file():
        raise FileNotFoundError(
            f"缺少 {json_path}。先在 WRS 里跑 ONE_HEADLESS=1 python examples/l1picking/o6planning.py"
        )
    mod = _l1picking(wrs_root)
    robot, table, cyl, ground = mod.build_scene()
    cyl.name = "cylinder"
    collider = mod.make_collider(robot, table, cyl, ground)
    ctx = mod.chain_planning_context(robot, collider, mod.CHAIN)
    import wrs.motion.probabilistic.rrt as wmpr

    planner = wmpr.RRTConnectPlanner(
        pln_ctx=ctx, extend_step_size=np.pi / 36, goal_bias=0.3
    )
    jaw = robot.left_hand.as_jaw(mod.PRIMITIVE)
    grasps = mod.load_world_grasps(cyl)
    lift = np.eye(4, dtype=np.float32)
    lift[:3, 3] = np.array(cyl.pos, dtype=np.float32) + np.array(mod.UP, dtype=np.float32)
    return L1CylinderScene(
        scene_id=SCENE_ID,
        robot=robot,
        cylinder=cyl,
        ground=ground,
        table=table,
        collider=collider,
        ctx=ctx,
        planner=planner,
        jaw=jaw,
        grasps=grasps,
        mod=mod,
        places={"lift": lift},
        movables={"cylinder": cyl},
        names={"cylinder": cyl, "ground": ground},
        gripper=robot.left_hand,
    )

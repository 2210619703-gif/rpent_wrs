"""Named WRS scenes. Add a catalog entry when a new table is wired up."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import wrs.geom.fitting as wgf
import wrs.geom.surface as wgs
import wrs.grasp.placement as wgp
import wrs.utils.constant as wuc
import wrs.utils.math as wum
from wrs import khi_rs007l, or_2fg7
from wrs.scene import scene_object as wsso
from wrs.scene import scene_object_primitive as wssop


def default_wrs_root() -> Path:
    env = os.environ.get("WRS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "WRS"


DEFAULT_SCENE_ID = "rs007l_pickplace"
L1_SCENE_ID = "l1_cylinder"

SCENE_CATALOG: list[dict] = [
    {
        "id": DEFAULT_SCENE_ID,
        "title": "RS007L 抓放",
        "summary": "Kawasaki RS007L + OnRobot 2FG7。桌上有兔子和方块，放置点 place_A / place_B，中间有墙。",
        "objects": ["bunny（兔子）", "cube（方块）"],
        "places": ["place_A", "place_B"],
        "examples": ["把兔子放到 place_A", "把方块放到 place_B"],
        "instruction_note": "物体写 兔子 / bunny 或 方块 / cube；放置点必须是 place_A 或 place_B。运行中可 pause / resume，或直接打新目标。",
        "object_names": ["bunny", "cube"],
        "place_names": ["place_A", "place_B"],
        "object_aliases": {
            "bunny": "bunny",
            "兔子": "bunny",
            "cube": "cube",
            "方块": "cube",
            "立方体": "cube",
        },
        "place_aliases": {
            "place_a": "place_A",
            "placea": "place_A",
            "place_b": "place_B",
            "placeb": "place_B",
        },
        "require_place": True,
        "default_goal": {"object": "bunny", "place": "place_A"},
        "goal_pattern": r"放|place|palce|plcae|兔子|方块|bunny|cube|立方体",
        "missing_object": "物体（兔子 / bunny，或方块 / cube）",
        "missing_place": "放置点（place_A 或 place_B）",
        "prompt_scene": (
            "- One arm (RS007L) with a gripper\n"
            "- Objects: bunny, cube\n"
            "- Places: place_A, place_B\n"
            "- A wall sits between some transfers; WRS will route around it"
        ),
        "prompt_tools": (
            "- plan_grasp: sample grasps for bunny or cube\n"
            "- move_to: approach an object, or carry/hover to a place\n"
            "- close_gripper: hold the approached object\n"
            "- release: drop the held object\n"
            "- pick_and_place: one-shot shortcut (still valid)"
        ),
        "prompt_typical": (
            "plan_grasp -> move_to(object) -> close_gripper -> move_to(place) -> release\n"
            "Use pick_and_place if you want WRS to do the whole transfer."
        ),
        "prompt_objects": "Objects: bunny, cube. Places: place_A, place_B.",
    },
    {
        "id": L1_SCENE_ID,
        "title": "L1 捡圆柱",
        "summary": "L1O6 左臂 + O6 手。桌上立着一根圆柱。任务是拿起并举起，没有 place_A / place_B。",
        "objects": ["cylinder（圆柱）"],
        "places": ["lift（举起 / 拿起）"],
        "examples": ["拿起圆柱", "把圆柱举起来"],
        "instruction_note": "物体写 圆柱 / cylinder。这个场景没有 place_A / place_B。运行中可 pause / resume。",
        "object_names": ["cylinder"],
        "place_names": ["lift"],
        "object_aliases": {
            "cylinder": "cylinder",
            "圆柱": "cylinder",
            "圆筒": "cylinder",
        },
        "place_aliases": {
            "lift": "lift",
            "举起": "lift",
            "拿起": "lift",
            "捡起": "lift",
            "抬起": "lift",
        },
        "require_place": False,
        "default_goal": {"object": "cylinder", "place": "lift"},
        "goal_pattern": r"圆柱|cylinder|圆筒|拿起|举起|捡起|抬起|lift",
        "missing_object": "物体（圆柱 / cylinder）",
        "missing_place": "动作（拿起 / 举起）",
        "prompt_scene": (
            "- One humanoid arm (L1O6 left) with an O6 hand\n"
            "- Object: cylinder standing on a table\n"
            "- Goal place name is lift (pick and raise). There is no place_A or place_B"
        ),
        "prompt_tools": (
            "- plan_grasp: report precomputed cylinder grasps\n"
            "- pick_and_place: pick cylinder and lift it (object=cylinder, place=lift)"
        ),
        "prompt_typical": "pick_and_place(object=cylinder, place=lift), then finish.",
        "prompt_objects": "Object: cylinder. Place: lift (raise after grasp). No place_A / place_B.",
    },
]


def scene_spec(scene_id: str | None = None) -> dict:
    wanted = scene_id or DEFAULT_SCENE_ID
    for item in SCENE_CATALOG:
        if item["id"] == wanted:
            return item
    known = ", ".join(item["id"] for item in SCENE_CATALOG)
    raise ValueError(f"未知场景 {wanted!r}。现有：{known}")


def bunny_stl_path(wrs_root: Path | None = None) -> Path:
    path = (wrs_root or default_wrs_root()) / "bunny.stl"
    if not path.is_file():
        raise FileNotFoundError(
            f"bunny.stl not found at {path}. Set WRS_ROOT to the WRS repo."
        )
    return path


@dataclass
class BunnyScene:
    robot: object
    gripper: object
    bunny: object
    cube: object
    ground: object
    wall: object
    collider: object
    tf_pick: np.ndarray
    tf_place: np.ndarray
    places: dict[str, np.ndarray] = field(default_factory=dict)
    movables: dict[str, object] = field(default_factory=dict)
    names: dict[str, object] = field(default_factory=dict)
    scene_id: str = DEFAULT_SCENE_ID

    @property
    def place_pos(self) -> np.ndarray:
        return self.places["place_A"][:3, 3].astype(np.float32)

    def attach(self, base) -> None:
        wssop.frame().add_to_scene(base.scene)
        self.robot.add_to_scene(base.scene)
        self.ground.add_to_scene(base.scene)
        self.wall.add_to_scene(base.scene)
        self.cube.add_to_scene(base.scene)
        wssop.frame(
            pos=self.tf_pick[:3, 3],
            rotmat=self.tf_pick[:3, :3],
        ).add_to_scene(base.scene)
        for tf in self.places.values():
            wssop.frame(pos=tf[:3, 3], rotmat=tf[:3, :3]).add_to_scene(base.scene)
        self.bunny.add_to_scene(base.scene)


def build_rs007l_pickplace(wrs_root: Path | None = None) -> BunnyScene:
    """Bunny layout matches WRS examples/test_rs007l_pickplace_bunny.py, plus cube / place_B."""
    np.random.seed(7)
    robot = khi_rs007l.RS007L()
    gripper = or_2fg7.OR2FG7()
    robot.mount(gripper, robot.runtime_lnks[-1], update=True)

    bunny = wsso.SceneObject.from_file(
        str(bunny_stl_path(wrs_root)),
        collision_type=wuc.CollisionType.MESH,
        is_floating=True,
    )
    bunny.name = "bunny"
    ground = wssop.plane(pos=(0, 0, 0.01))
    wall = wssop.box(
        pos=(0.0, 0.7, 0.10),
        xyz_lengths=(0.05, 1.0, 0.3),
        name="wall",
        collision_type=wuc.CollisionType.AABB,
    )
    wall.rgb = (0.7, 0.4, 0.4)

    hull = wgf.convex_hull(bunny.collisions[0].geom)
    stable = wgp.compute_stable_poses(
        hull.vs,
        hull.fs,
        wgs.segment_surface(hull),
        com=None,
        stable_thresh=10.0,
    )
    if not stable:
        raise RuntimeError("no stable pose for bunny")
    pos0, rot0 = stable[0][0], stable[0][1]
    tf_pick = wum.tf_from_pos_rotmat(
        pos0 + np.array([-0.5, 0.0, 0.0], np.float32),
        rot0,
    )
    rot_place = wum.rotmat_from_euler(0, 0, np.deg2rad(45.0)) @ rot0
    tf_place = wum.tf_from_pos_rotmat(
        pos0 + np.array([0.3, 0.3, 0.0], np.float32),
        rot_place,
    )
    bunny.pos, bunny.rotmat = tf_pick[:3, 3], tf_pick[:3, :3]

    cube = wssop.box(
        pos=(-0.22, -0.38, 0.024),
        xyz_lengths=(0.028, 0.028, 0.028),
        name="cube",
        collision_type=wuc.CollisionType.AABB,
        is_floating=True,
    )
    cube.rgb = (0.25, 0.45, 0.75)
    tf_place_b = wum.tf_from_pos_rotmat(
        np.array([0.38, -0.22, 0.024], np.float32),
        np.eye(3, dtype=np.float32),
    )

    collider = robot.build_collider(fixtures=[ground, wall])
    scene = BunnyScene(
        robot=robot,
        gripper=gripper,
        bunny=bunny,
        cube=cube,
        ground=ground,
        wall=wall,
        collider=collider,
        tf_pick=tf_pick,
        tf_place=tf_place,
        places={"place_A": tf_place, "place_B": tf_place_b},
        movables={"bunny": bunny, "cube": cube},
        names={"bunny": bunny, "cube": cube, "wall": wall, "ground": ground},
        scene_id=DEFAULT_SCENE_ID,
    )
    return scene


def build_scene(scene_id: str | Path | None = None, wrs_root: Path | None = None):
    """Build a catalog scene. `scene_id` may be omitted (default RS007L)."""
    if isinstance(scene_id, Path):
        wrs_root = scene_id
        scene_id = DEFAULT_SCENE_ID
    spec = scene_spec(scene_id)
    if spec["id"] == DEFAULT_SCENE_ID:
        return build_rs007l_pickplace(wrs_root)
    if spec["id"] == L1_SCENE_ID:
        from rpent_wrs.l1_scene import build_l1_cylinder_scene

        return build_l1_cylinder_scene(wrs_root)
    raise ValueError(f"场景 {spec['id']} 还没有接线")

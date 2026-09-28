"""Privileged WRS tools: named objects/places and composable primitives."""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
from wrs.grasp.antipodal import antipodal
from wrs.grasp.grasp import Grasp
import wrs.utils.math as wum

import wrs.utils.constant as wuc

from wrs.motion.core.motion_data import MotionData

from rpent_wrs.control import Inbox, parse_goal
from rpent_wrs.replay import CLIP_TITLES, concat_motions, slice_motion, split_pick_place
from rpent_wrs.scene import L1_SCENE_ID, scene_spec

LIFT = 0.12
PLACE_XY_TOL = 0.08


class WrsExecutor:
    def __init__(self, scene=None, inbox: Inbox | None = None):
        self.scene = scene
        self.inbox = inbox
        self.view_scene = None
        self.motion = None
        self.motion_obj = None
        self.play_i = 0
        self.playing = False
        self._lock = threading.Lock()
        self.play_done = threading.Event()
        self.play_done.set()
        self.last_plan: dict[str, Any] = {}
        self.goal_object = "bunny"
        self.goal_place = "place_A"
        self.grasps: dict[str, list] = {}
        self.chosen_grasp: dict[str, Any] = {}
        self.held_name: str | None = None
        self.approach_name: str | None = None
        self._collider_key: tuple | None = None
        self.clips: list[dict[str, Any]] = []
        self.replay_name: str | None = None
        self._l1_play: dict[str, Any] | None = None
        self._l1_home: tuple[np.ndarray, np.ndarray] | None = None
        if scene is not None:
            self.adopt_scene(scene)

    def _is_l1(self) -> bool:
        return bool(self.scene) and getattr(self.scene, "scene_id", "") == L1_SCENE_ID

    def _spec(self) -> dict[str, Any]:
        sid = getattr(self.scene, "scene_id", None) if self.scene else None
        return scene_spec(sid)

    def adopt_scene(self, scene, spec: dict[str, Any] | None = None) -> None:
        self.scene = scene
        spec = spec or scene_spec(getattr(scene, "scene_id", None))
        goal = spec.get("default_goal") or {}
        self.goal_object = str(goal.get("object") or next(iter(scene.movables), ""))
        self.goal_place = str(goal.get("place") or next(iter(scene.places), ""))
        self.held_name = None
        self.approach_name = None
        self.grasps = {}
        self.chosen_grasp = {}
        self._collider_key = None
        self.clear_clips()
        self._l1_play = None
        self._l1_home = None
        if self._is_l1():
            cyl = scene.cylinder
            self._l1_home = (
                np.asarray(cyl.pos, dtype=np.float32).copy(),
                np.asarray(cyl.rotmat, dtype=np.float32).copy(),
            )
            self.grasps["cylinder"] = list(scene.grasps)

    def apply_instruction(self, text: str) -> dict[str, str]:
        parsed = parse_goal(text, self._spec() if self.scene else None)
        if parsed.get("object"):
            self.goal_object = parsed["object"]
        if parsed.get("place"):
            self.goal_place = parsed["place"]
        return {"object": self.goal_object, "place": self.goal_place}

    def _show_object(self, obj) -> None:
        if obj is not None and self.view_scene is not None:
            obj.add_to_scene(self.view_scene)
            self.view_scene.dirty = True

    def _xyz(self, pos) -> list[float]:
        return [float(pos[0]), float(pos[1]), float(pos[2])]

    def known_names(self) -> list[str]:
        if self.scene is None:
            return []
        names = list(self.scene.movables) + list(self.scene.places) + ["flange", "ground"]
        if getattr(self.scene, "wall", None) is not None:
            names.append("wall")
        return names

    def _movable(self, name: str):
        obj = self.scene.movables.get(name.strip())
        if obj is None:
            raise KeyError(name)
        return obj

    def place_tf_for(self, obj, place_name: str) -> np.ndarray:
        place = self.scene.places.get(place_name)
        if place is None:
            raise KeyError(place_name)
        out = np.eye(4, dtype=np.float32)
        out[:3, :3] = np.asarray(obj.rotmat, dtype=np.float32)
        out[:3, 3] = np.array(
            [float(place[0, 3]), float(place[1, 3]), float(obj.pos[2])],
            dtype=np.float32,
        )
        return out

    def collider_for(self, manipulated=None):
        extras = [
            obj
            for obj in self.scene.movables.values()
            if obj is not manipulated and obj is not self.held_obj
        ]
        key = (
            id(manipulated) if manipulated is not None else 0,
            id(self.held_obj) if self.held_obj is not None else 0,
            tuple((id(x), tuple(self._xyz(x.pos))) for x in extras),
        )
        if self._collider_key != key:
            for obj in self.scene.movables.values():
                if obj is manipulated or obj is self.held_obj:
                    obj.is_floating = True
                    obj.collision_group = wuc.CollisionGroup.ACTIVE
                else:
                    obj.is_floating = False
                    obj.collision_group = wuc.CollisionGroup.STATIC
            fixtures = [self.scene.ground, *extras]
            wall = getattr(self.scene, "wall", None)
            if wall is not None:
                fixtures.insert(1, wall)
            self.scene.collider = self.scene.robot.build_collider(fixtures=fixtures)
            self._collider_key = key
        return self.scene.collider

    @property
    def held_obj(self):
        return self.scene.movables.get(self.held_name) if self.held_name else None

    def get_pose(self, name: str) -> dict[str, Any]:
        if self.scene is None:
            return {"error": "scene not opened"}
        name = name.strip()
        if name == "flange":
            try:
                tf = self.scene.robot.tcp("flange").tf
                return {"name": name, "xyz": self._xyz(tf[:3, 3])}
            except Exception:
                hand = getattr(self.scene.robot, "left_hand", None)
                if hand is not None:
                    return {"name": name, "xyz": self._xyz(hand.pos)}
                return {"error": "no flange pose"}
        if name in self.scene.places:
            return {"name": name, "xyz": self._xyz(self.scene.places[name][:3, 3])}
        obj = self.scene.names.get(name)
        if obj is None:
            return {"error": f"unknown name {name!r}", "known": self.known_names()}
        return {"name": name, "xyz": self._xyz(obj.pos)}

    def view_state(self) -> dict[str, Any]:
        if self.scene is None:
            return {
                "objects": {},
                "places": {},
                "flange": None,
                "held": None,
                "grasps_ready": {},
                "goal": {"object": self.goal_object, "place": self.goal_place},
                "success": False,
                "scene_success": False,
                "inbox": self.inbox.snapshot() if self.inbox else None,
                "replay": {"playing": False, "clip": None, "clips": []},
            }
        objects = {n: self._xyz(obj.pos) for n, obj in self.scene.movables.items()}
        places = {n: self._xyz(tf[:3, 3]) for n, tf in self.scene.places.items()}
        return {
            "objects": objects,
            "places": places,
            "flange": self.get_pose("flange").get("xyz"),
            "held": self.held_name,
            "grasps_ready": {n: len(g) for n, g in self.grasps.items()},
            "goal": {"object": self.goal_object, "place": self.goal_place},
            "success": self.is_success(),
            "scene_success": self.is_success(),
            "inbox": self.inbox.snapshot() if self.inbox else None,
            "replay": {
                "playing": bool(self.playing and self.replay_name),
                "clip": self.replay_name,
                "clips": self.clip_summaries(),
            },
        }

    def is_success(self) -> bool:
        if self.scene is None:
            return False
        if self._is_l1():
            obj = self.scene.movables.get(self.goal_object)
            if obj is None or self._l1_home is None:
                return False
            start_z = float(self._l1_home[0][2])
            return bool(float(obj.pos[2]) >= start_z + 0.06 and self.held_name == "cylinder")
        try:
            obj = self._movable(self.goal_object)
            place = self.scene.places[self.goal_place]
        except KeyError:
            return False
        delta = np.asarray(obj.pos[:2], dtype=np.float32) - place[:2, 3]
        return bool(np.linalg.norm(delta) < PLACE_XY_TOL)

    def _l1_ensure_hold(self) -> None:
        if not self._is_l1() or self.held_name == "cylinder":
            return
        play = self._l1_play or {}
        amount = float(play.get("amount") or 0.0)
        primitive = play.get("primitive") or getattr(self.scene.mod, "PRIMITIVE", "tripod")
        self.scene.robot.left_hand.grip(primitive, amount)
        self.scene.robot.left_hand.hold(self.scene.cylinder)
        if self._l1_play is not None:
            self._l1_play["held"] = True
        self.held_name = "cylinder"

    def reset_visual(self) -> None:
        if not self._is_l1() or self._l1_home is None:
            return
        cyl = self.scene.cylinder
        if self.held_name == "cylinder" or (self._l1_play and self._l1_play.get("held")):
            try:
                self.scene.robot.left_hand.open_hand()
                self.scene.robot.left_hand.release(cyl)
            except Exception:
                pass
        self.held_name = None
        if self._l1_play is not None:
            self._l1_play["held"] = False
        cyl.pos = self._l1_home[0].copy()
        cyl.rotmat = self._l1_home[1].copy()

    def apply_frame(self, i: int) -> None:
        motion = self.motion
        if motion is None or i < 0 or i >= len(motion):
            return
        self.scene.robot.fk(qs=motion.robot_qpos_list[i])
        if self._is_l1():
            if getattr(motion, "l1_hold_from", False) and i == 0:
                self._l1_ensure_hold()
            grasp_at = getattr(motion, "l1_grasp_at", None)
            if grasp_at is not None and i >= int(grasp_at):
                self._l1_ensure_hold()
            return
        ee_qpos = motion.ee_qpos_list[i]
        if ee_qpos is not None and self.scene.gripper is not None:
            self.scene.gripper.fk(qs=ee_qpos)
        op = motion.obj_pose_list[i]
        obj = self.motion_obj
        if op is not None and obj is not None:
            obj.pos = op[:3, 3]
            obj.rotmat = op[:3, :3]

    def apply_final(self) -> None:
        if self.motion is None:
            return
        self.apply_frame(len(self.motion) - 1)

    def tick_play(self) -> bool:
        with self._lock:
            if not self.playing or self.motion is None:
                return False
            if self.play_i >= len(self.motion):
                self.playing = False
                self.play_done.set()
                return False
            i = self.play_i
            self.play_i += 1
        self.apply_frame(i)
        return True

    def _play_motion(
        self,
        motion,
        *,
        animate: bool,
        obj=None,
        label: str | None = None,
        record: bool = True,
        detail: str = "",
    ) -> dict[str, Any]:
        if record and label:
            self._store_clip(label, motion, obj, detail)
        self.motion_obj = obj
        self._show_object(obj)
        with self._lock:
            self.motion = motion
            self.last_plan = {"waypoints": len(motion)}
            if animate:
                self.play_i = 0
                self.playing = True
                self.play_done.clear()
            else:
                self.playing = False
                self.play_done.set()
        interrupted = False
        if animate:
            while not self.play_done.wait(timeout=0.2):
                if self.inbox is not None and self.inbox.stop:
                    interrupted = True
                    self.apply_final()
                    with self._lock:
                        self.playing = False
                        self.play_done.set()
                    break
                if self.inbox is not None and self.inbox.paused:
                    with self._lock:
                        self.playing = False
                elif self.motion is not None and self.play_i < len(self.motion):
                    with self._lock:
                        if not self.playing:
                            self.playing = True
        else:
            self.apply_final()
        return {
            "ok": True,
            "waypoints": len(motion),
            "interrupted": interrupted,
            "scene_success": self.is_success(),
        }

    def clear_clips(self) -> None:
        self.clips = []
        self.replay_name = None

    def clip_summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "id": i,
                "name": item["name"],
                "title": item["title"],
                "waypoints": item["waypoints"],
                "detail": item.get("detail") or "",
            }
            for i, item in enumerate(self.clips)
        ]

    def _object_name(self, obj) -> str | None:
        if obj is None:
            return None
        for name, item in self.scene.movables.items():
            if item is obj:
                return name
        return None

    def _store_clip(self, name: str, motion, obj=None, detail: str = "") -> None:
        if motion is None or len(motion) < 2:
            return
        title = CLIP_TITLES.get(name, name)
        self.clips.append(
            {
                "name": name,
                "title": title,
                "motion": motion.copy(),
                "object": self._object_name(obj),
                "waypoints": len(motion),
                "detail": detail or f"{len(motion)} 路点",
            }
        )

    def record_pick_place_clips(self, motion, obj=None, detail: str = "") -> None:
        for name, start, end in split_pick_place(motion):
            self._store_clip(name, slice_motion(motion, start, end), obj, detail)

    def replay_clip(self, clip_id: int | None = None, *, whole: bool = False) -> dict[str, Any]:
        if whole:
            motion = concat_motions([item["motion"] for item in self.clips])
            if motion is None or motion.is_empty():
                return {"ok": False, "error": "还没有可回放的动作"}
            obj_name = next((item.get("object") for item in self.clips if item.get("object")), None)
            name = "episode"
            title = "完整回放"
        else:
            if clip_id is None or clip_id < 0 or clip_id >= len(self.clips):
                return {"ok": False, "error": "没有这一段"}
            item = self.clips[clip_id]
            motion = item["motion"]
            obj_name = item.get("object")
            name = item["name"]
            title = item["title"]
        obj = self.scene.movables.get(obj_name) if obj_name else None
        if self._is_l1() and self._l1_play is not None:
            self.reset_visual()
            if whole:
                motion.l1_grasp_at = self._l1_play.get("grasp_idx")
            elif name != "approach":
                motion.l1_hold_from = True
        self.motion_obj = obj
        self._show_object(obj)
        with self._lock:
            self.motion = motion
            self.play_i = 0
            self.playing = True
            self.replay_name = name
            self.play_done.clear()
        return {"ok": True, "name": name, "title": title, "waypoints": len(motion)}

    def _box_grasps(self, half: float = 0.014) -> list:
        gripper = self.scene.gripper
        jw = float(2.0 * half)
        jaw_min, jaw_max = [float(x) for x in gripper.jaw_range]
        jw = min(max(jw, jaw_min), jaw_max)
        pre_jw = min(jaw_max, jw + 0.5 * (jaw_max - jw))
        grasps = []
        for yaw_deg in (0.0, 90.0, 180.0, 270.0):
            yaw = np.deg2rad(yaw_deg)
            y = np.array([np.cos(yaw), np.sin(yaw), 0.0], np.float32)
            z = np.array([0.0, 0.0, -1.0], np.float32)
            x = np.cross(y, z)
            rot = np.stack([x, y, z], axis=1)
            pose = wum.tf_from_pos_rotmat(np.zeros(3, dtype=np.float32), rot)
            pre = pose.copy()
            pre[:3, 3] = pose[:3, 3] - LIFT * pose[:3, 2]
            grasps.append(Grasp.from_jaw(gripper, pose, pre, jw, pre_jw, 1.0))
        return grasps

    def plan_grasp(self, obj_name: str) -> dict[str, Any]:
        if self._is_l1():
            if obj_name.strip() != "cylinder":
                return {"ok": False, "error": f"unknown object {obj_name!r}", "known": list(self.scene.movables)}
            grasps = list(self.scene.grasps)
            self.grasps["cylinder"] = grasps
            return {"ok": True, "object": "cylinder", "n_grasps": len(grasps)}
        try:
            obj = self._movable(obj_name)
        except KeyError:
            return {"ok": False, "error": f"unknown object {obj_name!r}", "known": list(self.scene.movables)}
        grasps = []
        for _ in range(6):
            found = antipodal(
                gripper=self.scene.gripper,
                target_sobj=obj,
                density=0.006,
                normal_tol_deg=25,
                roll_step_deg=30,
            )[:120]
            if found:
                grasps = list(found)
                break
        if not grasps:
            grasps = self._box_grasps()
            if not grasps:
                return {"ok": False, "error": f"no grasp for {obj_name}"}
        self.grasps[obj_name] = grasps
        self.chosen_grasp[obj_name] = grasps[0]
        return {"ok": True, "object": obj_name, "n_grasps": len(grasps)}

    def _ensure_grasps(self, obj_name: str) -> list | None:
        if obj_name not in self.grasps:
            result = self.plan_grasp(obj_name)
            if not result.get("ok"):
                return None
        return self.grasps.get(obj_name)

    def move_to(self, name: str, *, animate: bool) -> dict[str, Any]:
        name = name.strip()
        if self._is_l1():
            return self._l1_pick("cylinder", "lift" if name in {"lift", "cylinder", ""} else name, animate=animate)
        robot = self.scene.robot
        gripper = self.scene.gripper
        if name in self.scene.movables:
            if self.held_name and self.held_name != name:
                return {"ok": False, "error": f"holding {self.held_name}; release first"}
            obj = self._movable(name)
            grasps = self._ensure_grasps(name)
            if not grasps:
                return {"ok": False, "error": f"no grasp for {name}"}
            collider = self.collider_for(obj if self.held_name != name else None)
            last_err = "no feasible move_to"
            for grasp in grasps[:40]:
                world = np.asarray(obj.tf, dtype=np.float32) @ grasp.pose
                tcp = grasp.make_tcp(gripper)
                motion = robot.moveto(
                    world,
                    collider=collider,
                    tcp=tcp,
                    ee_qpos=grasp.pre_qpos,
                    time_limit=3.0,
                )
                if motion is None:
                    last_err = "IK/RRT failed for this grasp"
                    continue
                self.chosen_grasp[name] = grasp
                self.approach_name = name
                played = self._play_motion(
                    motion,
                    animate=animate,
                    obj=self.held_obj,
                    label="approach",
                    detail=name,
                )
                played["target"] = name
                played["kind"] = "object"
                return played
            return {"ok": False, "error": last_err, "target": name}

        if name in self.scene.places:
            if not self.held_name:
                place = self.scene.places[name]
                hover = place.copy()
                hover[2, 3] = float(place[2, 3]) + LIFT
                collider = self.collider_for(None)
                motion = robot.moveto(
                    hover,
                    collider=collider,
                    tcp=gripper.tcp("grasp_center"),
                    time_limit=3.0,
                )
                if motion is None:
                    return {"ok": False, "error": f"cannot hover above {name}"}
                played = self._play_motion(
                    motion,
                    animate=animate,
                    obj=None,
                    label="hover",
                    detail=name,
                )
                played["target"] = name
                played["kind"] = "place"
                return played
            obj = self.held_obj
            grasp = self.chosen_grasp.get(self.held_name)
            if grasp is None:
                return {"ok": False, "error": "no chosen grasp; plan_grasp then close_gripper"}
            dest = self.place_tf_for(obj, name)
            world = dest @ grasp.pose
            current = np.asarray(obj.tf, dtype=np.float32) @ grasp.pose
            lifted = current.copy()
            lifted[2, 3] = float(current[2, 3]) + LIFT
            hover = world.copy()
            hover[2, 3] = float(world[2, 3]) + LIFT
            tcp = grasp.make_tcp(self.scene.gripper)
            collider = self.scene.collider
            segs = []
            for goal in (lifted, hover, world):
                seg = robot.moveto(
                    goal,
                    collider=collider,
                    tcp=tcp,
                    ee_qpos=grasp.qpos,
                    time_limit=5.0,
                )
                if seg is None:
                    segs = []
                    break
                segs.append(seg)
            if segs:
                motion = segs[0]
                for seg in segs[1:]:
                    motion = motion + seg
                played = self._play_motion(
                    motion,
                    animate=animate,
                    obj=obj,
                    label="move",
                    detail=name,
                )
                played["target"] = name
                played["kind"] = "place"
                return played
            held = self.held_name
            self.release(animate=False)
            fallback = self.pick_and_place(held, name, animate=animate)
            fallback["fallback"] = "pick_and_place"
            fallback["target"] = name
            fallback["kind"] = "place"
            return fallback

        return {"ok": False, "error": f"unknown target {name!r}", "known": self.known_names()}

    def close_gripper(self, obj_name: str = "", *, animate: bool) -> dict[str, Any]:
        if self._is_l1():
            if self.held_name == "cylinder":
                return {"ok": True, "already_held": "cylinder", "scene_success": self.is_success()}
            return self._l1_pick("cylinder", "lift", animate=animate)
        name = (obj_name or self.approach_name or self.goal_object).strip()
        if self.held_name:
            return {"ok": True, "already_held": self.held_name, "scene_success": self.is_success()}
        try:
            obj = self._movable(name)
        except KeyError:
            return {"ok": False, "error": f"unknown object {name!r}"}
        grasps = self._ensure_grasps(name)
        if not grasps:
            return {"ok": False, "error": f"no grasp for {name}"}
        grasp = self.chosen_grasp.get(name) or grasps[0]
        collider = self.collider_for(obj)
        self.scene.robot.hold(obj, grasp.qpos, collider=collider)
        self.held_name = name
        self.chosen_grasp[name] = grasp
        self._collider_key = None
        self._show_object(obj)
        return {
            "ok": True,
            "held": name,
            "scene_success": self.is_success(),
        }

    def release(self, obj_name: str = "", *, animate: bool) -> dict[str, Any]:
        if self._is_l1():
            if self.held_name != "cylinder":
                return {"ok": False, "error": "not holding anything"}
            try:
                self.scene.robot.left_hand.open_hand()
                self.scene.robot.left_hand.release(self.scene.cylinder)
            except Exception as exc:
                return {"ok": False, "error": f"release failed: {exc}"}
            self.held_name = None
            if self._l1_play is not None:
                self._l1_play["held"] = False
            return {"ok": True, "released": "cylinder", "scene_success": self.is_success()}
        name = (obj_name or self.held_name or "").strip()
        if not self.held_name:
            return {"ok": False, "error": "not holding anything"}
        if name and name != self.held_name:
            return {"ok": False, "error": f"holding {self.held_name}, not {name}"}
        obj = self.held_obj
        try:
            self.scene.robot.release(obj, collider=self.scene.collider)
        except Exception as exc:
            return {"ok": False, "error": f"release failed: {exc}"}
        self.scene.gripper.open()
        released = self.held_name
        self.held_name = None
        self._collider_key = None
        self._show_object(obj)
        return {
            "ok": True,
            "released": released,
            "scene_success": self.is_success(),
        }

    def pick_and_place(self, obj_name: str, place: str, *, animate: bool) -> dict[str, Any]:
        obj_name = obj_name.strip()
        place = place.strip()
        if self._is_l1():
            return self._l1_pick(obj_name, place, animate=animate)
        if obj_name not in self.scene.movables:
            return {"ok": False, "error": f"unknown object {obj_name!r}", "known": list(self.scene.movables)}
        if place not in self.scene.places:
            return {"ok": False, "error": f"unknown place {place!r}", "known": list(self.scene.places)}
        if self.held_name:
            return {"ok": False, "error": f"holding {self.held_name}; release first"}

        obj = self._movable(obj_name)
        grasps = self._ensure_grasps(obj_name)
        if not grasps:
            return {"ok": False, "error": f"no grasp for {obj_name}"}
        pick_pose = np.asarray(obj.tf, dtype=np.float32)
        place_pose = self.place_tf_for(obj, place)
        collider = self.collider_for(obj)
        motion = None
        n_grasps = len(grasps)
        for _ in range(8):
            motion = self.scene.robot.pick_and_place(
                obj,
                grasps,
                pick_pose,
                place_pose,
                collider=collider,
                lift_height=LIFT,
            )
            if motion is not None:
                break
            more = self.plan_grasp(obj_name)
            if not more.get("ok"):
                break
            grasps = self.grasps[obj_name]
            n_grasps = len(grasps)
        if motion is None:
            return {"ok": False, "error": "no feasible pick-and-place plan"}

        self._collider_key = None
        self.record_pick_place_clips(motion, obj, detail=f"{obj_name} → {place}")
        played = self._play_motion(motion, animate=animate, obj=obj, record=False)
        played.update(
            {
                "object": obj_name,
                "place": place,
                "grasps": n_grasps,
                "success": self.is_success(),
            }
        )
        return played

    def _l1_pick(self, obj_name: str, place: str, *, animate: bool) -> dict[str, Any]:
        if obj_name != "cylinder":
            return {"ok": False, "error": f"unknown object {obj_name!r}", "known": list(self.scene.movables)}
        if place not in self.scene.places:
            return {"ok": False, "error": f"unknown place {place!r}", "known": list(self.scene.places)}
        if self.held_name:
            return {"ok": False, "error": f"holding {self.held_name}; release first"}
        planned = self.scene.plan_pick()
        if not planned.get("ok"):
            return planned
        traj = planned["traj"]
        grasp_idx = int(planned["grasp_idx"])
        amount = float(planned["amount"])
        self._l1_play = {
            "grasp_idx": grasp_idx,
            "amount": amount,
            "held": False,
            "primitive": getattr(self.scene.mod, "PRIMITIVE", "tripod"),
        }
        n = len(traj)
        motion = MotionData(list(traj), [None] * n, [None] * n)
        motion.l1_grasp_at = grasp_idx
        obj = self.scene.cylinder
        approach = slice_motion(motion, 0, max(2, grasp_idx + 1))
        pick = slice_motion(motion, max(0, grasp_idx - 1), min(n, grasp_idx + 2))
        depart = slice_motion(motion, grasp_idx, n)
        approach.l1_grasp_at = None
        pick.l1_grasp_at = min(1, max(0, len(pick) - 1))
        depart.l1_hold_from = True
        self._store_clip("approach", approach, obj, "cylinder")
        self._store_clip("pick", pick, obj, "grip")
        self._store_clip("depart", depart, obj, "lift")
        played = self._play_motion(motion, animate=animate, obj=obj, record=False)
        played.update(
            {
                "object": obj_name,
                "place": place,
                "grasp": planned.get("grasp"),
                "grasps": len(self.scene.grasps),
                "success": self.is_success(),
            }
        )
        return played

# Cube → place_B: scene_success gate conflict (unresolved)

## What happened
- Operator task: place **cube at place_B** ("把方块放到palce_B", typo for place_B).
- Transfer executed fully via primitives:
  `plan_grasp(cube)` → `move_to(cube)` → `close_gripper(cube)` → `move_to(place_B)` → (auto-release during WRS place phase; standalone `release` then reported "not holding anything" — expected).
- Cube now at [0.380, -0.220, 0.024] = exactly place_B coordinates. Gripper empty. Replay clips confirm full pick/place sequence for cube → place_B.

## Why the run is marked unsuccessful
- `view_state.goal` still reads `{object: cube, place: place_A}` (stale/registered at scene creation; place_A is occupied by the bunny).
- `scene_success` is evaluated against that registered goal, so it stays **false** even though the operator's goal (place_B) is met.
- `finish(success=true)` was **rejected** by the system: "scene_success is false; finish.success=true was rejected".

## What to try next time
- Do NOT move the cube to place_A to appease the gate — it would stack on the bunny and disobey the operator.
- If this recurs: finish(success=false) with a clear message citing the stale goal (place_A) vs. operator task (place_B), and ask the operator to re-register the goal or confirm place_A.
- Note: after `move_to(place)`, the WRS place phase already releases the object; calling `release` afterwards returns "not holding anything" — not an error condition.

# rpent_wrs

RPent 风格的管家 + WRS 执行。代码都在本目录，**不改** `rpent/` 和 `WRS/`。

不隔离、不用 VLA / SAM3。管家组合已注册的原语，路径由 WRS 算。

## 场景

- 臂：RS007L + OnRobot 2FG7
- 物体：`bunny`、`cube`
- 放置点：`place_A`、`place_B`
- 中间有墙，WRS 会绕过去

`bunny` / `place_A` 的布局与 `WRS/examples/test_rs007l_pickplace_bunny.py` 相同。

## 环境

只用 `wrs12`（Python 3.12）。网格读旁边的 `WRS/bunny.stl`。

管家走 GLM 的 OpenAI 兼容接口，本地写在 `config.py`（或环境变量）：

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=...
export RPENT_WRS_MODEL=glm-4.5
```

## 运行

```bash
conda activate wrs12
cd /home/fafu/Fishlj/rpent_wrs
python main.py
```

会打开和 RPent 类似的看板（先启动页，再监控）：

- 看板：**http://127.0.0.1:8001/**（Cursor Ports 转发 8001）
- WRS 画面：**http://127.0.0.1:8000/**（转发 8000；看板右侧会嵌进去）

打开看板先到**新建会话**页，只选场景。进去之后才在监控页输入任务、pause / resume 或改目标；右边这时才出现对应桌子。以后加新桌子，往 `SCENE_CATALOG` 里加一项即可。

`--instruction "把兔子放到 place_A"` 会跳过启动页直接跑。`--no-dashboard` 只要旧的 WRS 页。`--no-viewer` 不要网页。`--scripted` 不调管家、直接 `bunny -> place_A`。

## 管家会调的工具

| 工具 | 作用 |
|---|---|
| `view_state` | 物体/放置点、是否抓住、当前目标、`scene_success` |
| `get_pose` | 按名字读 xyz |
| `plan_grasp` | 给 `bunny` 或 `cube` 采样抓取 |
| `move_to` | 靠近物体，或带着物体去放置点 |
| `close_gripper` | WRS hold |
| `release` | WRS release |
| `pick_and_place` | 一整段抓放（捷径，仍可用） |
| `list_dir` / `read_text_file` / `write_text_file` | memory 和本轮文件 |
| `finish` | 结束；`success=true` 仅当当前目标已达成 |

典型组合：`plan_grasp` → `move_to(物体)` → `close_gripper` → `move_to(放置点)` → `release`。

## Memory 和落盘

- 跨次笔记：`memory/`
- 每次运行：`runs/<时间戳>/transcript.jsonl`、`audit.json`
- 只有当前目标的 `scene_success` 为真时才保留 `recipe.jsonl`
- 成功看的是**当前目标**的物体 xy 是否靠近目标放置点，不是管家口头的 `finish.success`

# Aegis 开发包示例索引

所有示例都支持 `FF_SDK_DRY_RUN=1` 在任何电脑上干跑（不发真实指令）。

## 入门（按编号顺序看）

| 示例 | 主题 |
|---|---|
| [01_hello_connect.py](01_hello_connect.py) | 第一次连接、diagnose、e_stop、close |
| [02_diagnose.py](02_diagnose.py) | 体检报告详解 |
| [03_estop.py](03_estop.py) | 紧急停止 + 回调 + 重置 |

## Aegis 专属

| 示例 | 主题 |
|---|---|
| [d1/udp_walk.py](d1/udp_walk.py) | 完整行走演示（站立 → 前进 → 转向 → 停 → 阻尼）|
| [d1/presets_and_telemetry.py](d1/presets_and_telemetry.py) | ★ 机型选择（点足/轮足/EDU/Ultra）+ 特技 + 关节遥测 |

## Motion（运动控制）

| 示例 | 主题 |
|---|---|
| [motion/cmd_vel.py](motion/cmd_vel.py) | 连续速度控制 |
| [motion/stand_damping.py](motion/stand_damping.py) | 起立 → 保持 → 阻尼 |
| [motion/do_preset.py](motion/do_preset.py) | 预设动作 / 特技 |

## State（遥测读取）

| 示例 | 主题 |
|---|---|
| [state/read_battery.py](state/read_battery.py) | 读电量 |
| [state/watch_status.py](state/watch_status.py) | 监听机器人状态变化 |

## Vision（机器人相机）

| 示例 | 主题 |
|---|---|
| [vision/height_calculator.py](vision/height_calculator.py) | 机器人相机网格标定 + YOLO/OpenCV 身高估计 |

## Cookbook（场景菜谱）

| 示例 | 场景 |
|---|---|
| [cookbook/context_manager.py](cookbook/context_manager.py) | `async with` 自动连/关 |
| [cookbook/graceful_shutdown.py](cookbook/graceful_shutdown.py) | Ctrl+C 触发 e_stop + 安全关 |
| [cookbook/safety_watchdog.py](cookbook/safety_watchdog.py) | 后台监控电量/状态自动 e_stop |
| [cookbook/multi_robot.py](cookbook/multi_robot.py) | 并发控制多台机器人 |
| [cookbook/record_trajectory.py](cookbook/record_trajectory.py) | 轮询 pose 写 CSV（数据采集）|
| [cookbook/diagnose_report.py](cookbook/diagnose_report.py) | 机群诊断导出 JSON |

## 运行方式

```bash
# 干跑（任何电脑，零风险）
export FF_SDK_DRY_RUN=1
python 01_hello_connect.py --target D1-DEMO

# 真机（机器人上 / Linux 开发机连机器人热点）
python d1/udp_walk.py
FF_SDK_D1_VARIANT=zsl-1 python d1/presets_and_telemetry.py --variant zsl-1   # 点足/EDU/Ultra
```

机型选择和部署说明见 [../docs/d1_models.md](../docs/d1_models.md) 和 [../docs/deployment.md](../docs/deployment.md)。
相机身高估计流程见 [../docs/camera_height_workflow.md](../docs/camera_height_workflow.md)。

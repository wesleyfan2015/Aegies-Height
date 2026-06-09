# Cookbook

场景化的使用菜谱。基础 API demo 在 `examples/motion/`、`examples/state/` 等；这里的示例都是**多步真实用法**：

| 菜谱 | 场景 | 涉及 API |
|---|---|---|
| [context_manager.py](context_manager.py) | `async with` 自动连/关 | Session lifecycle |
| [graceful_shutdown.py](graceful_shutdown.py) | Ctrl+C 安全停 + 退出 | signal / e_stop / close |
| [safety_watchdog.py](safety_watchdog.py) | 监控电量/状态自动触发 e_stop | state + estop + 回调 |
| [multi_robot.py](multi_robot.py) | 并发控制 2+ 台机器人 | asyncio.gather + connect |
| [record_trajectory.py](record_trajectory.py) | 记录 pose 时间序列到 CSV | state.pose 轮询 |
| [diagnose_report.py](diagnose_report.py) | 诊断结果导出 JSON（给 CI 用）| DiagnosticReport |

运行：

```bash
export FF_SDK_DRY_RUN=1
python cookbook/<file>.py
```

每个菜谱都支持 `--target` 参数指定机器人，结构一致可以 diff 学习。

# FF Robotics SDK — D1 Release Notes

**Build:** 20260603 · **Package:** `ff_sdk 0.1.0a0` · **Platform:** D1 / AEGIS

---

## 中文

### 本次更新

修复了 D1 速度控制 `cmd_vel` 的问题：此前调用 `sess.motion.cmd_vel(...)` 不会报错，但机器人不会移动。现已修正 —— 机器人可正确响应**前进、后退、左转、右转**。

同时改进了 SDK 连接建立与运动初始化的稳定性。

### 升级方法（业务代码无需改动）

```bash
# 树莓派 / D1 机载 (aarch64)
pip install --force-reinstall wheels/ff_sdk-0.1.0a0-cp310-cp310-linux_aarch64.whl

# x86 开发机 (x86_64)
pip install --force-reinstall wheels/ff_sdk-0.1.0a0-cp310-cp310-linux_x86_64.whl
```

升级后，原有的 `sess.motion.cmd_vel(linear=..., angular=...)` 调用即可正常驱动机器人行走，**不需要改动任何代码**。

### 验证

已在 D1 实机完成全向行走验证（前进 / 后退 / 左转 / 右转），以及姿态控制回归（站立 / 趴下 / 急停）。

### 包内容

| 目录 | 内容 |
|---|---|
| `wheels/` | Python SDK（aarch64 + x86_64） |
| `examples/` | 示例脚本（连接 / 运动 / 状态 / 诊断） |
| `docs/` | 快速上手 / 部署 / 模型说明 |
| `cpp/` | C++ SDK |

### 反馈

如遇问题请联系 FF Robotics SDK 团队。

---

## English

### What's New

Fixed an issue with D1 velocity control `cmd_vel`: previously, calling `sess.motion.cmd_vel(...)` returned no error but the robot did not move. This is now resolved — the robot correctly responds to **forward, backward, turn-left, and turn-right** commands.

Connection setup and motion initialization reliability were also improved.

### Upgrade (no code changes required)

```bash
# D1 robot onboard (aarch64)
pip install --force-reinstall wheels/ff_sdk-0.1.0a0-cp310-cp310-linux_aarch64.whl

# x86 dev machine (x86_64)
pip install --force-reinstall wheels/ff_sdk-0.1.0a0-cp310-cp310-linux_x86_64.whl
```

After upgrading, your existing `sess.motion.cmd_vel(linear=..., angular=...)` calls will drive the robot as expected — **no code changes needed**.

### Verification

Validated on a real D1 unit for omnidirectional walking (forward / backward / left / right turn) plus posture-control regression (stand / lie down / emergency stop).

### Contents

| Directory | Description |
|---|---|
| `wheels/` | Python SDK (aarch64 + x86_64) |
| `examples/` | sample scripts (connect / motion / state / diagnose) |
| `docs/` | quickstart / deployment / models |
| `cpp/` | C++ SDK |

### Support

Contact the FF Robotics SDK team with any issues.

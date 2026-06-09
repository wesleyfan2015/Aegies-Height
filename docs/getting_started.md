# Aegis Getting Started — 进阶入门

> 零基础先看 [QUICKSTART.md](QUICKSTART.md)；名词不懂查 [GLOSSARY.md](GLOSSARY.md)。
> 本文覆盖：安装、dry-run、真机连接、配置、紧急停止、诊断、常见错误。

---

## 1. 安装

```bash
pip install ff_sdk-<version>-cp310-cp310-manylinux2014_aarch64.whl   # 机器人上
pip install ff_sdk-<version>-cp310-cp310-manylinux2014_x86_64.whl    # Linux 开发机
```

### 需求

- Python **3.10**（必须，wheel 按此版本构建）
- 操作系统：Linux / macOS / Windows 都能跑 dry-run；**真机控制需要 Linux**
- 不需要安装任何其他机器人软件 —— wheel 自包含

---

## 2. Hello, Robot —— 第一次连接

### 2.1 dry-run 模式（任何电脑）

```bash
export FF_SDK_DRY_RUN=1
python -c "
import asyncio, ff_sdk

async def main():
    dog = await ff_sdk.connect('D1-DEMO')
    print(dog.diagnose().summary())
    await dog.e_stop(reason='first test')
    print(f'State: {dog.session_state.value}')
    await dog.close()

asyncio.run(main())
"
```

或者直接运行内置示例：

```bash
FF_SDK_DRY_RUN=1 python examples/01_hello_connect.py
```

### 2.2 真机连接

电脑（或程序所在设备）连上机器人热点后：

```bash
# 不要设 FF_SDK_DRY_RUN
python examples/d1/udp_walk.py
```

期望：`diagnose()` 各项 `OK`，机器人完成站立 → 行走 → 阻尼。
机型（点足 / 轮足 / EDU / Ultra）选择见 [d1_models.md](d1_models.md)。

---

## 3. 当前能做什么

| 功能 | API |
|---|---|
| 连接机器人 | `await ff_sdk.connect(target)` |
| 诊断体检 | `dog.diagnose()` |
| 紧急停止 | `await dog.e_stop(reason=...)` |
| 查支持的能力 | `dog.capabilities()` |
| **速度控制** | `await dog.motion.cmd_vel(linear=0.3, angular=0)` |
| **预设动作 / 特技** | `await dog.motion.do_preset("stand" / "shake_hand" / "jump" / ...)` |
| **站立 / 阻尼快捷方法** | `await dog.motion.stand()` / `damping()` / `stop()` |
| **电量** | `await dog.state.battery()` |
| **姿态状态** | `await dog.state.status()` |
| **位姿** | `await dog.state.pose()` |
| **关节遥测** | `await dog.state.joint_states()`（机型差异见 [d1_models.md](d1_models.md) §5）|

---

## 4. 支持的 Target

| Target 形式 | 含义 |
|---|---|
| `D1-<sn>` | Aegis 真机。host 由 `FF_SDK_D1_HOST` 指定，不设时用热点网关默认值 |

---

## 5. 配置

### 5.1 Config 对象

```python
from ff_sdk import Config

cfg = Config(
    transport_timeout=5.0,      # 单次操作超时（秒）
    dry_run=False,              # 干跑模式
)
cfg.extra["d1_variant"] = "zsl-1"    # 机型: zsl-1 点足 / zsl-1w 轮足
```

### 5.2 环境变量（推荐开发用）

| 变量 | 作用 |
|---|---|
| `FF_SDK_DRY_RUN=1` | 启用 dry-run 模式（不真发指令）|
| `FF_SDK_D1_HOST=<robot-ip>` | 机器人 IP 覆盖（局域网模式用）|
| `FF_SDK_D1_VARIANT=zsl-1` | 机型选择（点足 / EDU / Ultra）|
| `FF_SDK_TRANSPORT_TIMEOUT=5` | 传输超时（秒）|
| `FF_SDK_LOG_DIR=...` | 日志根目录 |

`Config.from_env()` 一次性加载所有环境变量。

---

## 6. 紧急停止（e_stop）—— 一等公民

- 任何会话状态下都必须 500ms 内响应
- 触发后 session state → `ESTOPPED`，后续运动调用会抛 `EStopActiveError`
- 重置：`dog.estop.reset()`（应当由操作员手工确认后调用）

```python
# 触发
await dog.e_stop(reason="obstacle too close")

# 检查
if dog.estop.is_active:
    print(f"e_stop active: {dog.estop.last_event}")
```

注册回调（比如清理资源、向监控上报）：

```python
def on_stop(event):
    print(f"[ALERT] e_stop from {event.source}: {event.reason}")

unregister = dog.estop.register(on_stop)
```

完整用法见 `examples/03_estop.py`。

---

## 7. 诊断（diagnose）

```python
report = dog.diagnose()
print(report.summary())
# [✓] d1 · D1-DEMO
#   ✓ target host — ...
#   ✓ UDP transport — open
#   ✓ 运动后端 — connected
```

各 check 字段：

- `name` — 检查项名
- `status` — `OK` / `WARN` / `FAIL` / `UNKNOWN`
- `detail` — 详情字符串

`report.overall` 是聚合健康状态（任何 FAIL → 整体 FAIL；任何 WARN → WARN；全 OK → OK）。

---

## 8. 常见错误

### 8.1 `ConfigError: no adapter available for target 'xxx'`
Target 格式不对。Aegis 用 `D1-<sn>` 形式。

### 8.2 `EStopActiveError`
`e_stop` 触发后尝试调普通操作。先 `dog.estop.reset()`（操作员确认后）。

### 8.3 `TransportError: UDP sendto failed`
网络不通。检查：

- 是否连到机器人热点
- 局域网模式下 `FF_SDK_D1_HOST` 是否指对
- `ping <robot-ip>` 有无响应

### 8.4 关节遥测抛 `CapabilityNotSupported`
部分轮足固件版本限制（见 [d1_models.md](d1_models.md) §5）。运动控制不受影响，
用 try/except 兼容写法。

### 8.5 指令发了机器人不动
- 先 `motion.stand()` 让机器人站起来，再发 `cmd_vel`
- 看 `state.status()` 确认当前状态
- 看 `diagnose()` 确认运动后端在线

---

## 9. 下一步去哪看

- [d1_models.md](d1_models.md) —— 机型适配（EDU / Ultra / 点足 / 轮足）+ 特技清单
- [deployment.md](deployment.md) —— 部署到机器人 + 开机自启
- [`../examples/`](../examples/) —— 全部可运行示例（按主题分类）

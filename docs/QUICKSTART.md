# 5 分钟零基础上手 — Aegis 四足机器人

第一次接触机器人编程？没问题。这篇带你**不用真机**，在自己电脑上 5 分钟里写出
控制 Aegis 站立、行走、读电量的完整程序。每一步都解释在做什么。

> 看到不懂的词（Capability？dry-run？）随时翻 → [GLOSSARY.md](GLOSSARY.md)（名词表）。

---

## 你需要什么

- 一台电脑（Mac / Linux / Windows 都行）
- Python **3.10**（命令行敲 `python --version` 看一下）
- **不需要**真机器人 —— 我们用「dry-run 干跑」模式

---

## 第 1 步：装 SDK

```bash
pip install ff_sdk-<version>-<对应你系统的>.whl
```

装完敲一句确认：

```bash
python -c "import ff_sdk; print('装好了，版本', ff_sdk.__version__)"
```

> 还没拿到 wheel？没关系，先往下看代码 —— 写法不变，拿到 wheel 后直接跑。

---

## 关键概念：「dry-run（干跑）」是什么？

机器人编程最大的门槛是「得有台机器人」。dry-run 模式下，
SDK **假装**连上了机器人 —— 你的代码照常跑、照常打日志，但不会真的发指令给硬件。
用来验证「我的代码逻辑对不对」，零风险。

```bash
export FF_SDK_DRY_RUN=1        # Windows: set FF_SDK_DRY_RUN=1
```

---

## 第 2 步：第一次「连接」机器人

新建一个文件 `my_first.py`：

```python
import asyncio
import ff_sdk

async def main():
    # connect 的参数是「机器人地址」，Aegis 统一用 D1-<序列号> 形式
    dog = await ff_sdk.connect("D1-DEMO")

    # 问它支持哪些「能力（capability）」
    print("这台机器人能做:", dog.capabilities())

    await dog.close()

asyncio.run(main())
```

跑它：

```bash
export FF_SDK_DRY_RUN=1
python my_first.py
```

`motion`（运动）和 `state`（状态读取）会出现在输出里。
**你刚刚连上了一台（虚拟的）Aegis。**

> `async` / `await` 是什么？机器人指令要等硬件回应，所以 SDK 用「异步」写法。
> 你现在只要记住：调机器人的方法前面加 `await`，主函数用 `asyncio.run(...)` 启动。

---

## 第 3 步：让它「站起来」+ 读电量

```python
import asyncio
import ff_sdk

async def main():
    dog = await ff_sdk.connect("D1-DEMO")

    # 让机器人站立（motion 是「运动能力」，stand 是其中一个动作）
    result = await dog.motion.stand()
    print("站立:", "成功" if result.success else "失败", "—", result.message)

    # 读状态（state 是「状态能力」）
    battery = await dog.state.battery()
    print(f"电量: {battery.percent * 100:.0f}%")

    status = await dog.state.status()
    print("当前姿态:", status)

    await dog.close()

asyncio.run(main())
```

---

## 第 4 步：让它走两步（速度控制）

```python
    # cmd_vel = 速度指令：前进 0.3 m/s
    await dog.motion.cmd_vel(linear=0.3, angular=0.0)
    await asyncio.sleep(2)         # 走 2 秒
    await dog.motion.stop()        # 停
    await dog.motion.damping()     # 阻尼（安全收尾）
```

> ⚠️ 安全第一：`cmd_vel` 是真的会让机器人移动的指令。dry-run 里随便试；
> 接真机前先了解 `e_stop`（紧急停止，见名词表）。

---

## 第 5 步：特技（握手）

```python
    await dog.motion.do_preset("shake_hand")
    await asyncio.sleep(dog.motion.preset_timeout("shake_hand"))   # 等动作做完 (~10s)
```

全部特技清单见 [d1_models.md](d1_models.md) §6。

---

## 卡住了？

| 现象 | 原因 / 解决 |
|---|---|
| `ModuleNotFoundError: ff_sdk` | wheel 没装上，或 Python 不是 3.10。重跑第 1 步。 |
| `RuntimeError: asyncio` | 忘了用 `asyncio.run(main())` 启动，或忘了在调用前加 `await`。 |
| 连真机连不上 | 检查是否连上机器人热点；先用 `dog.diagnose()` 自检。新手先别碰真机。 |

---

## 接下来

- 不懂的名词 → [GLOSSARY.md](GLOSSARY.md)
- 更多用法（真机连接、配置、紧急停止）→ [getting_started.md](getting_started.md)
- 你手上是哪个机型（EDU / Ultra / 点足 / 轮足）→ [d1_models.md](d1_models.md)
- 按主题分类的可运行示例 → [../examples/](../examples/)
- 部署到机器人上正式运行 → [deployment.md](deployment.md)

你已经会：连接机器人、查能力、让它站立、读状态、控制速度、做特技。
接下来用真机把同样的代码跑起来 —— **一行不用改**。

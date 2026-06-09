# 名词表（Glossary）— Aegis 开发包

第一次看 SDK 文档会撞到一堆词。这页用大白话解释，看不懂哪个就翻回来查。

---

### Session（会话）
`await ff_sdk.connect(...)` 返回的那个 `dog` 对象就是一个 Session —— 代表「你和这台机器人
的一次连接」。所有能力都挂在它上面。用完 `await dog.close()` 关掉。

### connect target（连接地址）
告诉 SDK 连哪台机器人的字符串，Aegis 统一为 `D1-<序列号>` 形式（如 `D1-DEMO`）。

### Capability（能力）
机器人「能做的一类事」。Aegis 开发包提供两类：
- **motion**（运动）— 站立、行走、速度控制、特技动作
- **state**（状态）— 电量、姿态、位姿、关节遥测

用法永远是 `dog.<能力>.<动作>()`，例如 `dog.motion.cmd_vel(0.3)`。

### Adapter（适配器）
SDK 内部把你的 `dog.motion.stand()` 翻译成机器人底层指令的「翻译官」。
**适配器把底层差异吃掉**，所以点足 / 轮足 / EDU / Ultra 用的都是同一套代码。

### variant（机型变体）
告诉 SDK 你手上是哪种 Aegis：`zsl-1`（点足，含 EDU / Ultra）或 `zsl-1w`（轮足）。
通过环境变量 `FF_SDK_D1_VARIANT` 设置，详见 [d1_models.md](d1_models.md)。

### dry-run（干跑模式）
设环境变量 `FF_SDK_DRY_RUN=1` 后，SDK **假装**连上了机器人：代码照跑、日志照打，但不真的
发指令给硬件。用来在没有真机时验证代码逻辑，零风险。

### e_stop（紧急停止）
最高优先级的「立刻停」。任何连接状态下，500ms 内让机器人进入安全停止（阻尼）。
`await dog.e_stop()`。接真机前一定先了解它。

### preset（预设动作）
内置整套动作，用名字调用：`do_preset("stand")` / `do_preset("shake_hand")` /
`do_preset("backflip")` 等。每个动作有建议等待时长，用 `preset_timeout(name)` 查。

### damping（阻尼）
让全部关节进入「软」状态 —— 机器人会缓缓趴下，电机不再用力。
是最安全的停止方式，**每段控制程序收尾都建议调一次**。

### diagnose（诊断 / 体检）
`dog.diagnose()` 返回一份体检报告：网络通不通、运动后端起没起来、遥测链路是否正常。
连真机出问题时第一件事就是看它。

### CapabilityNotSupported（能力不支持）
你调了一个这台机器人/这个固件版本做不到的功能时抛的错。
SDK 的原则是**明确报错，绝不假装成功** —— 看到这个错是正常的设计，不是 bug。

### Transport（传输层）
最底层的「线上协议」封装（怎么把字节发给机器人）。**开发者不用碰它** —— 适配器替你处理了。

### wheel（安装包）
SDK 的发布形态（`.whl` 文件），`pip install` 即装。机器人控制所需的底层运动库已打包在内，
不需要安装其他任何东西。

---

还有词没查到？看 [getting_started.md](getting_started.md)（进阶）或 [QUICKSTART.md](QUICKSTART.md)（动手）。

# Aegis C++ SDK

用 C++ 控制 Aegis（产品代号 D1）四足机器人。与 Python 版同一套语义，面向需要
原生性能或已有 C++ 代码基的开发者。

```cpp
#include "ff_aegis_sdk.hpp"
using namespace ff::aegis;

Robot dog(Variant::Footed);     // 点足 / EDU / Ultra；轮足用 Variant::Wheeled
dog.connect();                  // 默认连机器人热点网关
dog.stand();
dog.move(0.3f, 0.0f, 0.0f);     // 前进 0.3 m/s
dog.doPreset("shake_hand");
dog.damping();                  // 安全收尾
```

## 包内容

```
cpp/
├── README.md
├── include/ff_aegis_sdk.hpp        ← 唯一需要 include 的头文件
├── lib/
│   ├── x86_64/                     ← Linux 开发机用
│   │   ├── libff_aegis_sdk.so      ← FF SDK（你链接这个）
│   │   └── (运动运行时 .so)        ← 自动依赖，无需关心
│   └── aarch64/                    ← 机器人本体用
│       ├── libff_aegis_sdk.so
│       └── (运动运行时 .so)
├── examples/hello_aegis.cpp        ← 示例：连接 → 站立 → 行走 → 阻尼
└── CMakeLists.txt                  ← 编译示例（也是你接入的模板）
```

> **无需安装任何厂商 SDK**：底层运动运行时 `.so` 已随包提供，`libff_aegis_sdk.so`
> 运行时自动加载。你的代码只 include 一个头、链接一个库。

## 编译示例

```bash
cd cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
./build/hello_aegis footed      # 或 wheeled（默认）
```

CMake 会按当前架构自动选 `lib/x86_64` 或 `lib/aarch64`。

## 接入你自己的程序

把本目录当模板：`#include "ff_aegis_sdk.hpp"`，链接 `lib/<arch>/libff_aegis_sdk.so`，
并保证运行时能找到同目录的 `.so`（CMakeLists 里已用 RPATH 处理；手工编译时设
`LD_LIBRARY_PATH=cpp/lib/<arch>`）。

## API 速查

| 方法 | 说明 |
|---|---|
| `Robot(Variant)` | 创建句柄。`Variant::Footed`（点足/EDU/Ultra）/ `Variant::Wheeled`（轮足）|
| `connect(dog_ip, local_ip, local_port)` | 连接，返回 bool。参数都有默认值 |
| `stand() / lieDown() / damping()` | 站立 / 趴下 / 阻尼（软急停）|
| `move(vx, vy, yaw_rate)` | 速度控制，body-FLU：前正 / 左正 / 逆时针正 |
| `attitude(roll, pitch, yaw, height)` | 姿态微调（速度量）|
| `doPreset(name)` | 特技 / 预设；`knownPresets()` 列当前机型支持项；未知名返回 false |
| `presetDuration(name)` | 一个预设动作物理完成的建议等待秒数 |
| `battery()` | 电量 0-100 |
| `ctrlMode()` | 当前状态枚举（Damping/Standing/Moving/Lying/...）|
| `rpy() / position() / quaternion() / bodyVelocity()` | 位姿遥测 |
| `jointAngles() / jointVelocities() / jointTorques()` | 关节遥测（点足 12 / 轮足 16）|
| `lastError()` | 上一次失败的原因字符串 |

## 机型与特技

| Variant | 特技集 | 关节 |
|---|---|---|
| `Footed`（点足 / EDU / Ultra）| stand · lie_down · damping · **shake_hand · jump · front_jump · backflip · two_leg_stand** | 12 |
| `Wheeled`（轮足）| stand · lie_down · damping · **crawl** | 16 |

机型详解见 [../docs/d1_models.md](../docs/d1_models.md)。

## 安全

- 第一次跑运动前把机器人放空旷平整地面，周围留足空间
- 特技（尤其 `backflip`）需要更大空间 + 满电
- 任何控制循环都应能在异常时调 `damping()`

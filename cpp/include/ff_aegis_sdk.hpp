// ff_aegis_sdk.hpp — FF Aegis 四足机器人 C++ SDK（公开头文件）
//
// 用一套 FF 品牌 API 控制 Aegis（产品代号 D1）四足机器人。底层差异（点足 /
// 轮足）由 SDK 内部吸收，你的代码只面向这一个头文件。
//
//   #include "ff_aegis_sdk.hpp"
//   ff::aegis::Robot dog(ff::aegis::Variant::Footed);   // 点足 / EDU / Ultra
//   dog.connect();
//   dog.stand();
//   dog.move(0.3f, 0.0f, 0.0f);     // 前进 0.3 m/s
//   dog.doPreset("shake_hand");
//
// 线程模型：所有控制方法是同步的（一次发送即返回）。特技动作的物理执行需要
// 时间，发出后请按 presetDuration() 的建议时长等待再发下一条。
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace ff {
namespace aegis {

// 机型变体。Footed = 点足（标准点足版 / EDU / Pro / Ultra）；
// Wheeled = 轮足（轮狗）。决定可用的特技集与关节数。
enum class Variant {
    Footed,
    Wheeled,
};

// 机器人当前控制状态。
enum class CtrlMode {
    Damping,   // 阻尼 / 关节自由
    Standing,  // 站立（含特技子状态）
    Moving,    // 移动中
    Lying,     // 趴下
    Action,    // 执行动作中
    Unknown,
};

// 一次关节读数。每个家族（abad/hip/knee[/foot]）按腿顺序 FR, FL, RR, RL
// 排列。点足机型 foot 为空；轮足机型 foot 为 4 个轮关节。
struct JointReadout {
    std::vector<float> abad;
    std::vector<float> hip;
    std::vector<float> knee;
    std::vector<float> foot;  // 仅轮足
};

class Robot {
 public:
    // 创建一个机器人句柄。variant 必须与真实机型匹配。
    explicit Robot(Variant variant = Variant::Wheeled);
    ~Robot();

    Robot(const Robot&) = delete;
    Robot& operator=(const Robot&) = delete;

    // ── 连接 / 生命周期 ────────────────────────────────────────────
    // 连接机器人。dog_ip 默认是机器人热点网关。成功返回 true。
    bool connect(const std::string& dog_ip = "192.168.234.1",
                 const std::string& local_ip = "127.0.0.1",
                 int local_port = 43988);
    bool isConnected();

    // ── 运动 ───────────────────────────────────────────────────────
    std::uint32_t stand();
    std::uint32_t lieDown();
    std::uint32_t damping();  // 软急停（零力矩），收尾推荐调用
    // 速度控制，body-FLU 坐标系：vx 前正、vy 左正、yaw_rate 逆时针正。
    std::uint32_t move(float vx, float vy, float yaw_rate);
    // 姿态微调（横滚 / 俯仰 / 偏航 / 高度 速度）。
    std::uint32_t attitude(float roll_vel, float pitch_vel,
                           float yaw_vel, float height_vel);

    // ── 特技 / 预设动作（按名字，跨机型统一）────────────────────────
    // 支持的名字随机型不同，用 knownPresets() 查询。未知名字返回 false，
    // 原因见 lastError()。绝不静默失败。
    bool doPreset(const std::string& name);
    std::vector<std::string> knownPresets() const;
    // 一个预设动作物理完成的建议等待秒数（发出后 sleep 这么久再发下一条）。
    double presetDuration(const std::string& name) const;

    // ── 遥测 ───────────────────────────────────────────────────────
    int battery();              // 0-100
    CtrlMode ctrlMode();
    std::vector<float> rpy();           // [roll, pitch, yaw]
    std::vector<float> position();      // [x, y, z]
    std::vector<float> quaternion();    // [w, x, y, z]
    std::vector<float> bodyVelocity();  // [vx, vy, vz]
    JointReadout jointAngles();
    JointReadout jointVelocities();
    JointReadout jointTorques();

    // ── 杂项 ───────────────────────────────────────────────────────
    Variant variant() const;
    std::string lastError() const;

 private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace aegis
}  // namespace ff

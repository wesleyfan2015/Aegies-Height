// hello_aegis — FF Aegis C++ SDK 最小示例。
//
// 编译 + 运行（见 cpp/README.md）：
//   cmake -B build && cmake --build build
//   ./build/hello_aegis footed        # 或 wheeled
// （CMake 已配 RPATH 指向 lib/<arch>，运行时自动找到 SDK 与运动运行时库）
//
// 参数: footed (点足 / EDU / Ultra) | wheeled (轮足，默认)
#include "ff_aegis_sdk.hpp"

#include <chrono>
#include <cstdio>
#include <string>
#include <thread>

using namespace ff::aegis;

static const char* modeName(CtrlMode m) {
    switch (m) {
        case CtrlMode::Damping:  return "damping";
        case CtrlMode::Standing: return "standing";
        case CtrlMode::Moving:   return "moving";
        case CtrlMode::Lying:    return "lying";
        case CtrlMode::Action:   return "action";
        default:                 return "unknown";
    }
}

int main(int argc, char** argv) {
    Variant v = Variant::Wheeled;
    if (argc > 1 && std::string(argv[1]) == "footed") v = Variant::Footed;

    Robot dog(v);
    std::printf(">> connecting (%s)...\n", v == Variant::Footed ? "footed" : "wheeled");
    if (!dog.connect()) {
        std::printf("   connect failed: %s\n", dog.lastError().c_str());
        return 1;
    }
    std::printf("   connected. battery=%d%%  mode=%s\n",
                dog.battery(), modeName(dog.ctrlMode()));

    std::printf(">> presets available:");
    for (const auto& p : dog.knownPresets()) std::printf(" %s", p.c_str());
    std::printf("\n");

    std::printf(">> stand\n");
    dog.stand();
    std::this_thread::sleep_for(std::chrono::duration<double>(dog.presetDuration("stand")));

    std::printf(">> move forward 0.3 m/s for 2s\n");
    dog.move(0.3f, 0.0f, 0.0f);
    std::this_thread::sleep_for(std::chrono::seconds(2));
    dog.move(0.0f, 0.0f, 0.0f);

    auto rpy = dog.rpy();
    if (rpy.size() == 3)
        std::printf("   rpy = [%.3f %.3f %.3f]\n", rpy[0], rpy[1], rpy[2]);

    std::printf(">> damping (safe halt)\n");
    dog.damping();
    return 0;
}

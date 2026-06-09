cmake_minimum_required(VERSION 3.16)
project(my_aegis_app LANGUAGES CXX)

# 消费者 CMake：链接预编译的 libff_aegis_sdk.so，不需要任何厂商头文件 / 源码。
set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# 按目标架构选预编译库目录
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
  set(FF_ARCH aarch64)   # 在机器人上编
else()
  set(FF_ARCH x86_64)    # 在 Linux 开发机上编
endif()
message(STATUS "ff_aegis_sdk arch = ${FF_ARCH}")

set(FF_LIBDIR "${CMAKE_SOURCE_DIR}/lib/${FF_ARCH}")
find_library(FF_AEGIS_SDK ff_aegis_sdk PATHS ${FF_LIBDIR} REQUIRED)

add_executable(hello_aegis examples/hello_aegis.cpp)
target_include_directories(hello_aegis PRIVATE ${CMAKE_SOURCE_DIR}/include)
target_link_libraries(hello_aegis PRIVATE ${FF_AEGIS_SDK})

# 运行时在同目录找 SDK 与厂商运行时库（lib/<arch>/ 下都已就位）
set_target_properties(hello_aegis PROPERTIES
  BUILD_RPATH   "${FF_LIBDIR}"
  INSTALL_RPATH "${FF_LIBDIR}")

cmake_minimum_required(VERSION 3.24)
project(riskcore VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(RISKCORE_PYTHON "Build the pybind11 extension module" ON)
option(RISKCORE_TESTS "Build the C++ unit tests" OFF)
option(RISKCORE_SANITIZE "Build with AddressSanitizer + UndefinedBehaviorSanitizer" OFF)

add_library(riskcore_lib STATIC src/riskcore.cpp)
target_include_directories(riskcore_lib PUBLIC include)
find_package(Threads REQUIRED)
target_link_libraries(riskcore_lib PUBLIC Threads::Threads)
# -O3 lets GCC/Clang auto-vectorize the inner loops; no -march=native, because
# the wheel and container image must run on any x86-64 host.
target_compile_options(riskcore_lib PRIVATE
  $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic -Wconversion -Werror $<$<CONFIG:Release>:-O3>>)

if(RISKCORE_SANITIZE)
  target_compile_options(riskcore_lib PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  target_link_options(riskcore_lib PUBLIC -fsanitize=address,undefined)
endif()

if(RISKCORE_PYTHON)
  find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module)
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(_riskcore src/bindings.cpp)
  target_link_libraries(_riskcore PRIVATE riskcore_lib)
  # Static C++ runtime: the extension then runs on slim images without libstdc++.
  if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_link_options(_riskcore PRIVATE -static-libstdc++ -static-libgcc)
  endif()
  install(TARGETS _riskcore LIBRARY DESTINATION riskcore)
endif()

if(RISKCORE_TESTS)
  enable_testing()
  add_executable(riskcore_tests tests/test_riskcore.cpp)
  target_link_libraries(riskcore_tests PRIVATE riskcore_lib)
  add_test(NAME riskcore_tests COMMAND riskcore_tests)
endif()

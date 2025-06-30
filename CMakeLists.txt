cmake_minimum_required(VERSION 3.10)
project(psi_size)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -O3")

find_package(SEAL 4.1 REQUIRED)

include_directories(${PROJECT_SOURCE_DIR}/include)

add_executable(psi_ca
    src/main.cpp
    src/psi.cpp
    src/config.cpp
)

target_link_libraries(psi_ca emp-tool SEAL::seal pthread)

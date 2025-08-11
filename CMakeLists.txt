cmake_minimum_required(VERSION 3.10)
project(psi_size)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -O3")

find_package(OpenSSL REQUIRED)

find_path(CMAKE_FOLDER NAMES cmake/emp-tool-config.cmake)
include(${CMAKE_FOLDER}/cmake/emp-base.cmake)

find_package(emp-ot REQUIRED)
include_directories(${EMP-OT_INCLUDE_DIRS})

# Installation
install(FILES cmake/emp-sh2pc-config.cmake DESTINATION cmake/)
install(DIRECTORY emp-sh2pc DESTINATION include/)
find_package(emp-ot)

find_path(EMP-SH2PC_INCLUDE_DIR emp-sh2pc/emp-sh2pc.h)

include(FindPackageHandleStandardArgs)
find_package_handle_standard_args(emp-sh2pc DEFAULT_MSG EMP-SH2PC_INCLUDE_DIR)

if(EMP-SH2PC_FOUND)
	set(EMP-SH2PC_INCLUDE_DIRS ${EMP-SH2PC_INCLUDE_DIR}/include/ ${EMP-OT_INCLUDE_DIRS})
	set(EMP-SH2PC_LIBRARIES ${EMP-TOOL_LIBRARIES})
endif()

find_package(SEAL 4.1 REQUIRED)

include_directories(${PROJECT_SOURCE_DIR}/include)

# Server程序
add_executable(psi_server
    src/server.cpp
    src/psi.cpp
    src/config.cpp
)

target_link_libraries(psi_server emp-tool SEAL::seal OpenSSL::Crypto pthread)

# Client程序
add_executable(psi_client
    src/client.cpp
    src/psi.cpp
    src/config.cpp
)

target_link_libraries(psi_client emp-tool SEAL::seal OpenSSL::Crypto pthread)

# 数据生成程序
add_executable(gendata
    src/gendata.cpp
    src/config.cpp
)

target_link_libraries(gendata pthread)

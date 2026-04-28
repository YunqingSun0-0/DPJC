#include "psi.h"
#include "config.h"
#include <emp-tool/emp-tool.h>
#include <thread>
#include <iostream>
#include <random>
#include <chrono>
#include <fstream>
#include <vector>
#include <string>

void run_server(int server_id) {
    GlobalConfig& config = get_config();
    int port = config.port;

    const char* peer_host = server_id == 1 ? nullptr : config.server1_host.c_str();

    std::cout << "Starting Server " << server_id << " on port " << port << std::endl;
    emp::NetIO* server_io = new emp::NetIO(peer_host, port);
    
    // 创建与clients的连接
    std::vector<emp::NetIO*> client_connections;
    int client_start_port = port + 100 + (server_id == 1 ? 0 : config.num_clients_per_server); // client连接端口偏移
    
    for (int i = 0; i < config.num_clients_per_server; ++i) {
        emp::NetIO* client_io = new emp::NetIO(nullptr, client_start_port + i);
        client_connections.push_back(client_io);
    }
    
    // 执行server端的PSI计算
    int psi_size = psi_server(server_id, server_io, client_connections);
    
    if(server_id == 2){
        std::cerr << "[Server" << server_id << "] Final PSI size: " << psi_size << std::endl;
        std::cout << "[Server" << server_id << "] Final PSI size: " << psi_size << std::endl;
    }
    
    // 清理连接
    delete server_io;
    for (auto* io : client_connections) {
        delete io;
    }
}

int main(int argc, char** argv) {
    int server_id = -1;

    // 解析通用配置参数
    get_config().parse_args(argc, argv);

    // 解析server特定参数
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        if (arg == "-p" && i + 1 < argc) {
            server_id = std::atoi(argv[++i]);
        } else if(arg == "--help") {
            std::cout << "Usage: " << argv[0] << " -p <1|2> [-port <port>] [--num_clients_per_server=<n>] [--server1_host=<host>] [--server2_host=<host>]" << std::endl;
            std::cout << "  Server IDs: 1 or 2" << std::endl;
            return 1;
        }
    }

    // 验证server ID
    if (server_id != 1 && server_id != 2) {
        std::cerr << "Usage: " << argv[0] << " -p <1|2> [-port <port>] [--num_clients_per_server=<n>] [--server1_host=<host>] [--server2_host=<host>]" << std::endl;
        std::cerr << "  Server IDs: 1 or 2" << std::endl;
        return 1;
    }

    run_server(server_id);
    
    return 0;
}
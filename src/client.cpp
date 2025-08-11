#include "psi.h"
#include "config.h"
#include "input.hpp"
#include <emp-tool/emp-tool.h>
#include <thread>
#include <iostream>
#include <random>
#include <chrono>
#include <fstream>
#include <vector>
#include <string>

void run_client(int client_id, int server_id, const std::string& data_file = "") {
    std::cout << "Starting Client " << client_id << " (Server " << server_id << ")" << std::endl;
    
    GlobalConfig& config = get_config();
    int real_port = config.port + 100 + client_id - 1 + (server_id == 2 ? config.num_clients_per_server : 0);
    /* 
    port between 2 server: config.port
    port S1 to n client: config.port + 100 + client_id - 1
    port S2 to n client: config.port + 100 + client_id - 1 + config.num_clients_per_server
    */
    
    // 创建与对应server的连接
    emp::NetIO* client_io = new emp::NetIO("127.0.0.1", real_port);
    
    // 获取client的输入集合
    FileInputProvider* input;
    if (!data_file.empty()) {
        input = new FileInputProvider(data_file);
        std::cout << "[Client" << client_id << "] Loading data from file: " << data_file << std::endl;
    } else {
        std::cerr << "Error: No data file provided" << std::endl;
        return;
    }
    std::cout << "[Client" << client_id << "] input_set size: " << input->get_input_set().size() << std::endl;
    
    // 执行client端的PSI计算
    int result = psi_client(client_id, server_id, input->get_input_set(), client_io);
    
    std::cout << "[Client" << client_id << "] Processing completed" << std::endl;
    
    delete input;
    delete client_io;
}

int main(int argc, char** argv) {
    int client_id = -1;
    int port = 20929;
    std::string data_file = "";

    // 解析通用配置参数
    get_config().parse_args(argc, argv);

    // 解析client特定参数
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        
        if (arg == "-p" && i + 1 < argc) {
            client_id = std::atoi(argv[++i]);
        } else if (arg.substr(0, 12) == "--data_file=") {
            data_file = arg.substr(12);
        } else if (arg == "--help") {
            std::cout << "Usage: " << argv[0] << " -p <client_id> [-port <port>] [--data_file=<filename>] [--num_clients_per_server=<n>]\n";
            std::cout << "  client_id: Client ID (1, 2, ...)\n";
            std::cout << "  port: Network port for server\n";
            std::cout << "  data_file: Optional file containing input data\n";
            return 0;
        }
    }

    GlobalConfig& config = get_config();
    config.party = client_id + 2;
    
    // 验证client ID
    bool valid_client = false;
    int server_id = 0;
    
    if (client_id >= 1 && client_id <= config.num_clients_per_server) {
        valid_client = true;
        server_id = 1; // 属于Server 1
    } else if (client_id >= config.num_clients_per_server + 1 && client_id <= 2 * config.num_clients_per_server) {
        valid_client = true;
        server_id = 2; // 属于Server 2
        client_id = client_id - config.num_clients_per_server;
    }
    
    if (!valid_client) {
        std::cerr << "Usage: " << argv[0] << " -p <3...> [-port <port>] [--data_file=<filename>] [--num_clients_per_server=<n>]" << std::endl;
        std::cerr << "  Client IDs:" << std::endl;
        std::cerr << "    1-" << (config.num_clients_per_server) << ": Clients for Server 1" << std::endl;
        std::cerr << "    " << (config.num_clients_per_server + 1) << "-" << (2 * config.num_clients_per_server) << ": Clients for Server 2" << std::endl;
        return 1;
    }

    run_client(client_id, server_id, data_file);
    
    return 0;
}

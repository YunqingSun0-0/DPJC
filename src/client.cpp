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
    const char* server_host = server_id == 1 ? config.server1_host.c_str() : config.server2_host.c_str();
    /*
    port between 2 server: config.port
    port S1 to n client: config.port + 100 + client_id - 1
    port S2 to n client: config.port + 100 + client_id - 1 + config.num_clients_per_server
    */

    // Create a connection to the corresponding server
    emp::NetIO* client_io = new emp::NetIO(server_host, real_port);
    
    // Get the client's input set
    std::vector<WeightedInput> input_set;
    
    if (!data_file.empty()) {
        // Use file-based data
        FileInputProvider input(data_file);
        input_set = input.get_input_set();
        std::cout << "[Client" << client_id << "] Loading data from file: " << data_file << std::endl;
    } else {
        // Use the global data manager
        ClientDataProvider input(client_id, server_id);
        input_set = input.get_input_set();
        std::cout << "[Client" << client_id << "] Loading data from global data manager" << std::endl;
    }
    
    std::cout << "[Client" << client_id << "] input_set size: " << input_set.size() << std::endl;
    
    // Run client-side PSI computation
    int64_t result = psi_client(client_id, server_id, input_set, client_io);
    
    std::cout << "[Client" << client_id << "] Processing completed" << std::endl;
    
    delete client_io;
}

int main(int argc, char** argv) {
    int client_id = -1;
    int port = 20929;
    std::string data_file = "";

    // Parse common configuration arguments
    get_config().parse_args(argc, argv);

    // Parse client-specific arguments
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        
        if (arg == "-p" && i + 1 < argc) {
            client_id = std::atoi(argv[++i]);
        } else if (arg.substr(0, 12) == "--data_file=") {
            data_file = arg.substr(12);
        } else if (arg == "--help") {
            std::cout << "Usage: " << argv[0] << " -p <client_id> [-port <port>] [--data_file=<filename>] [--num_clients_per_server=<n>] [--server1_host=<host>] [--server2_host=<host>]\n";
            std::cout << "  client_id: Client ID (1, 2, ...)\n";
            std::cout << "  port: Network port for server\n";
            std::cout << "  data_file: Optional file containing input data (if not provided, uses global data manager)\n";
            return 0;
        }
    }

    GlobalConfig& config = get_config();
    config.party = client_id + 2;
    
    // Validate client ID
    bool valid_client = false;
    int server_id = 0;
    
    if (client_id >= 1 && client_id <= config.num_clients_per_server) {
        valid_client = true;
        server_id = 1; // Assigned to Server 1
    } else if (client_id >= config.num_clients_per_server + 1 && client_id <= 2 * config.num_clients_per_server) {
        valid_client = true;
        server_id = 2; // Assigned to Server 2
        client_id = client_id - config.num_clients_per_server;
    }
    
    if (!valid_client) {
        std::cerr << "Usage: " << argv[0] << " -p <3...> [-port <port>] [--data_file=<filename>] [--num_clients_per_server=<n>] [--server1_host=<host>] [--server2_host=<host>]" << std::endl;
        std::cerr << "  Client IDs:" << std::endl;
        std::cerr << "    1-" << (config.num_clients_per_server) << ": Clients for Server 1" << std::endl;
        std::cerr << "    " << (config.num_clients_per_server + 1) << "-" << (2 * config.num_clients_per_server) << ": Clients for Server 2" << std::endl;
        return 1;
    }

    run_client(client_id, server_id, data_file);
    
    return 0;
}

// refresh-marker: 20260503T033330Z

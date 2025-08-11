#pragma once
#include <string>
#include <cstdint>

struct GlobalConfig {
    std::string psi_mode = "naive";
    bool test_mode = false;
    /*
    naive: not secure, used for accuracy test
    fhe: secure, used for performance test
    */
    int port = 20929;
    int party = 0;

    int universal_set_size = 1<<20, universal_set_size_bit = 20;
    int seed_size = 1<<6, seed_size_bit = 6;
    uint64_t prg_seed = 998244353;
    uint64_t prg_dd = 6;

    int mom_kk = 30, mom_tt = 100; // median of means

    size_t seal_degree = 8192;
    size_t seal_plain_modulus = 20;
    
    int num_clients_per_server = 1;
    
    void parse_args(int argc, char** argv);

    std::string role_description(int party) const {
        if (party == 1 || party == 2) {
            return party == 1 ? "Server1" : "Server2";
        } else if (party >= 3 && party <= 2 + num_clients_per_server) {
            return "Client" + std::to_string(party - 2) + "_Server1";
        } else if (party >= 3 + num_clients_per_server && party <= 2 + 2 * num_clients_per_server) {
            return "Client" + std::to_string(party - 2 - num_clients_per_server) + "_Server2";
        }
        return "Unknown";
    }
    
    bool is_server(int party) const {
        return party == 1 || party == 2;
    }
    
    bool is_client(int party) const {
        return party >= 3 && party <= 2 + 2 * num_clients_per_server;
    }
    
    int get_server_id(int party) const {
        if (party >= 3 && party <= 2 + num_clients_per_server) {
            return 1; // 属于Server1的client
        } else if (party >= 3 + num_clients_per_server && party <= 2 + 2 * num_clients_per_server) {
            return 2; // 属于Server2的client
        }
        return 0; // 不是client
    }
};

GlobalConfig& get_config();
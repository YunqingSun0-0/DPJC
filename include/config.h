#pragma once
#include <string>
#include <cstdint>
#include <vector>

struct GlobalConfig {
    std::string psi_mode = "naive";
    bool test_mode = false;
    /*
    naive: not secure, used for accuracy test
    fhe: secure, used for performance test
    */
    int port = 20929;
    int party = 0;
    std::string server1_host = "127.0.0.1";
    std::string server2_host = "127.0.0.1";

    int universal_set_size = 1<<24, universal_set_size_bit = 24;
    int seed_size = 1<<8, seed_size_bit = 8;
    uint64_t prg_seed = 998244353;
    uint64_t prg_dd = 7;

    int mom_kk = 400, mom_tt = 11; // median of means

    size_t seal_degree = 8192;
    size_t seal_plain_modulus = 24;
    std::vector<int> seal_coeff_modulus = { 60, 60, 36, 27, 27 };
    /*
    input_size; seal_plain_modulus; coeff_modulus; noise budget
    10; 20; 188 = { 52, 52, 36, 24, 24 }; {188, 136, 111, 76, 44, 12, 7}
    10; 24; 210 = { 60, 60, 36, 27, 27 }; {210, 151, 122, 86, 50, 14, 9}
    10; 28;
    */
    
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
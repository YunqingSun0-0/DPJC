#pragma once
#include <string>
#include <vector>
#include <functional>
#include <cstdint>
#include <emp-tool/emp-tool.h>

struct WeightedInput {
    int value = 0;
    int64_t weight = 1;
};

// Server-Client PSI functions
int64_t psi_server(int server_id, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections);
int64_t psi_client(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io);

// FHE version functions
int64_t psi_server_fhe(int server_id, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections);
int64_t psi_client_fhe(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io);

// Naive PSI functions (plaintext version)
int64_t psi_server_naive(int server_id, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections);
int64_t psi_client_naive(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io);

// Registration functions
void register_psi_server(const std::string& name, int64_t(*func)(int, emp::NetIO*, std::vector<emp::NetIO*>&));
void register_psi_client(const std::string& name, int64_t(*func)(int, int, const std::vector<WeightedInput>&, emp::NetIO*));

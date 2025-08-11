#pragma once
#include <string>
#include <vector>
#include <functional>
#include <emp-tool/emp-tool.h>

// Server-Client PSI functions
int psi_server(int server_id, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections);
int psi_client(int client_id, int server_id, const std::vector<int>& input_set, emp::NetIO* io);

// FHE version functions
int psi_server_fhe(int server_id, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections);
int psi_client_fhe(int client_id, int server_id, const std::vector<int>& input_set, emp::NetIO* io);

// Naive PSI functions (plaintext version)
int psi_server_naive(int server_id, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections);
int psi_client_naive(int client_id, int server_id, const std::vector<int>& input_set, emp::NetIO* io);

// Registration functions
void register_psi_server(const std::string& name, int(*func)(int, emp::NetIO*, std::vector<emp::NetIO*>&));
void register_psi_client(const std::string& name, int(*func)(int, int, const std::vector<int>&, emp::NetIO*));

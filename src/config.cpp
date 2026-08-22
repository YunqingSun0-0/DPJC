#include "config.h"
#include <string>
#include <cstdint>
#include <iostream>
#include <random>
#include <chrono>
#include <cstring>
#include <stdexcept>

GlobalConfig& get_config() {
    static GlobalConfig config;
    return config;
}

void GlobalConfig::parse_args(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);

        auto get_value = [&](const std::string& prefix) -> std::string {
            if (arg.substr(0, prefix.size()) == prefix) {
                return arg.substr(prefix.size());
            }
            return "";
        };

        if (auto val = get_value("--port="); !val.empty()) {
            port = std::stoi(val);
        } else if (auto val = get_value("--server1_host="); !val.empty()) {
            server1_host = val;
        } else if (auto val = get_value("--server2_host="); !val.empty()) {
            server2_host = val;
        } else if (auto val = get_value("--universal_set_size_bit="); !val.empty()) {
            universal_set_size_bit = std::stoi(val);
            universal_set_size = 1 << universal_set_size_bit;
        } else if (auto val = get_value("--seed_size_bit="); !val.empty()) {
            seed_size_bit = std::stoi(val);
            seed_size = 1 << seed_size_bit;
        } else if (auto val = get_value("--prg_seed="); !val.empty()) {
            prg_seed = std::stoull(val);
        } else if (auto val = get_value("--prg_dd="); !val.empty()) {
            prg_dd = std::stoull(val);
        } else if (auto val = get_value("--psi_mode="); !val.empty()) {
            psi_mode = val;
        } else if (auto val = get_value("--mom_k="); !val.empty()) {
            mom_kk = std::stoi(val);
        } else if (auto val = get_value("--mom_t="); !val.empty()) {
            mom_tt = std::stoi(val);
        } else if (auto val = get_value("--seal_degree="); !val.empty()) {
            seal_degree = std::stoul(val);
        } else if (auto val = get_value("--seal_plain_modulus="); !val.empty()) {
            seal_plain_modulus = std::stoul(val);
        } else if (auto val = get_value("--num_clients_per_server="); !val.empty()) {
            num_clients_per_server = std::stoi(val);
        } else if (auto val = get_value("--client_threads="); !val.empty()) {
            client_threads = std::stoi(val);
            if (client_threads < 1) {
                throw std::invalid_argument("--client_threads must be >= 1");
            }
        } else if (auto val = get_value("--client_lazy_relin="); !val.empty()) {
            client_lazy_relin = (std::stoi(val) != 0);
        } else if (arg == "--client_lazy_relin") {
            client_lazy_relin = true;
        } else if (arg == "--no_client_lazy_relin") {
            client_lazy_relin = false;
        } else if (auto val = get_value("--client_skip_compute="); !val.empty()) {
            client_skip_compute = (std::stoi(val) != 0);
        } else if (arg == "--client_skip_compute") {
            client_skip_compute = true;
        } else if (arg == "--no_client_skip_compute") {
            client_skip_compute = false;
        } else if (auto val = get_value("--weight_scale_div="); !val.empty()) {
            (void)val;
            // Deprecated: weight scaling is disabled. Keep arg for compatibility.
            weight_scale_div = 1;
        } else if (auto val = get_value("--weighted_limb_bits="); !val.empty()) {
            weighted_limb_bits = std::stoi(val);
            if (weighted_limb_bits <= 0 || weighted_limb_bits > 16) {
                throw std::invalid_argument("--weighted_limb_bits must be in [1, 16]");
            }
        } else if (auto val = get_value("--weighted_chunk_k="); !val.empty()) {
            weighted_chunk_k = std::stoi(val);
            if (weighted_chunk_k < 0) {
                throw std::invalid_argument("--weighted_chunk_k must be >= 0");
            }
        } else if (arg == "--weighted_mode") {
            weighted_mode = true;
        } else if (arg == "--weighted_multilimb_exact") {
            weighted_multilimb_exact = true;
        } else if (arg == "--test_mode") {
            test_mode = true;
            std::mt19937 rng(std::chrono::system_clock::now().time_since_epoch().count());
            prg_seed = std::uniform_int_distribution<uint64_t>(1, UINT64_MAX)(rng);
        }
    }
}

// refresh-marker: 20260503T033330Z

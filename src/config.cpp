#include "config.h"
#include <string>
#include <cstdint>
#include <iostream>
#include <random>
#include <chrono>
#include <cstring>

GlobalConfig& get_config() {
    static GlobalConfig config;
    return config;
}

void GlobalConfig::parse_args(int argc, char** argv, int& party, int& port) {
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "-p") == 0 && i + 1 < argc) {
            party = std::atoi(argv[++i]);
        } else if (std::strcmp(argv[i], "-port") == 0 && i + 1 < argc) {
            port = std::atoi(argv[++i]);
        }
    }

    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);

        auto get_value = [&](const std::string& prefix) -> std::string {
            if (arg.substr(0, prefix.size()) == prefix) {
                return arg.substr(prefix.size());
            }
            return "";
        };

        if (auto val = get_value("--universal_set_size_bit="); !val.empty()) {
            universal_set_size_bit = std::stoi(val);
            universal_set_size = 1 << universal_set_size_bit;
        } else if (auto val = get_value("--seed_size_bit="); !val.empty()) {
            seed_size_bit = std::stoi(val);
            seed_size = 1 << seed_size_bit;
        } else if (auto val = get_value("--prg_seed="); !val.empty()) {
            prg_seed = std::stoull(val);
        } else if (auto val = get_value("--input_seed="); !val.empty()) {
            input_seed = std::stoull(val);
        } else if (auto val = get_value("--prg_dd="); !val.empty()) {
            prg_dd = std::stoull(val);
        } else if (auto val = get_value("--psi_mode="); !val.empty()) {
            psi_mode = val;
        } else if (auto val = get_value("--mom_kk="); !val.empty()) {
            mom_kk = std::stoi(val);
        } else if (auto val = get_value("--mom_tt="); !val.empty()) {
            mom_tt = std::stoi(val);
        } else if (auto val = get_value("--seal_degree="); !val.empty()) {
            seal_degree = std::stoul(val);
        } else if (auto val = get_value("--seal_plain_modulus="); !val.empty()) {
            seal_plain_modulus = std::stoul(val);
        } else if(auto val = get_value("--testmode="); !val.empty()) {
            test_mode = true;
            output_file = val;
            std::mt19937 rng(std::chrono::system_clock::now().time_since_epoch().count());
            prg_seed = std::uniform_int_distribution<uint64_t>(0, UINT64_MAX)(rng);
            input_seed = std::uniform_int_distribution<uint64_t>(0, UINT64_MAX)(rng);
        }
        // else {
        //     std::cerr << "Unknown argument: " << arg << std::endl;
        // }
    }
}

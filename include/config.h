#pragma once
#include <string>
#include <cstdint>

struct GlobalConfig {
    std::string psi_mode = "prg_nondeter_He_simd_mpc";
    bool test_mode = false;
    std::string output_file = "1.txt";

    int universal_set_size = 1<<12, universal_set_size_bit = 12;
    int seed_size = 1<<6, seed_size_bit = 6;
    uint64_t prg_seed = 998244353;  
    uint64_t input_seed = 19920929;
    uint64_t prg_dd = 6;
    /*
        naive 直接发 set
        prg_nondeter_naive As方法 无He 无 ciruit
        prg_nondeter_He As方法 有He 无 ciruit
        prg_nondeter_He_simd
    */
    int mom_kk = 30, mom_tt = 100; // median of means
    size_t seal_degree = 8192;
    size_t seal_plain_modulus = 20;
    
    void parse_args(int argc, char** argv, int& party, int& port);

    std::string role_description(int party) const {
        return party == 1 ? "P1" : "P2";
    }
};

GlobalConfig& get_config();
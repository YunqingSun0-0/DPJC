#include "psi.h"
#include "input.hpp"
#include "config.h"
#include <emp-tool/emp-tool.h>
#include <thread>
#include <iostream>
#include <random>
#include <chrono>

int precise_psi_size() {
    auto input_set_1 = create_input_provider(1)->get_input_set();
    auto input_set_2 = create_input_provider(2)->get_input_set();
    sort(input_set_1.begin(), input_set_1.end());
    sort(input_set_2.begin(), input_set_2.end());
    int intersection_size = 0;
    for(size_t i=0, j=0; i<input_set_1.size() && j<input_set_2.size();) {
        if(input_set_1[i] < input_set_2[j]) {
            ++i;
        } else if(input_set_1[i] > input_set_2[j]) {
            ++j;
        } else {
            ++intersection_size;
            ++i; ++j;
        }
    }
    std::cout << "Precise PSI size: " << intersection_size << std::endl;
    return intersection_size;
}

void uniform_psi_size() {
    std::mt19937 rng(std::chrono::steady_clock::now().time_since_epoch().count());
    auto input_set_1 = create_input_provider(1)->get_input_set();
    auto input_set_2 = create_input_provider(2)->get_input_set();
    sort(input_set_1.begin(), input_set_1.end());
    sort(input_set_2.begin(), input_set_2.end());

    std::vector<long long> mom;
    for(int tt = 0; tt < get_config().mom_tt; ++tt) {
        long long tmp_tot = 0;
        for(int kk = 0; kk < get_config().mom_kk; ++kk) {
            int e1 = 0, e2 = 0;
            for(int i = 0, j1 = 0, j2 = 0, rd; i < get_config().universal_set_size; ++i) {
                rd = std::uniform_int_distribution<int>(0, 1)(rng);
                if(j1 < input_set_1.size() && input_set_1[j1] == i) {
                    e1 += (rd ? -1 : 1);
                    ++j1;
                }
                if(j2 < input_set_2.size() && input_set_2[j2] == i) {
                    e2 += (rd ? -1 : 1);
                    ++j2;
                }
            }
            tmp_tot += e1 * e2;
        }
        mom.push_back(tmp_tot / get_config().mom_kk);
    }
    sort(mom.begin(), mom.end());

    std::cerr<<"Uniform mom: ";
    for (const auto& val : mom) {
        std::cerr << val << " ";
    }
    std::cerr << std::endl;

    std::cout << "Uniform PSI size: " << mom[mom.size() / 2] << std::endl;
}

void run_party(int party, int port) {
    emp::NetIO* io = new emp::NetIO(party == 1 ? nullptr : "127.0.0.1", port);

    GlobalConfig& config = get_config();

    InputProvider* input = create_input_provider(party);
    auto input_set = input->get_input_set();

    int psi_size = compute_psi_ca(party, input_set, io);
    if (party == 2) {
        std::cout << "[Party 2] PSI size: " << psi_size << std::endl;
        if(get_config().test_mode) {
            std::ofstream fout(get_config().output_file, std::ios::app);
            if (fout.is_open()) {
                fout << (double) psi_size / precise_psi_size() << std::endl;
                fout.close();
            } else {
                std::cerr << "Failed to open output file: " << get_config().output_file << std::endl;
            }
        }
    }

    delete input;
    delete io;
}

// each party is a process
int main(int argc, char** argv) {
    int party = -1;
    int port = 20929;

    get_config().parse_args(argc, argv, party, port);

    if (party != 1 && party != 2) {
        std::cerr << "Usage: " << argv[0] << " -p <1|2> [-port <port>]" << std::endl;
        return 1;
    }

    run_party(party, port);

    if(party == 2) {
        precise_psi_size();
    }
    return 0;
}

// each party is a thread
// int main(int argc, char** argv) {
//     get_config().parse_args(argc, argv);

//     int port = 20929;

//     std::thread t1(run_party, 1, port);
//     std::this_thread::sleep_for(std::chrono::milliseconds(100));
//     std::thread t2(run_party, 2, port);

//     t1.join();
//     t2.join();

//     precise_psi_size();

//     return 0;
// }

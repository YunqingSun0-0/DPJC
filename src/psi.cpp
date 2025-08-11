#include "psi.h"
#include "random.hpp"
#include "config.h"
#include "seal/seal.h"
#include <emp-sh2pc/emp-sh2pc.h>
#include <unordered_set>
#include <iostream>
#include <cmath>
#include <unordered_map>
#include <sstream>
#include <vector>
#include <stack>
#include <cstring>
#include <algorithm>
#include <random>
#include <chrono>
#include <functional>
using namespace seal;
using namespace emp;

// static std::unordered_map<std::string, PsiFunc>& registry() {
//     static std::unordered_map<std::string, PsiFunc> impl;
//     return impl;
// }

// void register_psi_method(const std::string& name, PsiFunc func) {
//     registry()[name] = func;
// }

// int compute_psi_ca(int party, const std::vector<int>& set, emp::NetIO* io) {
//     std::string mode = get_config().psi_mode;
//     // const char* env_mode = std::getenv("PSI_MODE");
//     // if (env_mode) mode = env_mode;

//     if (!registry().count(mode)) {
//         throw std::runtime_error("Unregistered psi_mode: " + mode);
//     }
//     return registry()[mode](party, set, io);
// }

// ------------ prg_nondeter_He Implementation ------------

template<typename T>
void iosend(int party, emp::NetIO* io, const T& key) {
    std::stringstream stream;
    auto size = key.save(stream, compr_mode_type::zstd);
    string str = stream.str();
    size_t send_size = str.size();
    // std::cerr << "[Party " << party << "] Sent "<< typeid(T).name() << " bytes: " << send_size << std::endl;
    io->send_data(&send_size, sizeof(send_size));
    io->send_data(str.data(), send_size);
}

template<typename T>
void iorecv(int party, emp::NetIO* io, const SEALContext& context, T& key) {
    size_t recv_size;
    io->recv_data(&recv_size, sizeof(recv_size));
    std::string str(recv_size, '\0');
    io->recv_data(&str[0], recv_size);
    std::stringstream stream(str);
    key.load(context, stream);
    // std::cerr << "[Party " << party << "] Received "<< typeid(T).name() << " bytes: " << recv_size << std::endl;
}

Integer mod_add(const Integer& a, const Integer& b, const Integer& p) {
    Integer sum = a + b;
    Bit over = sum >= p;
    return If(over, sum - p, sum);
}

Integer mod_mul(const Integer& a, const Integer& b, const Integer& p) {
    Integer product = a * b;
    return product % p; 
    // 取模要多一个sign bit
}

int psi_server_fhe(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
    if(get_config().test_mode) {
        uint64_t prg_seed = get_config().prg_seed;
        if(party == 1) {
            server_io->send_data(&prg_seed, sizeof(prg_seed));
            server_io->flush();
        } else {
            server_io->recv_data(&prg_seed, sizeof(prg_seed));
        }
        for(auto & client_io : client_connections) {
            client_io->send_data(&prg_seed, sizeof(prg_seed));
            client_io->flush();
        }
        get_config().prg_seed = prg_seed;
    }
    
    const GlobalConfig& config = get_config();
    int tot_rounds = config.mom_tt * config.mom_kk;
    ASSERT_MSG(tot_rounds <= config.seal_degree, "one batch is not enough for #rounds");
    
    // 阶段1: Server生成seed和密钥
    std::cerr << "[Server" << party << "] Phase 1: Generating seeds and keys" << std::endl;
    
    EncryptionParameters parms(scheme_type::bfv);
    size_t poly_modulus_degree = config.seal_degree;
    parms.set_poly_modulus_degree(poly_modulus_degree);
    parms.set_coeff_modulus(CoeffModulus::Create(poly_modulus_degree, { 52, 52, 36, 24, 24 }));
    parms.set_plain_modulus(PlainModulus::Batching(poly_modulus_degree, config.seal_plain_modulus));

    SEALContext context(parms);
    print_parameters(context); 

    // 生成密钥
    KeyGenerator keygen(context);
    SecretKey secret_key = keygen.secret_key();
    PublicKey public_key;
    keygen.create_public_key(public_key);
    RelinKeys relin_key_1, relin_key_2;
    keygen.create_relin_keys(relin_key_1);

    BatchEncoder batch_encoder(context);
    Encryptor encryptor(context, public_key);
    Evaluator evaluator(context);
    Decryptor decryptor(context, secret_key);

    // 生成并加密seeds
    std::mt19937_64 rnd(std::chrono::system_clock::now().time_since_epoch().count());
    AESGen gen_seed(std::uniform_int_distribution<uint64_t>(0, UINT64_MAX)(rnd));
    std::vector<std::vector<uint64_t>> batch_seed(config.seed_size, std::vector<uint64_t>(batch_encoder.slot_count(), 0ull));

    for(int now_round = 0; now_round < tot_rounds; ++now_round) {
        std::vector<bool> seed = gen_seed.get_bits(now_round, config.seed_size);
        for(int i = 0; i < config.seed_size; ++i) {
            batch_seed[i][now_round] = seed[i] ? parms.plain_modulus().value() - 1 : 1;
        }
    }

    std::vector<Plaintext> plain_seed(config.seed_size);
    std::vector<Ciphertext> encrypted_seed_1(config.seed_size), encrypted_seed_2(config.seed_size);
    for(int i = 0; i < config.seed_size; ++i) {
        batch_encoder.encode(batch_seed[i], plain_seed[i]);
        encryptor.encrypt(plain_seed[i], encrypted_seed_1[i]);
    }
    
    if(party == 1) {
        iosend(party, server_io, relin_key_1);
        for(int i = 0; i < config.seed_size; ++i) iosend(party, server_io, encrypted_seed_1[i]);
        server_io->flush();
        iorecv(party, server_io, context, relin_key_2);
        for(int i = 0; i < config.seed_size; ++i) iorecv(party, server_io, context, encrypted_seed_2[i]);
    } else {
        iorecv(party, server_io, context, relin_key_2);
        for(int i = 0; i < config.seed_size; ++i) iorecv(party, server_io, context, encrypted_seed_2[i]);
        iosend(party, server_io, relin_key_1);
        for(int i = 0; i < config.seed_size; ++i) iosend(party, server_io, encrypted_seed_1[i]);
        server_io->flush();
    }
    for(int i = 0; i < config.seed_size; ++i) {
        evaluator.multiply_plain_inplace(encrypted_seed_2[i], plain_seed[i]);
        evaluator.mod_switch_to_next_inplace(encrypted_seed_2[i]);
    }

    // 阶段2: 等待client处理完成
    for(auto & client_io : client_connections) {
        iosend(party, client_io, relin_key_2);
        for(int i = 0; i < config.seed_size; ++i) iosend(party, client_io, encrypted_seed_2[i]);
        client_io->flush();
    }
    std::cerr << "[Server" << party << "] Phase 2: Waiting for client processing" << std::endl;
    
    // 接收来自clients的处理结果
    std::stack<std::pair<int, Ciphertext>> t_stack;
    for(auto & client_io : client_connections) {
        Ciphertext client_result;
        iorecv(party, client_io, context, client_result);
        auto tmp = std::make_pair(1, client_result);
        while(!t_stack.empty()){
            auto top = t_stack.top();
            ASSERT_MSG(abs(top.first) >= abs(tmp.first), "stack top should be larger than current");
            if(top.first == tmp.first) {
                evaluator.add_inplace(tmp.second, top.second);
                tmp.first <<= 1;
                t_stack.pop();
            } else break;
        }
        t_stack.push(tmp);
    }
    Ciphertext combined_result = t_stack.top().second;
    t_stack.pop();
    while(!t_stack.empty()) {
        evaluator.add_inplace(combined_result, t_stack.top().second);
        t_stack.pop();
    }

    // 阶段3: Server进行secret sharing和MPC计算
    std::cerr << "[Server" << party << "] Phase 3: Secret sharing and MPC computation" << std::endl;

    // 与其他server进行secret sharing
    std::vector<uint64_t> rnd_1(batch_encoder.slot_count(), 0ull), rnd_2;
    for(int i = 0; i < tot_rounds; ++i) {
        rnd_1[i] = std::uniform_int_distribution<uint64_t>(0, parms.plain_modulus().value() - 1)(rnd);
    }
    Plaintext rnd_1_plain, rnd_2_plain;
    batch_encoder.encode(rnd_1, rnd_1_plain);
    evaluator.sub_plain_inplace(combined_result, rnd_1_plain);
    
    Ciphertext esti_cipher_1, esti_cipher_2;
    if(party == 1) {
        iosend(party, server_io, combined_result);
        server_io->flush();
        iorecv(party, server_io, context, esti_cipher_2);
    } else {
        iorecv(party, server_io, context, esti_cipher_2);
        iosend(party, server_io, combined_result);
        server_io->flush();
    }
    
    std::cerr<<"[Server" << party << "] final noise budget: " << decryptor.invariant_noise_budget(esti_cipher_2) << std::endl;
    decryptor.decrypt(esti_cipher_2, rnd_2_plain);
    batch_encoder.decode(rnd_2_plain, rnd_2);

    // MPC计算
    if(party == 1) {
        iorecv(party, server_io, context, esti_cipher_1);
        iorecv(party, server_io, context, esti_cipher_2);
        evaluator.add_plain_inplace(esti_cipher_1, rnd_1_plain);
        evaluator.add_plain_inplace(esti_cipher_2, rnd_2_plain);
        evaluator.multiply_inplace(esti_cipher_1, esti_cipher_2);
        for(int i = 0; i < tot_rounds; ++i) {
            rnd_1[i] = std::uniform_int_distribution<uint64_t>(0, parms.plain_modulus().value() - 1)(rnd);
        }
        batch_encoder.encode(rnd_1, rnd_1_plain);
        evaluator.sub_plain_inplace(esti_cipher_1, rnd_1_plain);
        iosend(party, server_io, esti_cipher_1);
        server_io->flush();
    } else {
        encryptor.encrypt(rnd_1_plain, esti_cipher_1);
        encryptor.encrypt(rnd_2_plain, esti_cipher_2);
        evaluator.mod_switch_to_next_inplace(esti_cipher_1);
        evaluator.mod_switch_to_next_inplace(esti_cipher_2);
        evaluator.mod_switch_to_next_inplace(esti_cipher_1);
        evaluator.mod_switch_to_next_inplace(esti_cipher_2);
        iosend(party, server_io, esti_cipher_2);
        iosend(party, server_io, esti_cipher_1);
        server_io->flush();
        iorecv(party, server_io, context, esti_cipher_2);
        decryptor.decrypt(esti_cipher_2, rnd_2_plain);
        batch_encoder.decode(rnd_2_plain, rnd_2);
    }

    setup_semi_honest(server_io, party);
    int mpcbitlen = 32;
    Integer *esti_1 = new Integer[get_config().mom_tt], *esti_2 = new Integer[get_config().mom_tt], 
        *esti_sum = new Integer[get_config().mom_tt];
    Integer modp(mpcbitlen, parms.plain_modulus().value(), PUBLIC), mod23p(mpcbitlen, parms.plain_modulus().value()*2/3, PUBLIC);
    for(int tt = 0, i = 0; tt < get_config().mom_tt; ++tt) {
        int64_t val_1 = 0, val_2 = 0;
        for(int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
            val_1 += rnd_1[i];
            val_2 += rnd_2[i];
        }
        val_1 %= parms.plain_modulus().value();
        val_2 %= parms.plain_modulus().value();
        esti_1[tt] = Integer(mpcbitlen, val_1, ALICE);
        esti_2[tt] = Integer(mpcbitlen, val_2, BOB);
    }
    for(int tt = 0; tt < get_config().mom_tt; ++tt){
        esti_sum[tt] = mod_add(esti_1[tt], esti_2[tt], modp);
        Bit over = esti_sum[tt] >= mod23p;
        esti_sum[tt] = If(over, esti_sum[tt] - modp, esti_sum[tt]);
    }
    sort(esti_sum, get_config().mom_tt);
    int64_t psi_ca = esti_sum[get_config().mom_tt/2].reveal<int64_t>(PUBLIC);
    delete[] esti_1, esti_2, esti_sum;
    finalize_semi_honest();
    return psi_ca / get_config().mom_kk;
}

int psi_client_fhe(int client_id, int server_id, const std::vector<int>& input_set, emp::NetIO* io) {
    if(get_config().test_mode) {
        uint64_t prg_seed;
        io->recv_data(&prg_seed, sizeof(prg_seed));
        get_config().prg_seed = prg_seed;
    }

    const GlobalConfig& config = get_config();
    int party = config.party;
    int tot_rounds = config.mom_tt * config.mom_kk;
    
    std::cerr << "[Client" << config.party << "] Phase 2: Processing input set" << std::endl;
    
    // 接收来自server的密钥和seeds
    EncryptionParameters parms(scheme_type::bfv);
    size_t poly_modulus_degree = config.seal_degree;
    parms.set_poly_modulus_degree(poly_modulus_degree);
    parms.set_coeff_modulus(CoeffModulus::Create(poly_modulus_degree, { 52, 52, 36, 24, 24 }));
    parms.set_plain_modulus(PlainModulus::Batching(poly_modulus_degree, config.seal_plain_modulus));

    SEALContext context(parms);
    BatchEncoder batch_encoder(context);
    Evaluator evaluator(context);
    
    RelinKeys relin_key;
    std::vector<Ciphertext> encrypted_seed(config.seed_size);
    
    // 从对应的server接收密钥和seeds
    iorecv(party, io, context, relin_key);
    for(int i = 0; i < config.seed_size; ++i) {
        iorecv(party, io, context, encrypted_seed[i]);
    }
    
    // 处理输入集合
    AESGen aes_gen(0);
    std::unordered_map<uint64_t, Ciphertext> t_map;
    std::stack<std::pair<int, Ciphertext>> t_stack_in;
    std::vector<Ciphertext> calc_prg(config.prg_dd);
    
    for(auto & item : input_set) {
        std::vector<int> ids = aes_gen.get_id_group(0, item);
        sort(ids.begin(), ids.end());
        for(int i = 0; i < config.prg_dd; i+=2) {
            if(i + 1 == config.prg_dd) {
                calc_prg[i] = encrypted_seed[ids[i]];
                evaluator.mod_switch_to_next_inplace(calc_prg[i]);
            } else {
                uint64_t key = ((uint64_t)ids[i]<<32) | ids[i+1];
                if(!t_map.count(key)) {
                    evaluator.multiply(encrypted_seed[ids[i]], encrypted_seed[ids[i+1]], calc_prg[i]);
                    evaluator.relinearize_inplace(calc_prg[i], relin_key);
                    evaluator.mod_switch_to_next_inplace(calc_prg[i]);
                    t_map[key] = calc_prg[i];
                } else {
                    calc_prg[i] = t_map[key];
                }
            }
        }
        for(int w = 2; w < config.prg_dd; w <<= 1) {
            for(int i = 0; i < config.prg_dd; i += (w<<1)) {
                if(i + w < config.prg_dd) {
                    evaluator.multiply_inplace(calc_prg[i], calc_prg[i + w]);
                    evaluator.relinearize_inplace(calc_prg[i], relin_key);
                }
            }
        }
        auto sum_prg = std::make_pair(1, calc_prg[0]);
        while(!t_stack_in.empty()){
            auto top = t_stack_in.top();
            ASSERT_MSG(abs(top.first) >= abs(sum_prg.first), "stack top should be larger than current");
            if(top.first == sum_prg.first) {
                evaluator.add_inplace(sum_prg.second, top.second);
                sum_prg.first <<= 1;
                t_stack_in.pop();
            } else break;
        }
        t_stack_in.push(sum_prg);
    }
    
    Ciphertext esti_cipher = t_stack_in.top().second;
    t_stack_in.pop();
    while(!t_stack_in.empty()) {
        evaluator.add_inplace(esti_cipher, t_stack_in.top().second);
        t_stack_in.pop();
    }
    
    // 发送结果给对应的server
    iosend(party, io, esti_cipher);
    io->flush();
    
    std::cerr << "[Client" << config.party << "] Processing completed" << std::endl;
    return -1; // Client不返回PSI大小
}

// -----------------------------------------------------------
// Naive PSI Implementation (Plaintext Version)
// -----------------------------------------------------------

int psi_server_naive(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
    if(get_config().test_mode) {
        uint64_t prg_seed = get_config().prg_seed;
        if(party == 1) {
            server_io->send_data(&prg_seed, sizeof(prg_seed));
            server_io->flush();
        } else {
            server_io->recv_data(&prg_seed, sizeof(prg_seed));
        }
        for(auto & client_io : client_connections) {
            client_io->send_data(&prg_seed, sizeof(prg_seed));
            client_io->flush();
        }
        get_config().prg_seed = prg_seed;
    }

    const GlobalConfig& config = get_config();
    int tot_rounds = config.mom_tt * config.mom_kk;
    
    std::cerr << "[Server" << party << "] Naive PSI: Phase 1 - Generating seeds" << std::endl;
    
    // 阶段1: 生成明文seeds
    std::mt19937_64 rnd(std::chrono::system_clock::now().time_since_epoch().count());
    AESGen gen_seed(std::uniform_int_distribution<uint64_t>(0, UINT64_MAX)(rnd));
    std::vector<std::vector<uint8_t>> seeds(config.seed_size);
    
    for(int round = 0; round < tot_rounds; ++round) {
        std::vector<bool> seed = gen_seed.get_bits(round, config.seed_size);
        for(int i = 0; i < config.seed_size; ++i) {
            seeds[i].push_back(seed[i] ? 1 : 0);
        }
    }
    
    // 与其他server交换seeds
    std::vector<std::vector<uint8_t>> seeds_other(config.seed_size, std::vector<uint8_t>(tot_rounds, 0));
    if(party == 1) {
        for(int i = 0; i < config.seed_size; ++i) {
            for(int j = 0; j < tot_rounds; ++j) {
                server_io->send_data(&seeds[i][j], sizeof(uint8_t));
            }
        }
        server_io->flush();
        for(int i = 0; i < config.seed_size; ++i) {
            for(int j = 0; j < tot_rounds; ++j) {
                server_io->recv_data(&seeds_other[i][j], sizeof(uint8_t));
            }
        }
    } else {
        for(int i = 0; i < config.seed_size; ++i) {
            for(int j = 0; j < tot_rounds; ++j) {
                server_io->recv_data(&seeds_other[i][j], sizeof(uint8_t));
            }
        }
        for(int i = 0; i < config.seed_size; ++i) {
            for(int j = 0; j < tot_rounds; ++j) {
                server_io->send_data(&seeds[i][j], sizeof(uint8_t));
            }
        }
        server_io->flush();
    }
    
    // 计算组合seeds
    std::vector<std::vector<uint8_t>> combined_seeds(config.seed_size);
    for(int i = 0; i < config.seed_size; ++i) {
        for(int j = 0; j < tot_rounds; ++j) 
            combined_seeds[i].push_back(seeds[i][j] ^ seeds_other[i][j]);
    }
    
    // 阶段2: 发送seeds给clients
    std::cerr << "[Server" << party << "] Naive PSI: Phase 2 - Sending seeds to clients" << std::endl;
    for(auto& client_io : client_connections) {
        for(int i = 0; i < config.seed_size; ++i) {
            for(int j = 0; j < tot_rounds; ++j) {
                client_io->send_data(&combined_seeds[i][j], sizeof(uint8_t));
            }
        }
        client_io->flush();
    }
    
    // 阶段3: 接收clients的处理结果
    std::cerr << "[Server" << party << "] Naive PSI: Phase 3 - Receiving client results" << std::endl;
    std::vector<int64_t> combined_result(tot_rounds, 0);
    for(auto& client_io : client_connections) {
        for(int i = 0; i < tot_rounds; ++i) {
            int64_t tmp;
            client_io->recv_data(&tmp, sizeof(int64_t));
            combined_result[i] += tmp;
        }
    }
    
    // 阶段4: 计算最终结果
    std::cerr << "[Server" << party << "] Naive PSI: Phase 4 - Computing final result" << std::endl;

    if(party == 1) {
        for(int i = 0; i < tot_rounds; ++i) {
            server_io->send_data(&combined_result[i], sizeof(int64_t));
        }
        server_io->flush();
        return -1;
    } else {
        std::vector<int64_t> result, means;
        for(int i = 0; i < tot_rounds; ++i) {
            int64_t tmp;
            server_io->recv_data(&tmp, sizeof(int64_t));
            result.push_back(tmp);
        }
        for(int i = 0; i < tot_rounds; ++i) combined_result[i] *= result[i];
        for(int tt = 0, i = 0; tt < config.mom_tt; ++tt) {
            uint64_t sum = 0;
            for(int kk = 0; kk < config.mom_kk; ++kk, ++i) {
                sum += combined_result[i];
            }
            means.push_back(sum / config.mom_kk);
        }
        sort(means.begin(), means.end());
        return means[config.mom_tt / 2];
    }
}

int psi_client_naive(int client_id, int server_id, const std::vector<int>& input_set, emp::NetIO* io) {
    if(get_config().test_mode) {
        uint64_t prg_seed;
        io->recv_data(&prg_seed, sizeof(prg_seed));
        get_config().prg_seed = prg_seed;
    }

    const GlobalConfig& config = get_config();
    int tot_rounds = config.mom_tt * config.mom_kk;
    
    std::cerr << "[Client" << client_id << "] Naive PSI: Processing input set" << std::endl;
    
    // 接收来自server的seeds
    std::vector<std::vector<uint8_t>> seeds(config.seed_size, std::vector<uint8_t>(tot_rounds, 0));
    for(int i = 0; i < config.seed_size; ++i) {
        for(int j = 0; j < tot_rounds; ++j) {
            io->recv_data(&seeds[i][j], sizeof(uint8_t));
        }
    }
    
    // 处理输入集合
    AESGen aes_gen(0);
    std::vector<int64_t> result(tot_rounds, 0);

    for(const auto& item : input_set) {
        std::vector<int> ids = aes_gen.get_id_group(0, item);
        sort(ids.begin(), ids.end());
        // 为每个round计算贡献
        for(int round = 0; round < tot_rounds; ++round) {
            uint8_t contribution = 0;
            for(int i = 0; i < config.prg_dd; i++) {
                contribution ^= seeds[ids[i]][round];
            }
            result[round] += contribution ? -1 : 1;
        }
    }
    
    // 发送结果给server
    for(int i = 0; i < tot_rounds; ++i) {
        io->send_data(&result[i], sizeof(int64_t));
    }
    io->flush();
    
    std::cerr << "[Client" << client_id << "] Naive PSI: Processing completed" << std::endl;
    return -1; // Client不返回PSI大小
}

// -----------------------------------------------------------
// Registration Mechanism
// -----------------------------------------------------------

// 函数指针类型定义
using PsiServerFunc = int(*)(int, emp::NetIO*, std::vector<emp::NetIO*>&);
using PsiClientFunc = int(*)(int, int, const std::vector<int>&, emp::NetIO*);

// 注册表
static std::unordered_map<std::string, PsiServerFunc> server_registry;
static std::unordered_map<std::string, PsiClientFunc> client_registry;

// 注册函数
void register_psi_server(const std::string& name, PsiServerFunc func) {
    server_registry[name] = func;
}

void register_psi_client(const std::string& name, PsiClientFunc func) {
    client_registry[name] = func;
}

// 主函数 - 使用注册机制
int psi_server(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
    const GlobalConfig& config = get_config();
    std::string mode = config.psi_mode;
    
    if (!server_registry.count(mode)) {
        throw std::runtime_error("Unregistered psi_mode for server: " + mode);
    }
    
    return server_registry[mode](party, server_io, client_connections);
}

int psi_client(int client_id, int server_id, const std::vector<int>& input_set, emp::NetIO* io) {
    const GlobalConfig& config = get_config();
    std::string mode = config.psi_mode;
    
    if (!client_registry.count(mode)) {
        throw std::runtime_error("Unregistered psi_mode for client: " + mode);
    }
    
    return client_registry[mode](client_id, server_id, input_set, io);
}

// 静态注册
static bool register_functions() {
    register_psi_server("naive", psi_server_naive);
    register_psi_server("fhe", psi_server_fhe);
    register_psi_client("naive", psi_client_naive);
    register_psi_client("fhe", psi_client_fhe);
    return true;
}

// 静态变量确保注册在程序启动时执行
static bool registered = register_functions();
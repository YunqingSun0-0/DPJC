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
#include <iomanip>
#include <functional>
#include <thread>
#include <mutex>
#include <atomic>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
#include <cstdint>
#include <stdexcept>
#include <iostream>
#include <limits>
#include "oleu32.h"
using namespace seal;
using namespace emp;
#include <array>
#include <cstring>
struct Deg3Coeff {
    uint64_t c0, c1, c2, c3;
};

// ------------ prg_nondeter_He Implementation ------------

// Global communication size counter for server1
static size_t total_communication_size = 0;

template<typename T>
void iosend(int party, emp::NetIO* io, const T& key, size_t* recorded_bytes = nullptr) {
    std::stringstream stream;
    key.save(stream, compr_mode_type::zstd);
    string str = stream.str();
    size_t send_size = str.size();
    size_t bytes_with_header = sizeof(send_size) + send_size;
    if (party == 1) total_communication_size += bytes_with_header;
    if (recorded_bytes) *recorded_bytes = bytes_with_header;
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
    if (party == 1) total_communication_size += sizeof(recv_size) + recv_size;
    // std::cerr << "[Party " << party << "] Received "<< typeid(T).name() << " bytes: " << recv_size << std::endl;
}

inline uint32_t reveal_share_u32_modp(uint32_t my_share, uint32_t p, int party, emp::NetIO* io) {
    uint32_t other_share = 0;

    if (party == ALICE) {
        io->send_data(&my_share, sizeof(uint32_t));
        io->recv_data(&other_share, sizeof(uint32_t));
    } else {
        io->recv_data(&other_share, sizeof(uint32_t));
        io->send_data(&my_share, sizeof(uint32_t));
    }
    io->flush();

    uint64_t s = (uint64_t)my_share + (uint64_t)other_share;
    return (uint32_t)(s % p);
}

inline uint32_t add_mod_u32(uint32_t a, uint32_t b, uint32_t p) {
    uint64_t s = (uint64_t)a + (uint64_t)b;
    return (uint32_t)(s % p);
}

inline uint32_t mul_mod_u32(uint32_t a, uint32_t b, uint32_t p) {
    uint64_t z = (uint64_t)a * (uint64_t)b;
    return (uint32_t)(z % p);
}

inline uint32_t pow2_mod_u32(int exponent, uint32_t p) {
    ASSERT_MSG(exponent >= 0, "pow2 exponent must be non-negative");
    uint64_t result = 1 % p;
    uint64_t base = 2 % p;
    int e = exponent;
    while (e > 0) {
        if (e & 1) {
            result = (result * base) % p;
        }
        base = (base * base) % p;
        e >>= 1;
    }
    return static_cast<uint32_t>(result);
}

inline int ole_bit_length_from_plain_modulus(uint64_t plain_modulus) {
    if (plain_modulus < 2) {
        throw std::runtime_error("plain_modulus must be >= 2 for OLE recovery");
    }
    if (plain_modulus > static_cast<uint64_t>(UINT32_MAX)) {
        throw std::runtime_error("plain_modulus exceeds OLE_U32 range (must be <= 2^32-1)");
    }

    uint32_t max_encoded_value = static_cast<uint32_t>(plain_modulus - 1);
    int bit_length = 0;
    do {
        ++bit_length;
        max_encoded_value >>= 1;
    } while (max_encoded_value != 0u);
    return bit_length;
}

inline uint64_t encode_weight_with_optional_scaling(
    int64_t raw_weight,
    uint64_t weight_scale_div,
    uint64_t plain_modulus
) {
    ASSERT_MSG(raw_weight > 0, "weight must be a positive integer");
    ASSERT_MSG(weight_scale_div >= 1, "weight_scale_div must be >= 1");
    ASSERT_MSG(plain_modulus >= 2, "plain_modulus must be >= 2");

    const uint64_t weight_u64 = static_cast<uint64_t>(raw_weight);
    uint64_t encoded = weight_u64;
    if (weight_scale_div > 1) {
        // Round-to-nearest integer for positive weights.
        encoded = (weight_u64 + (weight_scale_div / 2)) / weight_scale_div;
        if (encoded == 0) {
            encoded = 1;
        }
    }

    if (encoded >= plain_modulus) {
        std::ostringstream oss;
        oss << "encoded weight " << encoded
            << " must be < plain_modulus " << plain_modulus
            << " (try increasing --weight_scale_div)";
        throw std::runtime_error(oss.str());
    }
    return encoded;
}

inline int64_t restore_weight_scale_if_needed(
    int64_t estimate_scaled,
    int mom_kk,
    bool weighted_mode,
    uint64_t weight_scale_div
) {
    ASSERT_MSG(mom_kk > 0, "mom_kk must be positive");
    __int128 value = static_cast<__int128>(estimate_scaled);

    if (weighted_mode && weight_scale_div > 1) {
        const __int128 scale_sq =
            static_cast<__int128>(weight_scale_div) * static_cast<__int128>(weight_scale_div);
        value *= scale_sq;
    }

    value /= static_cast<__int128>(mom_kk);

    if (value > static_cast<__int128>(std::numeric_limits<int64_t>::max()) ||
        value < static_cast<__int128>(std::numeric_limits<int64_t>::min())) {
        throw std::runtime_error("restored weighted estimate overflows int64_t");
    }
    return static_cast<int64_t>(value);
}

inline bool is_default_8192_seal_profile(const GlobalConfig& config) {
    static const std::vector<int> kDefault8192Coeff = {60, 60, 36, 27, 27};
    return config.seal_degree == 8192 &&
           config.seal_plain_modulus == 24 &&
           config.seal_coeff_modulus == kDefault8192Coeff;
}

inline Ciphertext mul_cipher_by_scalar_double_add(
    const Ciphertext& input,
    uint64_t scalar,
    Evaluator& evaluator
) {
    ASSERT_MSG(scalar >= 1, "scalar must be >= 1 for double-add path");
    if (scalar == 1) {
        return input;
    }

    Ciphertext base = input;
    Ciphertext acc;
    bool acc_initialized = false;
    uint64_t k = scalar;

    while (k > 0) {
        if (k & 1ULL) {
            if (!acc_initialized) {
                acc = base;
                acc_initialized = true;
            } else {
                evaluator.add_inplace(acc, base);
            }
        }
        k >>= 1;
        if (k > 0) {
            evaluator.add_inplace(base, base);
        }
    }
    return acc;
}

Bit geq_unsigned(const Integer& a, const Integer& b);

Integer mod_add(const Integer& a, const Integer& b, const Integer& p) {
    // Do unsigned modular addition in widened domain to avoid signed overflow.
    const size_t base_len = std::max({a.size(), b.size(), p.size()});
    const size_t wide_len = base_len + 2;

    Integer a_wide = a;
    Integer b_wide = b;
    Integer p_wide = p;
    a_wide.resize(wide_len, false);
    b_wide.resize(wide_len, false);
    p_wide.resize(wide_len, false);

    Integer sum = a_wide + b_wide;
    Bit over = geq_unsigned(sum, p_wide);
    Integer reduced = If(over, sum - p_wide, sum);
    reduced.resize(base_len, false);
    return reduced;
}

Integer mod_mul(const Integer& a, const Integer& b, const Integer& p) {
    // Integer product = a * b;
    // return product % p;
    // emp::Integer keeps multiplication result at the same bit-width.
    // Widen first to avoid truncation before modulo.
    const size_t base_len = a.size();
    const size_t wide_len = std::max(base_len, p.size()) * 2 + 1;

    Integer a_wide = a;
    Integer b_wide = b;
    Integer p_wide = p;
    a_wide.resize(wide_len, false);
    b_wide.resize(wide_len, false);
    p_wide.resize(wide_len, false);

    Integer product = a_wide * b_wide;
    Integer reduced = product % p_wide;
    reduced.resize(base_len, false);
    return reduced;
}

Bit geq_unsigned(const Integer& a, const Integer& b) {
    const size_t base_len = std::max(a.size(), b.size());
    const size_t wide_len = base_len + 1;
    Integer a_wide = a;
    Integer b_wide = b;
    a_wide.resize(wide_len, false);
    b_wide.resize(wide_len, false);
    return a_wide >= b_wide;
}

int64_t psi_server_fhe(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
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
    std::cerr << "[Server" << party << "] mom_kk=" << config.mom_kk
              << ", mom_tt=" << config.mom_tt
              << ", total_rounds=" << tot_rounds << std::endl;
    ASSERT_MSG(tot_rounds <= config.seal_degree, "one batch is not enough for #rounds");
    
    // 阶段1: Server生成seed和密钥
    std::cerr << "[Server" << party << "] Phase 1: Generating seeds and keys" << std::endl;

    // 开始时间统计
    auto start_time = std::chrono::high_resolution_clock::now();
    auto key_gen_start = start_time;
    
    // Reset communication size counter for server1
    if (party == 1) total_communication_size = 0;
    
    EncryptionParameters parms(scheme_type::bfv);
    size_t poly_modulus_degree = config.seal_degree;
    parms.set_poly_modulus_degree(poly_modulus_degree);
    parms.set_coeff_modulus(CoeffModulus::Create(poly_modulus_degree, config.seal_coeff_modulus));
    parms.set_plain_modulus(PlainModulus::Batching(poly_modulus_degree, config.seal_plain_modulus));

    SEALContext context(parms);
    print_parameters(context); 

    // 生成密钥
    KeyGenerator keygen(context);
    SecretKey secret_key = keygen.secret_key(), sk_noise_budget;
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
    // std::mt19937_64 rnd(19920929+party*1000000000); // ftest_fhe_vs_naive.py 专用
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
    if(!get_config().test_mode) {
        std::cerr << "noise budget - encrypt seed: " << decryptor.invariant_noise_budget(encrypted_seed_1[0]) << std::endl;
        if(party == 1) {
            iosend(party, server_io, secret_key);
            server_io->flush();
            iorecv(party, server_io, context, sk_noise_budget);
        } else {
            iorecv(party, server_io, context, sk_noise_budget);
            iosend(party, server_io, secret_key);
            server_io->flush();
        }
    }
    
    size_t relin_key_1_send_bytes = 0;
    size_t encrypted_seed_1_total_send_bytes = 0;
    if(party == 1) {
        iosend(party, server_io, relin_key_1, &relin_key_1_send_bytes);
        for(int i = 0; i < config.seed_size; ++i) {
            size_t one_seed_send_bytes = 0;
            iosend(party, server_io, encrypted_seed_1[i], &one_seed_send_bytes);
            encrypted_seed_1_total_send_bytes += one_seed_send_bytes;
        }
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

    // 计算密钥生成和传输时间
    auto key_gen_end = std::chrono::high_resolution_clock::now();
    auto key_gen_duration = std::chrono::duration_cast<std::chrono::milliseconds>(key_gen_end - key_gen_start);
    double key_gen_time = key_gen_duration.count() / 1000.0;
    
    // 输出实际通信大小（以server1为准）
    if (party == 1) {
        std::cerr << "Key generation time: " << key_gen_time << "s" << std::endl;
        std::cerr << "key_gen_comm_mb: " << (encrypted_seed_1_total_send_bytes / (1024.0 * 1024.0)) << std::endl;
        std::cerr << "broadcast_comm_mb: " << ((relin_key_1_send_bytes + encrypted_seed_1_total_send_bytes) / (1024.0 * 1024.0)) << std::endl;
    }

    if(!get_config().test_mode) {
        Decryptor decryptor_noise_budget(context, sk_noise_budget);
        std::cerr << "noise budget - multiply seed: " << decryptor_noise_budget.invariant_noise_budget(encrypted_seed_2[0]) << std::endl;
        for(auto & client_io : client_connections) {
            iosend(party, client_io, sk_noise_budget);
            client_io->flush();
        }
    }

    // 阶段2: 等待client处理完成
    for(auto & client_io : client_connections) {
        iosend(party, client_io, relin_key_2);
        for(int i = 0; i < config.seed_size; ++i) iosend(party, client_io, encrypted_seed_2[i]);
        client_io->flush();
    }
    std::cerr << "[Server" << party << "] Phase 2: Waiting for client processing" << std::endl;

    // Reset communication size counter for server1
    if (party == 1) total_communication_size = 0;
    
    // 接收来自clients的处理结果（不计入聚合计算时间）
    std::vector<Ciphertext> client_results;
    client_results.reserve(client_connections.size());
    std::stack<std::pair<int, Ciphertext>> t_stack;
    for(auto & client_io : client_connections) {
        Ciphertext client_result;
        iorecv(party, client_io, context, client_result);
        client_results.push_back(std::move(client_result));
    }

    // 仅统计“收到结果之后，把结果全都加起来”的计算时间（不含网络接收）
    auto client_aggregate_start = std::chrono::high_resolution_clock::now();
    for(const auto & client_result : client_results) {
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
    auto client_aggregate_end = std::chrono::high_resolution_clock::now();
    auto client_aggregate_us = std::chrono::duration_cast<std::chrono::microseconds>(
        client_aggregate_end - client_aggregate_start);
    double client_aggregate_ms = client_aggregate_us.count() / 1000.0;
    std::cerr << std::fixed << std::setprecision(3)
              << "[Server" << party << "] Client results aggregation time: "
              << client_aggregate_ms << "ms (" << client_aggregate_us.count() << "us)"
              << std::defaultfloat << std::endl;
    if (party == 1) {
        std::cerr << "client_server_comm_mb: " << (total_communication_size / (1024.0 * 1024.0)) << std::endl;
    }
    // 阶段3: Server进行secret sharing和MPC计算
    std::cerr << "[Server" << party << "] Phase 3: Secret sharing and MPC computation" << std::endl;

    // 与其他server进行secret sharing（会用DD替换估计, 只统计2PC时间）
    std::vector<uint64_t> rnd_1(batch_encoder.slot_count(), 0ull), rnd_2;
    for(int i = 0; i < tot_rounds; ++i) {
        rnd_1[i] = std::uniform_int_distribution<uint64_t>(0, parms.plain_modulus().value() - 1)(rnd);
    }
    Plaintext rnd_1_plain, rnd_2_plain;
    batch_encoder.encode(rnd_1, rnd_1_plain);
    evaluator.sub_plain_inplace(combined_result, rnd_1_plain);

    if(!get_config().test_mode) {
        Decryptor decryptor_noise_budget(context, sk_noise_budget);
        std::cerr << "noise budget - before sharing: " << decryptor_noise_budget.invariant_noise_budget(combined_result) << std::endl;
    }
    
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
    
    decryptor.decrypt(esti_cipher_2, rnd_2_plain);
    batch_encoder.decode(rnd_2_plain, rnd_2);

    // 开始服务器恢复时间统计 （只统计2PC时间）
    auto server_recover_start = std::chrono::high_resolution_clock::now();
    if (party == 1) total_communication_size = 0; //this is for communication of iorecv in seal

    // // MPC计算
    // if(party == 1) {
    //     iorecv(party, server_io, context, esti_cipher_1);
    //     iorecv(party, server_io, context, esti_cipher_2);
    //     evaluator.add_plain_inplace(esti_cipher_1, rnd_1_plain);
    //     evaluator.add_plain_inplace(esti_cipher_2, rnd_2_plain);
    //     evaluator.multiply_inplace(esti_cipher_1, esti_cipher_2);
    //     for(int i = 0; i < tot_rounds; ++i) {
    //         rnd_1[i] = std::uniform_int_distribution<uint64_t>(0, parms.plain_modulus().value() - 1)(rnd);
    //     }
    //     batch_encoder.encode(rnd_1, rnd_1_plain);
    //     evaluator.sub_plain_inplace(esti_cipher_1, rnd_1_plain);
    //     iosend(party, server_io, esti_cipher_1);
    //     server_io->flush();
    // } else {
    //     encryptor.encrypt(rnd_1_plain, esti_cipher_1);
    //     encryptor.encrypt(rnd_2_plain, esti_cipher_2);
    //     evaluator.mod_switch_to_next_inplace(esti_cipher_1);
    //     evaluator.mod_switch_to_next_inplace(esti_cipher_2);
    //     evaluator.mod_switch_to_next_inplace(esti_cipher_1);
    //     evaluator.mod_switch_to_next_inplace(esti_cipher_2);
    //     iosend(party, server_io, esti_cipher_2);
    //     iosend(party, server_io, esti_cipher_1);
    //     server_io->flush();
    //     iorecv(party, server_io, context, esti_cipher_2);
    //     decryptor.decrypt(esti_cipher_2, rnd_2_plain);
    //     batch_encoder.decode(rnd_2_plain, rnd_2);
    // }
    // size_t io_bytes_before = 0, io_bytes_after = 0;
    // if (party == 1) {
    //     io_bytes_before = server_io->counter;
    // }
    // setup_semi_honest(server_io, party);
    // int mpcbitlen = 32;
    // Integer *esti_1 = new Integer[get_config().mom_tt], *esti_2 = new Integer[get_config().mom_tt], 
    //     *esti_sum = new Integer[get_config().mom_tt];
    // Integer modp(mpcbitlen, parms.plain_modulus().value(), PUBLIC), mod23p(mpcbitlen, parms.plain_modulus().value()*2/3, PUBLIC);
    // for(int tt = 0, i = 0; tt < get_config().mom_tt; ++tt) {
    //     int64_t val_1 = 0, val_2 = 0;
    //     for(int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
    //         val_1 += rnd_1[i];
    //         val_2 += rnd_2[i];
    //     }
    //     val_1 %= parms.plain_modulus().value();
    //     val_2 %= parms.plain_modulus().value();
    //     esti_1[tt] = Integer(mpcbitlen, val_1, ALICE);
    //     esti_2[tt] = Integer(mpcbitlen, val_2, BOB);
    // }
    // for(int tt = 0; tt < get_config().mom_tt; ++tt){
    //     esti_sum[tt] = mod_add(esti_1[tt], esti_2[tt], modp);
    //     Bit over = esti_sum[tt] >= mod23p;
    //     esti_sum[tt] = If(over, esti_sum[tt] - modp, esti_sum[tt]);
    // }
    // sort(esti_sum, get_config().mom_tt);
    // int64_t psi_ca = esti_sum[get_config().mom_tt/2].reveal<int64_t>(PUBLIC);
    // delete[] esti_1, esti_2, esti_sum;
    // finalize_semi_honest();

    // Before setup_semi_honest: record NetIO counters
    size_t io_bytes_before = 0, io_bytes_after = 0;
    if (party == 1) {
        io_bytes_before = server_io->counter;
    }

    const uint64_t plain_mod_u64 = parms.plain_modulus().value();
    const int ole_bit_length = ole_bit_length_from_plain_modulus(plain_mod_u64);
    const uint32_t plain_mod_u32 = static_cast<uint32_t>(plain_mod_u64);

    int64_t psi_ca = 0;
    const bool use_weighted_multilimb_exact =
        (config.weighted_mode && config.weighted_multilimb_exact);
    if (party == 1 && use_weighted_multilimb_exact) {
        std::cerr << "[Server1] weighted_multilimb_exact enabled: "
                  << "limb-decomposed OLE recovery (no per-round residue reveal)." << std::endl;
    }

    int64_t *esti_sum = new int64_t[get_config().mom_tt];
    if (use_weighted_multilimb_exact) {
        IKNP <NetIO> *cot = new IKNP <NetIO> (server_io, true);
        const bool ole_sender_role = (party == BOB);
        if (party == BOB) {
            cot->setup_send();
        } else {
            cot->setup_recv();
        }

        const uint32_t mod23_u32 = (uint32_t)(((uint64_t)plain_mod_u32 * 2) / 3);
        OLE_U32_MODP<emp::NetIO> ole(server_io, cot, plain_mod_u32, ole_bit_length, ole_sender_role);

        const int limb_bits = config.weighted_limb_bits;
        ASSERT_MSG(limb_bits >= 1 && limb_bits <= 16, "weighted_limb_bits must be in [1, 16]");
        const uint32_t mask = (1u << limb_bits) - 1u;
        const uint32_t shift_limb_mod = pow2_mod_u32(limb_bits, plain_mod_u32);
        const uint32_t shift_2limb_mod = pow2_mod_u32(2 * limb_bits, plain_mod_u32);

        const size_t rounds = static_cast<size_t>(tot_rounds);

        // Cross term 1: xA * yB (split into 4 limb products).
        std::vector<uint32_t> in_xy_00(rounds), out_xy_00(rounds);
        std::vector<uint32_t> in_xy_01(rounds), out_xy_01(rounds);
        std::vector<uint32_t> in_xy_10(rounds), out_xy_10(rounds);
        std::vector<uint32_t> in_xy_11(rounds), out_xy_11(rounds);

        // Cross term 2: yA * xB (split into 4 limb products).
        std::vector<uint32_t> in_yx_00(rounds), out_yx_00(rounds);
        std::vector<uint32_t> in_yx_01(rounds), out_yx_01(rounds);
        std::vector<uint32_t> in_yx_10(rounds), out_yx_10(rounds);
        std::vector<uint32_t> in_yx_11(rounds), out_yx_11(rounds);

        std::vector<uint32_t> local_terms(rounds, 0u);

        for (size_t i = 0; i < rounds; ++i) {
            const uint32_t xA = (party == ALICE) ? (uint32_t)rnd_2[i] : 0u;
            const uint32_t yA = (party == ALICE) ? (uint32_t)rnd_1[i] : 0u;
            const uint32_t xB = (party == BOB)   ? (uint32_t)rnd_1[i] : 0u;
            const uint32_t yB = (party == BOB)   ? (uint32_t)rnd_2[i] : 0u;

            const uint32_t x_local = (party == ALICE) ? xA : xB;
            const uint32_t y_local = (party == ALICE) ? yA : yB;
            local_terms[i] = mul_mod_u32(x_local, y_local, plain_mod_u32);

            const uint32_t xA0 = xA & mask, xA1 = xA >> limb_bits;
            const uint32_t yA0 = yA & mask, yA1 = yA >> limb_bits;
            const uint32_t xB0 = xB & mask, xB1 = xB >> limb_bits;
            const uint32_t yB0 = yB & mask, yB1 = yB >> limb_bits;

            // xA * yB
            in_xy_00[i] = (party == ALICE) ? xA0 : yB0;
            in_xy_01[i] = (party == ALICE) ? xA0 : yB1;
            in_xy_10[i] = (party == ALICE) ? xA1 : yB0;
            in_xy_11[i] = (party == ALICE) ? xA1 : yB1;

            // yA * xB
            in_yx_00[i] = (party == ALICE) ? yA0 : xB0;
            in_yx_01[i] = (party == ALICE) ? yA0 : xB1;
            in_yx_10[i] = (party == ALICE) ? yA1 : xB0;
            in_yx_11[i] = (party == ALICE) ? yA1 : xB1;
        }

        ole.compute(out_xy_00, in_xy_00);
        ole.compute(out_xy_01, in_xy_01);
        ole.compute(out_xy_10, in_xy_10);
        ole.compute(out_xy_11, in_xy_11);

        ole.compute(out_yx_00, in_yx_00);
        ole.compute(out_yx_01, in_yx_01);
        ole.compute(out_yx_10, in_yx_10);
        ole.compute(out_yx_11, in_yx_11);

        for (int tt = 0, i = 0; tt < get_config().mom_tt; ++tt) {
            uint32_t bucket_share = 0;
            for (int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
                uint32_t prod_share = local_terms[i];

                uint32_t cross_xy = out_xy_00[i];
                uint32_t cross_xy_01_10 = add_mod_u32(out_xy_01[i], out_xy_10[i], plain_mod_u32);
                cross_xy_01_10 = mul_mod_u32(cross_xy_01_10, shift_limb_mod, plain_mod_u32);
                uint32_t cross_xy_11 = mul_mod_u32(out_xy_11[i], shift_2limb_mod, plain_mod_u32);
                cross_xy = add_mod_u32(cross_xy, cross_xy_01_10, plain_mod_u32);
                cross_xy = add_mod_u32(cross_xy, cross_xy_11, plain_mod_u32);

                uint32_t cross_yx = out_yx_00[i];
                uint32_t cross_yx_01_10 = add_mod_u32(out_yx_01[i], out_yx_10[i], plain_mod_u32);
                cross_yx_01_10 = mul_mod_u32(cross_yx_01_10, shift_limb_mod, plain_mod_u32);
                uint32_t cross_yx_11 = mul_mod_u32(out_yx_11[i], shift_2limb_mod, plain_mod_u32);
                cross_yx = add_mod_u32(cross_yx, cross_yx_01_10, plain_mod_u32);
                cross_yx = add_mod_u32(cross_yx, cross_yx_11, plain_mod_u32);

                prod_share = add_mod_u32(prod_share, cross_xy, plain_mod_u32);
                prod_share = add_mod_u32(prod_share, cross_yx, plain_mod_u32);
                bucket_share = add_mod_u32(bucket_share, prod_share, plain_mod_u32);
            }

            uint32_t bucket_sum = reveal_share_u32_modp(bucket_share, plain_mod_u32, party, server_io);
            esti_sum[tt] = (bucket_sum >= mod23_u32)
                ? ((int64_t)bucket_sum - (int64_t)plain_mod_u32)
                : (int64_t)bucket_sum;
        }
        delete cot;
    } else {
        IKNP <NetIO> *cot = new IKNP <NetIO> (server_io, true);
        const bool ole_sender_role = (party == BOB);
        if (party == BOB) {
            cot->setup_send();
        } else {
            cot->setup_recv();
        }

        const uint32_t mod23_u32 = (uint32_t)(((uint64_t)plain_mod_u32 * 2) / 3);
        OLE_U32_MODP<emp::NetIO> ole(server_io, cot, plain_mod_u32, ole_bit_length, ole_sender_role);

        const size_t rounds = static_cast<size_t>(tot_rounds);
        std::vector<uint32_t> local_terms(rounds, 0u);
        std::vector<uint32_t> cross1_in(rounds, 0u), cross1_out(rounds, 0u);
        std::vector<uint32_t> cross2_in(rounds, 0u), cross2_out(rounds, 0u);

        for (size_t i = 0; i < rounds; ++i) {
            const uint32_t xA = (party == ALICE) ? (uint32_t)rnd_2[i] : 0u;
            const uint32_t yA = (party == ALICE) ? (uint32_t)rnd_1[i] : 0u;
            const uint32_t xB = (party == BOB)   ? (uint32_t)rnd_1[i] : 0u;
            const uint32_t yB = (party == BOB)   ? (uint32_t)rnd_2[i] : 0u;

            local_terms[i] = (party == ALICE)
                ? mul_mod_u32(xA, yA, plain_mod_u32)
                : mul_mod_u32(xB, yB, plain_mod_u32);

            cross1_in[i] = (party == ALICE) ? xA : yB;
            cross2_in[i] = (party == ALICE) ? yA : xB;
        }
        ole.compute(cross1_out, cross1_in);
        ole.compute(cross2_out, cross2_in);

        for (int tt = 0, i = 0; tt < get_config().mom_tt; ++tt) {
            uint32_t bucket_share = 0;
            for (int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
                uint32_t prod_share = local_terms[i];
                prod_share = add_mod_u32(prod_share, cross1_out[i], plain_mod_u32);
                prod_share = add_mod_u32(prod_share, cross2_out[i], plain_mod_u32);
                bucket_share = add_mod_u32(bucket_share, prod_share, plain_mod_u32);
            }

            // Reveal once per tt-bucket (small communication, limited leakage).
            uint32_t bucket_sum = reveal_share_u32_modp(bucket_share, plain_mod_u32, party, server_io);
            esti_sum[tt] = (bucket_sum >= mod23_u32)
                ? ((int64_t)bucket_sum - (int64_t)plain_mod_u32)
                : (int64_t)bucket_sum;
        }
        delete cot;
    }

    std::sort(esti_sum, esti_sum + get_config().mom_tt);
    psi_ca = esti_sum[get_config().mom_tt / 2];
    delete[] esti_sum;

    size_t mpc_comm = 0;
    if (party == 1) {
        io_bytes_after = server_io->counter;
        mpc_comm = io_bytes_after - io_bytes_before;
        std::cerr << "2PC communication: " << mpc_comm << " bytes ("
                << (mpc_comm / (1024.0 * 1024.0)) << " MB)" << std::endl;
    }
    
    // 计算服务器恢复时间 = 客户端结果聚合 + 2PC
    auto server_recover_end = std::chrono::high_resolution_clock::now();
    auto server_recover_duration = std::chrono::duration_cast<std::chrono::milliseconds>(server_recover_end - server_recover_start);
    double server_2pc_time = server_recover_duration.count() / 1000.0;
    double server_recover_time = server_2pc_time + client_aggregate_us.count() / 1000000.0;

    if(party == 1) {
        std::cerr << "server_2pc_time: " << server_2pc_time << "s" << std::endl;
        std::cerr << "Server recovery time: " << server_recover_time << "s" << std::endl;
        std::cerr << "Server recovery Communication: " << ((total_communication_size + mpc_comm)/ (1024.0 * 1024.0) ) << " MB" << std::endl;
    }
    
    // 计算总时间
    auto total_end = std::chrono::high_resolution_clock::now();
    auto total_duration = std::chrono::duration_cast<std::chrono::milliseconds>(total_end - start_time);
    double total_time = total_duration.count() / 1000.0;
    
    if(party == 1) {
        std::cerr << "Total server time: " << total_time << "s" << std::endl;
    }
    
    return restore_weight_scale_if_needed(
        psi_ca,
        get_config().mom_kk,
        config.weighted_mode,
        config.weight_scale_div
    );
}

int64_t psi_client_fhe(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io) {
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
    parms.set_coeff_modulus(CoeffModulus::Create(poly_modulus_degree, config.seal_coeff_modulus));
    parms.set_plain_modulus(PlainModulus::Batching(poly_modulus_degree, config.seal_plain_modulus));

    SEALContext context(parms);
    BatchEncoder batch_encoder(context);
    Evaluator evaluator(context);
    
    RelinKeys relin_key;
    SecretKey sk_noise_budget;
    std::vector<Ciphertext> encrypted_seed(config.seed_size);
    
    if(!get_config().test_mode) {
        iorecv(party, io, context, sk_noise_budget);
    }

    // 从对应的server接收密钥和seeds
    iorecv(party, io, context, relin_key);
    for(int i = 0; i < config.seed_size; ++i) {
        iorecv(party, io, context, encrypted_seed[i]);
    }
    
    // 处理输入集合 - 单线程版本
    // 开始客户端计算时间统计
    auto client_compute_start = std::chrono::high_resolution_clock::now();

    std::cerr << "[Client" << config.party << "] Using 1 thread for " 
              << input_set.size() << " items" << std::endl;

    std::unordered_map<uint64_t, Ciphertext> t_map;
    std::stack<std::pair<int, Ciphertext>> t_stack;
    AESGen aes_gen(0);
    std::vector<Ciphertext> calc_prg(config.prg_dd);
    std::vector<uint64_t> weight_slots(batch_encoder.slot_count(), 0ull);
    Plaintext weight_plain;
    const uint64_t plain_modulus = parms.plain_modulus().value();
    const uint64_t weight_scale_div = config.weight_scale_div;
    const bool use_double_add_for_weight =
        config.weighted_mode && is_default_8192_seal_profile(config);
    if (config.weighted_mode && weight_scale_div > 1) {
        const long double restore_scale =
            static_cast<long double>(weight_scale_div) * static_cast<long double>(weight_scale_div);
        std::cerr << "[Client" << config.party << "] weighted scaling enabled: divide-by "
                  << weight_scale_div << " before encode, restore by x"
                  << std::fixed << std::setprecision(0) << restore_scale
                  << std::defaultfloat << " after recovery" << std::endl;
    }
    if (use_double_add_for_weight) {
        std::cerr << "[Client" << config.party << "] weighted scalar multiply path: "
                  << "double-add (default 8192 profile)" << std::endl;
    }

    for(const auto& item : input_set) {
        std::vector<int> ids = aes_gen.get_id_group(0, item.value);
        sort(ids.begin(), ids.end());

        // 处理PRG计算
        for(int i = 0; i < config.prg_dd; i += 2) {
            if(i + 1 == config.prg_dd) {
                calc_prg[i] = encrypted_seed[ids[i]];
                evaluator.mod_switch_to_next_inplace(calc_prg[i]);
            } else {
                uint64_t key = ((uint64_t)ids[i] << 32) | ids[i + 1];
                if(t_map.count(key)) {
                    calc_prg[i] = t_map[key];
                } else {
                    evaluator.multiply(encrypted_seed[ids[i]], encrypted_seed[ids[i + 1]], calc_prg[i]);
                    evaluator.relinearize_inplace(calc_prg[i], relin_key);
                    evaluator.mod_switch_to_next_inplace(calc_prg[i]);
                    t_map[key] = calc_prg[i];
                }
            }
        }

        if (item.weight != 1) {
            const uint64_t encoded_weight = encode_weight_with_optional_scaling(
                item.weight, weight_scale_div, plain_modulus);
            if (encoded_weight != 1) {
                if (use_double_add_for_weight) {
                    calc_prg[0] = mul_cipher_by_scalar_double_add(calc_prg[0], encoded_weight, evaluator);
                } else {
                    std::fill(weight_slots.begin(), weight_slots.end(), 0ull);
                    for (int round = 0; round < tot_rounds; ++round) {
                        weight_slots[round] = encoded_weight;
                    }
                    batch_encoder.encode(weight_slots, weight_plain);
                    evaluator.multiply_plain_inplace(calc_prg[0], weight_plain);
                }
            }
        }

        // 多层乘法计算
        for(int w = 2; w < config.prg_dd; w <<= 1) {
            for(int i = 0; i < config.prg_dd; i += (w << 1)) {
                if(i + w < config.prg_dd) {
                    evaluator.multiply_inplace(calc_prg[i], calc_prg[i + w]);
                    evaluator.relinearize_inplace(calc_prg[i], relin_key);
                }
            }
        }

        // 累加到stack
        auto sum_prg = std::make_pair(1, calc_prg[0]);
        while(!t_stack.empty()) {
            auto top = t_stack.top();
            ASSERT_MSG(abs(top.first) >= abs(sum_prg.first), "stack top should be larger than current");
            if(top.first == sum_prg.first) {
                evaluator.add_inplace(sum_prg.second, top.second);
                sum_prg.first <<= 1;
                t_stack.pop();
            } else break;
        }
        t_stack.push(sum_prg);
    }

    // 最终合并
    Ciphertext esti_cipher = t_stack.top().second;
    t_stack.pop();
    while(!t_stack.empty()) {
        evaluator.add_inplace(esti_cipher, t_stack.top().second);
        t_stack.pop();
    }

    // 计算客户端计算时间
    auto client_compute_end = std::chrono::high_resolution_clock::now();
    auto client_compute_duration = std::chrono::duration_cast<std::chrono::milliseconds>(client_compute_end - client_compute_start);
    double client_compute_time = client_compute_duration.count() / 1000.0;
    
    std::cerr << "Client computation time: " << client_compute_time << "s" << std::endl;
    std::cerr << "[Client" << config.party << "] Processing completed" << std::endl;

    // 发送结果给对应的server
    iosend(party, io, esti_cipher);
    io->flush();

    return -1; // Client不返回PSI大小
}

// -----------------------------------------------------------
// Naive PSI Implementation (Plaintext Version, Tug-of-War)
// -----------------------------------------------------------

int64_t psi_server_naive(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
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
    // std::mt19937_64 rnd(19920929+party*1000000000); // ftest_fhe_vs_naive.py 专用
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
            int64_t sum = 0;
            for(int kk = 0; kk < config.mom_kk; ++kk, ++i) {
                sum += combined_result[i];
            }
            means.push_back(sum / config.mom_kk);
        }
        sort(means.begin(), means.end());
        return means[config.mom_tt / 2];
    }
}

int64_t psi_client_naive(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io) {
    if(get_config().test_mode) {
        uint64_t prg_seed;
        io->recv_data(&prg_seed, sizeof(prg_seed));
        get_config().prg_seed = prg_seed;
    }

    const GlobalConfig& config = get_config();
    const int tot_rounds = config.mom_tt * config.mom_kk;

    std::cerr << "[Client" << client_id << "] Naive PSI: Processing input set" << std::endl;

    // 接收来自 server 的 seeds
    std::vector<std::vector<uint8_t>> seeds(
        config.seed_size, std::vector<uint8_t>(tot_rounds, 0)
    );
    for (int i = 0; i < config.seed_size; ++i) {
        for (int j = 0; j < tot_rounds; ++j) {
            io->recv_data(&seeds[i][j], sizeof(uint8_t));
        }
    }

    std::vector<int64_t> result(tot_rounds, 0);

    auto make_sig = [](const std::vector<int>& ids) -> std::string {
        std::string sig;
        sig.reserve(ids.size() * sizeof(int));
        for (int v : ids) {
            sig.append(reinterpret_cast<const char*>(&v), sizeof(v));
        }
        return sig;
    };

    std::unordered_map<std::string, int64_t> global_ids_count;
    global_ids_count.reserve(input_set.size());

    AESGen aes_gen(0);
    for (size_t idx = 0; idx < input_set.size(); ++idx) {
        const auto item = input_set[idx];

        std::vector<int> ids = aes_gen.get_id_group(0, item.value);
        std::sort(ids.begin(), ids.end());

        global_ids_count[make_sig(ids)]++;

        std::vector<const uint8_t*> row_ptrs(config.prg_dd);
        for (int i = 0; i < config.prg_dd; ++i) {
            row_ptrs[i] = seeds[ids[i]].data();
        }

        for (int round = 0; round < tot_rounds; ++round) {
            uint8_t contribution = 0;
            for (int i = 0; i < config.prg_dd; ++i) {
                contribution ^= row_ptrs[i][round];
            }
            result[round] += contribution ? -item.weight : item.weight;
        }
    }

    // subtract collision pairs with same ids
    for (const auto& kv : global_ids_count) {
        int64_t k = kv.second;
        if (k >= 2) {
            int64_t pairs = k * (k - 1) / 2;
            for (int round = 0; round < tot_rounds; ++round) {
                result[round] -= pairs;
            }
        }
    }

    // 发送结果给 server
    for (int i = 0; i < tot_rounds; ++i) {
        io->send_data(&result[i], sizeof(int64_t));
    }
    io->flush();

    std::cerr << "[Client" << client_id << "] Naive PSI: Processing completed" << std::endl;
    return -1;
}
// -----------------------------------------------------------
// Naive PSI Implementation (uniform randomness)
// -----------------------------------------------------------

inline int64_t bool_to_pm1_from_block(const emp::block& b) {
    uint64_t low = (uint64_t)_mm_cvtsi128_si64(b);
    return (low & 1ULL) ? -1LL : 1LL;
}
inline bool prf_bool_item_round(emp::PRG& prg, uint64_t item, uint64_t round) {
    // 把 (round, item) 编码进一个 128-bit block
    emp::block in = emp::makeBlock(round, item);
    emp::block out = in;

    emp::AES_ecb_encrypt_blks(&out, 1, &prg.aes);

    alignas(16) uint64_t tmp[2];
    _mm_storeu_si128((__m128i*)tmp, out);

    return (tmp[0] & 1ULL) != 0;
}

int64_t psi_server_naive_uniform(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
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
            int64_t sum = 0;
            for(int kk = 0; kk < config.mom_kk; ++kk, ++i) {
                sum += combined_result[i];
            }
            means.push_back(sum / config.mom_kk);
        }
        sort(means.begin(), means.end());
        return means[config.mom_tt / 2];
    }
}

int64_t psi_client_naive_uniform(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io) {
    if (get_config().test_mode) {
        uint64_t prg_seed;
        io->recv_data(&prg_seed, sizeof(prg_seed));
        get_config().prg_seed = prg_seed;
    }

    const GlobalConfig& config = get_config();
    const int tot_rounds = config.mom_tt * config.mom_kk;

    std::cerr << "[Client" << client_id << "] Naive PSI: Processing input set" << std::endl;

    std::vector<int64_t> result(tot_rounds, 0);

    // 共享 seed，线程内各自构造 PRG
    const emp::block seed_block = emp::makeBlock(0ULL, (uint64_t)config.prg_seed);

    constexpr int BATCH = AES_BATCH_SIZE;

    emp::PRG prg(&seed_block);
    emp::block tmp[BATCH];
    for (size_t idx = 0; idx < input_set.size(); ++idx) {
        uint64_t item_u64 = (uint64_t)(uint32_t)input_set[idx].value;
        const int64_t weight = input_set[idx].weight;

        int round = 0;
        for (; round + BATCH <= tot_rounds; round += BATCH) {
            // 构造输入块: (round, item)
            for (int j = 0; j < BATCH; ++j) {
                tmp[j] = emp::makeBlock((uint64_t)(round + j), item_u64);
            }

            // 批量 AES
            emp::AES_ecb_encrypt_blks<BATCH>(tmp, &prg.aes);

            // 累加结果
            for (int j = 0; j < BATCH; ++j) {
                result[round + j] += weight * bool_to_pm1_from_block(tmp[j]);
            }
        }

        // 处理剩余 rounds
        int remain = tot_rounds - round;
        if (remain > 0) {
            for (int j = 0; j < remain; ++j) {
                tmp[j] = emp::makeBlock((uint64_t)(round + j), item_u64);
            }

            emp::AES_ecb_encrypt_blks(tmp, remain, &prg.aes);

            for (int j = 0; j < remain; ++j) {
                result[round + j] += weight * bool_to_pm1_from_block(tmp[j]);
            }
        }
    }

    for (int i = 0; i < tot_rounds; ++i) {
        io->send_data(&result[i], sizeof(int64_t));
    }
    io->flush();

    std::cerr << "[Client" << client_id << "] Naive PSI: Processing completed" << std::endl;
    return -1;
}
// -----------------------------------------------------------
// Naive PSI Implementation (Plaintext Version, (fourwise randomness))
// -----------------------------------------------------------

static constexpr uint64_t P = ((1ULL << 61) - 1);

inline uint64_t mod_p(__uint128_t x) {
    uint64_t low  = (uint64_t)x & P;
    uint64_t high = (uint64_t)(x >> 61);
    uint64_t res = low + high;
    res = (res & P) + (res >> 61);
    if (res >= P) res -= P;
    return res;
}

inline uint64_t add_mod_p(uint64_t a, uint64_t b) {
    uint64_t s = a + b;
    s = (s & P) + (s >> 61);
    if (s >= P) s -= P;
    return s;
}

inline uint64_t mul_mod_p(uint64_t a, uint64_t b) {
    return mod_p((__uint128_t)a * b);
}

inline uint64_t eval_deg3_horner(uint64_t x,
                                 uint64_t c0,
                                 uint64_t c1,
                                 uint64_t c2,
                                 uint64_t c3) {
    uint64_t y = c3;
    y = add_mod_p(mul_mod_p(y, x), c2);
    y = add_mod_p(mul_mod_p(y, x), c1);
    y = add_mod_p(mul_mod_p(y, x), c0);
    return y;
}


int64_t psi_server_naive_fourwise(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
    //1. 发送prg_seed 给clients，保证server和clients使用相同的随机数
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
    
    // 阶段3: 计算最终结果
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
            int64_t sum = 0;
            for(int kk = 0; kk < config.mom_kk; ++kk, ++i) {
                sum += combined_result[i];
            }
            means.push_back(sum / config.mom_kk);
        }
        sort(means.begin(), means.end());
        return means[config.mom_tt / 2];
    }
}

int64_t psi_client_naive_fourwise(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io) {
    if (get_config().test_mode) {
        uint64_t prg_seed;
        io->recv_data(&prg_seed, sizeof(prg_seed));
        get_config().prg_seed = prg_seed;
    }

    const GlobalConfig& config = get_config();
    const int tot_rounds = config.mom_tt * config.mom_kk;

    std::cerr << "[Client" << client_id << "] Naive PSI: Processing input set" << std::endl;

    AESGen gen_seed(config.prg_seed);

    // -------- Step 1: 预生成所有 round 的系数（AoS） --------
    std::vector<Deg3Coeff> coeffs(tot_rounds);

    auto get_u64x4 = [&](int round) -> std::array<uint64_t, 4> {
        std::vector<bool> bits = gen_seed.get_bits(round, 256);

        std::array<uint64_t, 4> words{0, 0, 0, 0};
        for (int w = 0; w < 4; ++w) {
            uint64_t x = 0;
            const int base = 64 * w;
            for (int b = 0; b < 64; ++b) {
                x |= (static_cast<uint64_t>(bits[base + b]) << b);
            }
            words[w] = x;
        }
        return words;
    };

    for (int round = 0; round < tot_rounds; ++round) {
        std::array<uint64_t, 4> words = get_u64x4(round);
        coeffs[round].c0 = mod_p(words[0]);
        coeffs[round].c1 = mod_p(words[1]);
        coeffs[round].c2 = mod_p(words[2]);
        coeffs[round].c3 = mod_p(words[3]);
    }

    // -------- Step 2: items 转连续 uint64_t --------
    std::vector<uint64_t> items;
    items.reserve(input_set.size());
    std::vector<int64_t> weights;
    weights.reserve(input_set.size());
    for (const auto& item : input_set) {
        items.push_back(mod_p((uint64_t)(uint32_t)item.value + 1ULL));
        weights.push_back(item.weight);
    }

    std::vector<int64_t> result(tot_rounds, 0);

    // round tile 大小，建议试 128/256/512
    constexpr int TILE = 256;
    constexpr int UNROLL = 8;

    // -------- Step 3: over items 累加 --------
    for (size_t idx = 0; idx < items.size(); ++idx) {
        const uint64_t x = items[idx];
        const int64_t weight = weights[idx];

        for (int base = 0; base < tot_rounds; base += TILE) {
            const int end = std::min(base + TILE, tot_rounds);

            int round = base;
            for (; round + UNROLL - 1 < end; round += UNROLL) {
                const Deg3Coeff& a0 = coeffs[round + 0];
                const Deg3Coeff& a1 = coeffs[round + 1];
                const Deg3Coeff& a2 = coeffs[round + 2];
                const Deg3Coeff& a3 = coeffs[round + 3];
                const Deg3Coeff& a4 = coeffs[round + 4];
                const Deg3Coeff& a5 = coeffs[round + 5];
                const Deg3Coeff& a6 = coeffs[round + 6];
                const Deg3Coeff& a7 = coeffs[round + 7];

                uint64_t v0 = eval_deg3_horner(x, a0.c0, a0.c1, a0.c2, a0.c3);
                uint64_t v1 = eval_deg3_horner(x, a1.c0, a1.c1, a1.c2, a1.c3);
                uint64_t v2 = eval_deg3_horner(x, a2.c0, a2.c1, a2.c2, a2.c3);
                uint64_t v3 = eval_deg3_horner(x, a3.c0, a3.c1, a3.c2, a3.c3);
                uint64_t v4 = eval_deg3_horner(x, a4.c0, a4.c1, a4.c2, a4.c3);
                uint64_t v5 = eval_deg3_horner(x, a5.c0, a5.c1, a5.c2, a5.c3);
                uint64_t v6 = eval_deg3_horner(x, a6.c0, a6.c1, a6.c2, a6.c3);
                uint64_t v7 = eval_deg3_horner(x, a7.c0, a7.c1, a7.c2, a7.c3);

                result[round + 0] += weight * (1 - 2 * (int64_t)(v0 & 1ULL));
                result[round + 1] += weight * (1 - 2 * (int64_t)(v1 & 1ULL));
                result[round + 2] += weight * (1 - 2 * (int64_t)(v2 & 1ULL));
                result[round + 3] += weight * (1 - 2 * (int64_t)(v3 & 1ULL));
                result[round + 4] += weight * (1 - 2 * (int64_t)(v4 & 1ULL));
                result[round + 5] += weight * (1 - 2 * (int64_t)(v5 & 1ULL));
                result[round + 6] += weight * (1 - 2 * (int64_t)(v6 & 1ULL));
                result[round + 7] += weight * (1 - 2 * (int64_t)(v7 & 1ULL));
            }

            for (; round < end; ++round) {
                const Deg3Coeff& a = coeffs[round];
                uint64_t v = eval_deg3_horner(x, a.c0, a.c1, a.c2, a.c3);
                result[round] += weight * (1 - 2 * (int64_t)(v & 1ULL));
            }
        }
    }

    for (int i = 0; i < tot_rounds; ++i) {
        io->send_data(&result[i], sizeof(int64_t));
    }
    io->flush();

    std::cerr << "[Client" << client_id << "] Naive PSI: Processing completed" << std::endl;
    return -1;
}

// -----------------------------------------------------------
// Registration Mechanism
// -----------------------------------------------------------

// 函数指针类型定义
using PsiServerFunc = int64_t(*)(int, emp::NetIO*, std::vector<emp::NetIO*>&);
using PsiClientFunc = int64_t(*)(int, int, const std::vector<WeightedInput>&, emp::NetIO*);

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
int64_t psi_server(int party, emp::NetIO* server_io, std::vector<emp::NetIO*>& client_connections) {
    const GlobalConfig& config = get_config();
    std::string mode = config.psi_mode;
    
    if (!server_registry.count(mode)) {
        throw std::runtime_error("Unregistered psi_mode for server: " + mode);
    }
    
    return server_registry[mode](party, server_io, client_connections);
}

int64_t psi_client(int client_id, int server_id, const std::vector<WeightedInput>& input_set, emp::NetIO* io) {
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
    register_psi_server("naive_uniform", psi_server_naive_uniform); 
    register_psi_server("naive_fourwise", psi_server_naive_fourwise);
    register_psi_client("naive", psi_client_naive);
    register_psi_client("fhe", psi_client_fhe);
    register_psi_client("naive_uniform", psi_client_naive_uniform); 
    register_psi_client("naive_fourwise", psi_client_naive_fourwise);
    return true;
}

// 静态变量确保注册在程序启动时执行
static bool registered = register_functions();

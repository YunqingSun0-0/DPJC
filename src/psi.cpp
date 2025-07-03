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
using namespace seal;
using namespace emp;

static std::unordered_map<std::string, PsiFunc>& registry() {
    static std::unordered_map<std::string, PsiFunc> impl;
    return impl;
}

void register_psi_method(const std::string& name, PsiFunc func) {
    registry()[name] = func;
}

int compute_psi_ca(int party, const std::vector<int>& set, emp::NetIO* io) {
    std::string mode = get_config().psi_mode;
    // const char* env_mode = std::getenv("PSI_MODE");
    // if (env_mode) mode = env_mode;

    if (!registry().count(mode)) {
        throw std::runtime_error("Unregistered psi_mode: " + mode);
    }
    return registry()[mode](party, set, io);
}

// ---------- Naive PSI Implementation ----------

int psi_ca_naive(int party, const std::vector<int>& input_set, emp::NetIO* io) {
    if (party == 1) {
        std::cerr<< "[Party 1] Sending input set: ";
        for (const auto& item : input_set) {
            std::cerr<< item << " ";
            io->send_data(&item, sizeof(item));
        }
        int end_signal = -1;
        io->send_data(&end_signal, sizeof(end_signal));
        io->flush();
        std::cerr<< std::endl;
        return -1; 
    } else {
        std::unordered_set<int> set1;
        char buffer[128];

        std::cerr<< "[Party 2] Receiving input set: ";
        while(1) {
            io->recv_data(buffer, sizeof(int));
            int item;
            std::memcpy(&item, buffer, sizeof(int));
            if (item == -1) break;
            set1.insert(item);
            std::cerr<< item << " ";
        }
        std::cerr<< std::endl;

        std::cerr<< "[Party 2] set: ";
        int intersection_size = 0;
        for (const auto& item : input_set) {
            std::cerr<< item << " ";
            if (set1.count(item)) {
                ++intersection_size;
            }
        }
        std::cerr<< std::endl;

        return intersection_size;
    }
}

// ------------ prg_nondeter_naive Implementation ------------

int psi_ca_prg_nondeter_naive(int party, const std::vector<int>& input_set, emp::NetIO* io) {
    AESGen aes_gen(0);
    int tot = get_config().mom_kk * get_config().mom_tt;
    vector<int> esti;

    for(int tt=0; tt<=tot; tt++) {
        std::vector<bool> seed = aes_gen.get_bits(tt, get_config().seed_size);
        int esti_val = 0;

        for(auto & item : input_set) {
            int rd = 0;
            // std::vector<int> ids = aes_gen.get_id_group(tt, item);
            std::vector<int> ids = aes_gen.get_id_group(0, item);
            for(const auto & id : ids) {
                rd ^= seed[id];
            }
            if(!rd) ++esti_val;
            else --esti_val;
        }
        esti.push_back(esti_val);
    }

    if (party == 1) {
        for(auto & item : esti) {
            io->send_data(&item, sizeof(item));
        }
        io->flush();
        return -1;
    } else {
        vector<int64_t> mom;
        for(int tt = 0, i=0; tt < get_config().mom_tt; ++tt) {
            int64_t tmp_tot = 0;
            for(int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
                int esti_val;
                io->recv_data(&esti_val, sizeof(esti_val));
                esti_val *= esti[i];
                tmp_tot += esti_val;
            }
            mom.push_back(tmp_tot/ get_config().mom_kk); 
        }
        sort(mom.begin(), mom.end());

        if(!get_config().test_mode) {
            std::cerr<<"prg(no He) mom: ";
            for (const auto& val : mom) {
                std::cerr << val << " ";
            }
            std::cerr << std::endl;
        }

        return mom[mom.size()/2]; 
    }
}

// ------------ prg_nondeter_He Implementation ------------

void debug_decrypt_output(int party, int round, const SEALContext& context, 
    const SecretKey& secret_key, const Ciphertext& encrypted) {
    if(party == 2) return;
    Decryptor decryptor(context, secret_key);
    BatchEncoder batch_encoder(context);
    Plaintext plain;
    std::vector<uint64_t> decoded;
    decryptor.decrypt(encrypted, plain);
    batch_encoder.decode(plain, decoded);
    std::cerr << decoded[round] << std::endl;
}

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

/*int psi_ca_prg_nondeter_He(int party, const std::vector<int>& input_set, emp::NetIO* io) {
    int tot_rounds = get_config().mom_tt * get_config().mom_kk;
    ASSERT_MSG(tot_rounds <= get_config().seal_degree, "one batch is not enough for #rounds");
    EncryptionParameters parms(scheme_type::bfv);

    // set parameters
    size_t poly_modulus_degree = get_config().seal_degree;
    parms.set_poly_modulus_degree(poly_modulus_degree);
    parms.set_coeff_modulus(CoeffModulus::BFVDefault(poly_modulus_degree));
    parms.set_plain_modulus(PlainModulus::Batching(poly_modulus_degree, 20));

    SEALContext context(parms);
    print_parameters(context); 

    // generate keys
    KeyGenerator keygen(context);
    SecretKey secret_key = keygen.secret_key();
    PublicKey public_key_1, public_key_2;
    keygen.create_public_key(public_key_1);
    RelinKeys relin_key_1, relin_key_2;
    keygen.create_relin_keys(relin_key_1);

    BatchEncoder batch_encoder(context);
    Encryptor encryptor(context, public_key_1);
    Evaluator evaluator(context);
    Decryptor decryptor(context, secret_key);

    // send public key, relin key
    iosend(party, io, public_key_1);
    iosend(party, io, relin_key_1);
    iorecv(party, io, context, public_key_2);
    iorecv(party, io, context, relin_key_2);

    // iosend(party, io, secret_key);
    // SecretKey debug_sk;
    // iorecv(party, io, context, debug_sk);
    // Decryptor debug_decryptor(context, debug_sk);

    // encrypt and send seeds
    std::mt19937_64 rnd(std::chrono::system_clock::now().time_since_epoch().count());
    AESGen gen_seed(std::uniform_int_distribution<uint64_t>(0, UINT64_MAX)(rnd));
    std::vector<std::vector<uint64_t>> batch_seed(get_config().seed_size, std::vector<uint64_t>(batch_encoder.slot_count(), 0ull));

    for(int now_round = 0; now_round < tot_rounds; ++now_round) {
        std::vector<bool> seed = gen_seed.get_bits(now_round, get_config().seed_size);
        for(int i = 0; i < get_config().seed_size; ++i) {
            batch_seed[i][now_round] = seed[i] ? parms.plain_modulus().value() - 1 : 1;
        }
    }

    std::vector<Plaintext> plain_seed(get_config().seed_size);
    std::vector<Ciphertext> encrypted_seed(get_config().seed_size);
    for(int i = 0; i < get_config().seed_size; ++i) {
        batch_encoder.encode(batch_seed[i], plain_seed[i]);
        encryptor.encrypt(plain_seed[i], encrypted_seed[i]);
        iosend(party, io, encrypted_seed[i]);
        iorecv(party, io, context, encrypted_seed[i]);
        // debug_decrypt_output(party, 0, context, debug_sk, encrypted_seed[i]);
        // if(party == 1) std::cerr << "[Party " << party << "] noise budget: " << debug_decryptor.invariant_noise_budget(encrypted_seed[i]) << std::endl;
    }

    // Ciphertext qwq;
    // evaluator.multiply(encrypted_seed[0], encrypted_seed[1], qwq);
    // evaluator.relinearize_inplace(qwq, relin_key_2);
    // debug_decrypt_output(party, 0, context, debug_sk, encrypted_seed[0]);
    // debug_decrypt_output(party, 0, context, debug_sk, encrypted_seed[1]);
    // debug_decrypt_output(party, 0, context, debug_sk, qwq);
    
    // estimate
    std::cerr<<"[Party " << party << "] Starting estimation" << std::endl;
    AESGen aes_gen(0);
    std::unordered_map<uint64_t, Ciphertext> t_map;
    std::stack<std::pair<int, Ciphertext>> t_stack_out;

    for(int now_round = 0; now_round < tot_rounds; ++now_round) {
        std::cerr<<"[Party " << party << "] Round " << now_round << "......" << std::endl;
        std::vector<Ciphertext> calc_prg_2(get_config().prg_dd);
        std::stack<std::pair<int, Ciphertext>> t_stack_in;
        for(auto & item : input_set) {
            // std::cerr<<"[Party " << party << "] Item " << item << "......" << std::endl;
            std::vector<int> ids = aes_gen.get_id_group(now_round, item);
            sort(ids.begin(), ids.end());
            for(int i = 0; i < get_config().prg_dd; i+=2) {
                if(i + 1 == get_config().prg_dd) {
                    calc_prg_2[i] = encrypted_seed[ids[i]];
                } else {
                    uint64_t key = ((uint64_t)ids[i]<<32) | ids[i+1];
                    // if(party == 1) std::cerr<<"[Party " << party << "] Item " << item << " id: " << ids[i] << " and " << ids[i+1] << std::endl;
                    if(!t_map.count(key)) {
                        evaluator.multiply(encrypted_seed[ids[i]], encrypted_seed[ids[i+1]], calc_prg_2[i]);
                        evaluator.relinearize_inplace(calc_prg_2[i], relin_key_2);
                        t_map[key] = calc_prg_2[i];
                    } else {
                        calc_prg_2[i] = t_map[key];
                    }
                }
                // debug_decrypt_output(party, now_round, context, debug_sk, calc_prg_2[i]);
            }
            for(int w = 2; w < get_config().prg_dd; w <<= 1) {
                for(int i = 0; i + w < get_config().prg_dd; i += (w<<1)) {
                    // if(party == 1) std::cerr<<"[Party " << party << "] Item " << item << " Multiply "<< i << " and " << i + w << std::endl;
                    // debug_decrypt_output(party, now_round, context, debug_sk, calc_prg_2[i]);
                    // debug_decrypt_output(party, now_round, context, debug_sk, calc_prg_2[i + w]);
                    evaluator.multiply_inplace(calc_prg_2[i], calc_prg_2[i + w]);
                    evaluator.relinearize_inplace(calc_prg_2[i], relin_key_2);
                    // debug_decrypt_output(party, now_round, context, debug_sk, calc_prg_2[i]);
                    // if(party == 1) std::cerr<<"[Party " << party << "] noise budget: "<< debug_decryptor.invariant_noise_budget(calc_prg_2[i]) << std::endl;
                }
            }
            // if(party == 1) std::cerr<<"[Party " << party << "] Item " << item << " Insert into stack" << std::endl;
            // debug_decrypt_output(party, now_round, context, debug_sk, calc_prg_2[0]);
            auto calc_prg = std::make_pair(1, calc_prg_2[0]);
            for(auto & id : ids) calc_prg.first *= (batch_seed[id][now_round] == 1 ? 1 : -1);
            while(!t_stack_in.empty()){
                auto top = t_stack_in.top();
                ASSERT_MSG(abs(top.first) >= abs(calc_prg.first), "stack top should be larger than current");
                if(top.first == calc_prg.first) {
                    evaluator.add_inplace(calc_prg.second, top.second);
                    calc_prg.first *= 2;
                    t_stack_in.pop();
                } else if(top.first == calc_prg.first * -1) {
                    evaluator.sub_inplace(calc_prg.second, top.second);
                    calc_prg.first *= 2;
                    t_stack_in.pop();
                } else break;
            }
            t_stack_in.push(calc_prg);
        }
        auto pm = t_stack_in.top().first;
        Ciphertext esti_cipher = t_stack_in.top().second;
        t_stack_in.pop();
        while(!t_stack_in.empty()) {
            if(t_stack_in.top().first * pm > 0) {
                evaluator.add_inplace(esti_cipher, t_stack_in.top().second);
            } else {
                evaluator.sub_inplace(esti_cipher, t_stack_in.top().second);
            }
            t_stack_in.pop();
        }
        std::vector<uint64_t> mask(batch_encoder.slot_count(), 0ull);
        mask[now_round] = (pm > 0) ? 1 : parms.plain_modulus().value() - 1;
        Plaintext mask_plain;
        batch_encoder.encode(mask, mask_plain);
        evaluator.multiply_plain_inplace(esti_cipher, mask_plain);

        // save and send
        auto esti_val = std::make_pair(1, esti_cipher);
        while(!t_stack_out.empty()) {
            auto top = t_stack_out.top();
            if(top.first == esti_val.first) {
                evaluator.add_inplace(esti_val.second, top.second);
                esti_val.first <<=1;
                t_stack_out.pop();
            } else break;
        }
        t_stack_out.push(esti_val);
    }
    std::cerr<<"[Party " << party << "] prepare to send" << std::endl;
    Ciphertext esti_cipher = t_stack_out.top().second;
    t_stack_out.pop();
    while(!t_stack_out.empty()) {
        evaluator.add_inplace(esti_cipher, t_stack_out.top().second);
        t_stack_out.pop();
    }
    iosend(party, io, esti_cipher);
    iorecv(party, io, context, esti_cipher);
    Plaintext esti_plain;
    decryptor.decrypt(esti_cipher, esti_plain);
    std::vector<uint64_t> esti_vec;
    batch_encoder.decode(esti_plain, esti_vec);
    std::cerr<<"[Party " << party << "] estimation values: ";
    for(int now_round = 0; now_round < get_config().mom_tt; ++now_round) std::cerr << esti_vec[now_round] << " ";
    std::cerr << std::endl;

    if(party == 1) {
        for(int now_round = 0; now_round < tot_rounds; ++now_round) {
            io->send_data(&esti_vec[now_round], sizeof(uint64_t));
        }
        io->flush();
        return -1; 
    } else {
        std::vector<int64_t> mom;
        for(int tt = 0, i=0; tt < get_config().mom_tt; ++tt) {
            int64_t tmp_tot = 0, esti_1, esti_2;
            for(int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
                uint64_t esti_val;
                io->recv_data(&esti_val, sizeof(esti_val));
                esti_1 = (esti_vec[i] <= parms.plain_modulus().value() / 2 ? esti_vec[i] : esti_vec[i] - parms.plain_modulus().value());
                esti_2 = (esti_val <= parms.plain_modulus().value() / 2 ? esti_val : esti_val - parms.plain_modulus().value());
                tmp_tot += esti_1 * esti_2;
            }
            mom.push_back(tmp_tot / get_config().mom_kk);
        }
        sort(mom.begin(), mom.end());
        for(const auto& val : mom) {
            std::cerr << val << " ";
        }
        std::cerr << std::endl;
        return mom[mom.size()/2];
    }
}*/

int psi_ca_prg_nondeter_He_simd_mpc(int party, const std::vector<int>& input_set, emp::NetIO* io) {
    int tot_rounds = get_config().mom_tt * get_config().mom_kk;
    ASSERT_MSG(tot_rounds <= get_config().seal_degree, "one batch is not enough for #rounds");
    EncryptionParameters parms(scheme_type::bfv);

    // set parameters
    size_t poly_modulus_degree = get_config().seal_degree;
    parms.set_poly_modulus_degree(poly_modulus_degree);
    parms.set_coeff_modulus(CoeffModulus::BFVDefault(poly_modulus_degree));
    parms.set_plain_modulus(PlainModulus::Batching(poly_modulus_degree, get_config().seal_plain_modulus));

    SEALContext context(parms);
    print_parameters(context); 

    // generate keys
    KeyGenerator keygen(context);
    SecretKey secret_key = keygen.secret_key();
    PublicKey public_key_1;
    keygen.create_public_key(public_key_1);
    RelinKeys relin_key_1, relin_key_2;
    keygen.create_relin_keys(relin_key_1);

    BatchEncoder batch_encoder(context);
    Encryptor encryptor(context, public_key_1);
    Evaluator evaluator(context);
    Decryptor decryptor(context, secret_key);

    // generate and encrypt seeds
    std::mt19937_64 rnd(std::chrono::system_clock::now().time_since_epoch().count());
    // AESGen gen_seed(std::uniform_int_distribution<uint64_t>(0, UINT64_MAX)(rnd));
    AESGen gen_seed(19920929*party);
    std::vector<std::vector<uint64_t>> batch_seed(get_config().seed_size, std::vector<uint64_t>(batch_encoder.slot_count(), 0ull));

    for(int now_round = 0; now_round < tot_rounds; ++now_round) {
        std::vector<bool> seed = gen_seed.get_bits(now_round, get_config().seed_size);
        for(int i = 0; i < get_config().seed_size; ++i) {
            batch_seed[i][now_round] = seed[i] ? parms.plain_modulus().value() - 1 : 1;
        }
    }

    std::vector<Plaintext> plain_seed(get_config().seed_size);
    std::vector<Ciphertext> encrypted_seed_1(get_config().seed_size);
    for(int i = 0; i < get_config().seed_size; ++i) {
        batch_encoder.encode(batch_seed[i], plain_seed[i]);
        encryptor.encrypt(plain_seed[i], encrypted_seed_1[i]);
    }

    // send relin key and seeds
    std::vector<Ciphertext> encrypted_seed_2(get_config().seed_size);
    if(party == 1) {
        iosend(party, io, relin_key_1);
        for(int i = 0; i < get_config().seed_size; ++i) iosend(party, io, encrypted_seed_1[i]);
        iorecv(party, io, context, relin_key_2);
        for(int i = 0; i < get_config().seed_size; ++i) iorecv(party, io, context, encrypted_seed_2[i]);
    } else {
        iorecv(party, io, context, relin_key_2);
        for(int i = 0; i < get_config().seed_size; ++i) iorecv(party, io, context, encrypted_seed_2[i]);
        iosend(party, io, relin_key_1);
        for(int i = 0; i < get_config().seed_size; ++i) iosend(party, io, encrypted_seed_1[i]);
    }
    for(int i = 0; i < get_config().seed_size; ++i) {
        evaluator.multiply_plain_inplace(encrypted_seed_2[i], plain_seed[i]);
    }

    // estimate
    std::cerr<<"[Party " << party << "] Starting estimation" << std::endl;
    AESGen aes_gen(0);
    std::unordered_map<uint64_t, Ciphertext> t_map;
    std::stack<std::pair<int, Ciphertext>> t_stack_in;
    std::vector<Ciphertext> calc_prg_2(get_config().prg_dd);
    
    for(auto & item : input_set) {
        std::vector<int> ids = aes_gen.get_id_group(0, item);
        sort(ids.begin(), ids.end());
        for(int i = 0; i < get_config().prg_dd; i+=2) {
            if(i + 1 == get_config().prg_dd) {
                calc_prg_2[i] = encrypted_seed_2[ids[i]];
            } else {
                uint64_t key = ((uint64_t)ids[i]<<32) | ids[i+1];
                if(!t_map.count(key)) {
                    evaluator.multiply(encrypted_seed_2[ids[i]], encrypted_seed_2[ids[i+1]], calc_prg_2[i]);
                    evaluator.relinearize_inplace(calc_prg_2[i], relin_key_2);
                    t_map[key] = calc_prg_2[i];
                } else {
                    calc_prg_2[i] = t_map[key];
                }
            }
        }
        for(int w = 2; w < get_config().prg_dd; w <<= 1) {
            for(int i = 0; i + w < get_config().prg_dd; i += (w<<1)) {
                evaluator.multiply_inplace(calc_prg_2[i], calc_prg_2[i + w]);
                evaluator.relinearize_inplace(calc_prg_2[i], relin_key_2);
            }
        }
        auto calc_prg = std::make_pair(1, calc_prg_2[0]);
        while(!t_stack_in.empty()){
            auto top = t_stack_in.top();
            ASSERT_MSG(abs(top.first) >= abs(calc_prg.first), "stack top should be larger than current");
            if(top.first == calc_prg.first) {
                evaluator.add_inplace(calc_prg.second, top.second);
                calc_prg.first <<= 1;
                t_stack_in.pop();
            } else break;
        }
        t_stack_in.push(calc_prg);
    }
    Ciphertext esti_cipher_1 = t_stack_in.top().second, esti_cipher_2;
    t_stack_in.pop();
    while(!t_stack_in.empty()) {
        evaluator.add_inplace(esti_cipher_1, t_stack_in.top().second);
        t_stack_in.pop();
    }

    // send back (mpc begins)
    std::vector<uint64_t> rnd_1(batch_encoder.slot_count(), 0ull), rnd_2;
    for(int i = 0; i < tot_rounds; ++i) {
        rnd_1[i] = std::uniform_int_distribution<uint64_t>(0, parms.plain_modulus().value() - 1)(rnd);
    }
    Plaintext rnd_1_plain, rnd_2_plain;
    batch_encoder.encode(rnd_1, rnd_1_plain);
    evaluator.sub_plain_inplace(esti_cipher_1, rnd_1_plain);
    if(party == 1) {
        iosend(party, io, esti_cipher_1);
        iorecv(party, io, context, esti_cipher_2);
    } else {
        iorecv(party, io, context, esti_cipher_2);
        iosend(party, io, esti_cipher_1);
    }
    std::cerr<<"[Party " << party << "] final noise budget: " << decryptor.invariant_noise_budget(esti_cipher_2) << std::endl;
    decryptor.decrypt(esti_cipher_2, rnd_2_plain);
    batch_encoder.decode(rnd_2_plain, rnd_2);

    // prepare input sharing
    std::cerr<<"[Party " << party << "] input sharing" << std::endl;
	setup_semi_honest(io, party);
    int mpcbitlen = 42;
    std::vector<std::vector<std::vector<Integer>>> esti_1(2, std::vector<std::vector<Integer>>(get_config().mom_tt, std::vector<Integer>(get_config().mom_kk)));
    std::vector<std::vector<std::vector<Integer>>> esti_2(2, std::vector<std::vector<Integer>>(get_config().mom_tt, std::vector<Integer>(get_config().mom_kk)));
    for(int tt = 0, i = 0; tt < get_config().mom_tt; ++tt) {
        for(int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
            esti_1[party-1][tt][kk] = Integer(mpcbitlen, rnd_1[i], party);
            esti_2[2-party][tt][kk] = Integer(mpcbitlen, rnd_2[i], party);
            esti_1[2-party][tt][kk] = Integer(mpcbitlen, 0, 3-party);
            esti_2[party-1][tt][kk] = Integer(mpcbitlen, 0, 3-party);
        }
    }

    // compute median of means
    std::cerr<<"[Party " << party << "] begin compute" << std::endl;
    Integer modp(mpcbitlen, parms.plain_modulus().value(), PUBLIC), mod23p(mpcbitlen, parms.plain_modulus().value()*2/3, PUBLIC);
	Integer *esti_sum = new Integer[get_config().mom_tt];
    for(int tt = 0; tt < get_config().mom_tt; ++tt) {
        esti_sum[tt] = Integer(mpcbitlen, 0, PUBLIC);
        for(int kk = 0; kk < get_config().mom_kk; ++kk) {
            Integer esti_x = mod_add(esti_1[0][tt][kk], esti_2[0][tt][kk], modp);
            Integer esti_y = mod_add(esti_1[1][tt][kk], esti_2[1][tt][kk], modp);
            esti_sum[tt] = mod_add(esti_sum[tt], mod_mul(esti_x, esti_y, modp), modp);
        }
        Bit over = esti_sum[tt] >= mod23p;
        esti_sum[tt] = If(over, esti_sum[tt] - modp, esti_sum[tt]);
    }
    sort(esti_sum, get_config().mom_tt);
    for(int tt = 0; tt < get_config().mom_tt; ++tt) {
        std::cerr<<"[Party " << party << "] esti_sum[" << tt << "] = " << esti_sum[tt].reveal<uint64_t>(PUBLIC) << std::endl;
    }
    int64_t psi_ca = esti_sum[get_config().mom_tt/2].reveal<uint64_t>(PUBLIC);
    delete[] esti_sum;
    finalize_semi_honest();
    return psi_ca / get_config().mom_kk;
}

/*int psi_ca_prg_nondeter_He_simd(int party, const std::vector<int>& input_set, emp::NetIO* io) {
    int tot_rounds = get_config().mom_tt * get_config().mom_kk;
    ASSERT_MSG(tot_rounds <= get_config().seal_degree, "one batch is not enough for #rounds");
    EncryptionParameters parms(scheme_type::bfv);

    // set parameters
    size_t poly_modulus_degree = get_config().seal_degree;
    parms.set_poly_modulus_degree(poly_modulus_degree);
    parms.set_coeff_modulus(CoeffModulus::BFVDefault(poly_modulus_degree));
    parms.set_plain_modulus(PlainModulus::Batching(poly_modulus_degree, get_config().seal_plain_modulus));

    SEALContext context(parms);
    print_parameters(context); 

    // generate keys
    KeyGenerator keygen(context);
    SecretKey secret_key = keygen.secret_key();
    PublicKey public_key_1, public_key_2;
    keygen.create_public_key(public_key_1);
    RelinKeys relin_key_1, relin_key_2;
    keygen.create_relin_keys(relin_key_1);

    BatchEncoder batch_encoder(context);
    Encryptor encryptor(context, public_key_1);
    Evaluator evaluator(context);
    Decryptor decryptor(context, secret_key);

    // send public key, relin key
    iosend(party, io, public_key_1);
    iosend(party, io, relin_key_1);
    iorecv(party, io, context, public_key_2);
    iorecv(party, io, context, relin_key_2);

    // encrypt and send seeds
    std::mt19937_64 rnd(std::chrono::system_clock::now().time_since_epoch().count());
    AESGen gen_seed(std::uniform_int_distribution<uint64_t>(0, UINT64_MAX)(rnd));
    std::vector<std::vector<uint64_t>> batch_seed(get_config().seed_size, std::vector<uint64_t>(batch_encoder.slot_count(), 0ull));

    for(int now_round = 0; now_round < tot_rounds; ++now_round) {
        std::vector<bool> seed = gen_seed.get_bits(now_round, get_config().seed_size);
        for(int i = 0; i < get_config().seed_size; ++i) {
            batch_seed[i][now_round] = seed[i] ? parms.plain_modulus().value() - 1 : 1;
        }
    }

    std::vector<Plaintext> plain_seed(get_config().seed_size);
    std::vector<Ciphertext> encrypted_seed(get_config().seed_size);
    for(int i = 0; i < get_config().seed_size; ++i) {
        batch_encoder.encode(batch_seed[i], plain_seed[i]);
        encryptor.encrypt(plain_seed[i], encrypted_seed[i]);
        iosend(party, io, encrypted_seed[i]);
        iorecv(party, io, context, encrypted_seed[i]);
        // 一起发
        evaluator.multiply_plain_inplace(encrypted_seed[i], plain_seed[i]);
    }

    // estimate
    auto t_start = std::chrono::high_resolution_clock::now();
    std::cerr<<"[Party " << party << "] Starting estimation" << std::endl;
    AESGen aes_gen(0);
    std::unordered_map<uint64_t, Ciphertext> t_map;
    std::stack<std::pair<int, Ciphertext>> t_stack_in;
    std::vector<Ciphertext> calc_prg_2(get_config().prg_dd);

    for(auto & item : input_set) {
        std::vector<int> ids = aes_gen.get_id_group(0, item);
        sort(ids.begin(), ids.end());
        for(int i = 0; i < get_config().prg_dd; i+=2) {
            if(i + 1 == get_config().prg_dd) {
                calc_prg_2[i] = encrypted_seed[ids[i]];
            } else {
                uint64_t key = ((uint64_t)ids[i]<<32) | ids[i+1];
                if(!t_map.count(key)) {
                    // t_start = std::chrono::high_resolution_clock::now();
                    evaluator.multiply(encrypted_seed[ids[i]], encrypted_seed[ids[i+1]], calc_prg_2[i]);
                    evaluator.relinearize_inplace(calc_prg_2[i], relin_key_2); // 省掉 
                    // std::cout << "time: " << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t_start).count() << " ms" << std::endl;
                    t_map[key] = calc_prg_2[i];
                } else {
                    calc_prg_2[i] = t_map[key];
                }
            }
        }
        for(int w = 2; w < get_config().prg_dd; w <<= 1) {
            for(int i = 0; i + w < get_config().prg_dd; i += (w<<1)) {
                evaluator.multiply_inplace(calc_prg_2[i], calc_prg_2[i + w]);
                evaluator.relinearize_inplace(calc_prg_2[i], relin_key_2);
            }
        }
        auto calc_prg = std::make_pair(1, calc_prg_2[0]);
        while(!t_stack_in.empty()){
            auto top = t_stack_in.top();
            ASSERT_MSG(abs(top.first) >= abs(calc_prg.first), "stack top should be larger than current");
            if(top.first == calc_prg.first) {
                evaluator.add_inplace(calc_prg.second, top.second);
                calc_prg.first <<= 1;
                t_stack_in.pop();
            } else break;
        }
        t_stack_in.push(calc_prg);
    }
    Ciphertext esti_cipher = t_stack_in.top().second;
    t_stack_in.pop();
    while(!t_stack_in.empty()) {
        evaluator.add_inplace(esti_cipher, t_stack_in.top().second);
        t_stack_in.pop();
    }

    // send back
    iosend(party, io, esti_cipher);
    iorecv(party, io, context, esti_cipher);
    Plaintext esti_plain;
    decryptor.decrypt(esti_cipher, esti_plain);
    std::cout << "noise budget: " << decryptor.invariant_noise_budget(esti_cipher) << std::endl;
    std::vector<uint64_t> esti_vec;
    batch_encoder.decode(esti_plain, esti_vec);
    std::cerr<<"[Party " << party << "] estimation values: ";
    for(int now_round = 0; now_round < get_config().mom_tt; ++now_round) std::cerr << esti_vec[now_round] << " ";
    std::cerr << std::endl;

    if(party == 1) {
        for(int now_round = 0; now_round < tot_rounds; ++now_round) {
            io->send_data(&esti_vec[now_round], sizeof(uint64_t));
        }
        io->flush();
        return -1; 
    } else {
        std::vector<int64_t> mom;
        for(int tt = 0, i=0; tt < get_config().mom_tt; ++tt) {
            int64_t tmp_tot = 0, esti_1, esti_2;
            for(int kk = 0; kk < get_config().mom_kk; ++kk, ++i) {
                uint64_t esti_val;
                io->recv_data(&esti_val, sizeof(esti_val));
                esti_1 = (esti_vec[i] <= parms.plain_modulus().value() / 2 ? esti_vec[i] : esti_vec[i] - parms.plain_modulus().value());
                esti_2 = (esti_val <= parms.plain_modulus().value() / 2 ? esti_val : esti_val - parms.plain_modulus().value());
                tmp_tot += esti_1 * esti_2;
            }
            mom.push_back(tmp_tot / get_config().mom_kk);
        }
        sort(mom.begin(), mom.end());
        for(const auto& val : mom) {
            std::cerr << val << " ";
        }
        std::cerr << std::endl;
        return mom[mom.size()/2];
    }
}*/

// -----------------------------------------------------------

struct _AutoRegister {
    _AutoRegister() {
        register_psi_method("naive", psi_ca_naive);
        register_psi_method("prg_nondeter_naive", psi_ca_prg_nondeter_naive);
        // register_psi_method("prg_nondeter_He", psi_ca_prg_nondeter_He);
        // register_psi_method("prg_nondeter_He_simd", psi_ca_prg_nondeter_He_simd);
        register_psi_method("prg_nondeter_He_simd_mpc", psi_ca_prg_nondeter_He_simd_mpc);
    }
} _auto_register;
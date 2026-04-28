#ifndef PRIMUS_OLE_U32_MODP_H
#define PRIMUS_OLE_U32_MODP_H

#include "emp-ot/emp-ot.h"
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>

using std::vector;

/*
 * OLE over Z_p with uint32_t storage.
 *
 * This follows the same structure as backend/ole.h:
 * - sender/receiver side is selected explicitly by the caller
 * - bit decomposition of the receiver input
 * - sender and receiver outputs add up to a*b mod p
 *
 * Requirements:
 * - 0 < p < 2^32
 * - bit_length should cover all possible inputs, e.g. 24 in your case
 * - all inputs must already be reduced mod p
 */

template <typename IO>
class OLE_U32_MODP {
public:
    IO* io;
    COT<IO>* ot;
    bool is_sender;
    CCRH ccrh;
    uint32_t p;
    size_t bit_length;
    vector<uint32_t> exp2_mod_p;

    OLE_U32_MODP(IO* io, COT<IO>* ot, uint32_t modulus, size_t bitlen, bool sender_role)
        : io(io), ot(ot), is_sender(sender_role), p(modulus), bit_length(bitlen) {
        assert(io != nullptr);
        assert(ot != nullptr);
        assert(p > 0);
        assert(bit_length > 0 && bit_length <= 32);
        exp2_mod_p.resize(bit_length);
        uint64_t cur = 1 % p;
        for (size_t i = 0; i < bit_length; ++i) {
            exp2_mod_p[i] = (uint32_t)cur;
            cur = (cur * 2) % p;
        }
    }

    ~OLE_U32_MODP() = default;

    static inline uint32_t add_mod_u32(uint32_t a, uint32_t b, uint32_t p) {
        uint64_t s = (uint64_t)a + (uint64_t)b;
        s %= p;
        return (uint32_t)s;
    }

    static inline uint32_t sub_mod_u32(uint32_t a, uint32_t b, uint32_t p) {
        return (a >= b) ? (a - b) : (uint32_t)(a + p - b);
    }

    static inline uint32_t mul_mod_u32(uint32_t a, uint32_t b, uint32_t p) {
        uint64_t z = (uint64_t)a * (uint64_t)b;
        z %= p;
        return (uint32_t)z;
    }

    static inline void send_u32(IO* io, uint32_t x) {
        io->send_data(&x, sizeof(uint32_t));
    }

    static inline void recv_u32(IO* io, uint32_t& x) {
        io->recv_data(&x, sizeof(uint32_t));
    }

    inline uint32_t H_u32_modp(const block& in) {
        block out = ccrh.H(in);

        alignas(16) uint8_t buf[16];
        _mm_store_si128((__m128i*)buf, out);

        uint64_t x = 0;
        std::memcpy(&x, buf, sizeof(uint64_t));
        return (uint32_t)(x % p);
    }

    /*
     * out[k] becomes this party's additive share of:
     *   in_sender[k] * in_receiver[k] mod p
     *
     * Exactly like the original OLE::compute, but with uint32_t.
     */
    void compute(vector<uint32_t>& out, const vector<uint32_t>& in) {
        assert(out.size() == in.size());
        assert(io != nullptr);
        assert(ot != nullptr);

        const size_t n = out.size();
        block* raw = new block[n * bit_length];

#ifndef NDEBUG
        for (size_t i = 0; i < n; ++i) {
            assert(in[i] < p);
            if (bit_length < 32) {
                assert((in[i] >> bit_length) == 0u);
            }
        }
#endif

        if (is_sender) {
            // Sender side
            ot->send_cot(raw, n * bit_length);

            for (size_t i = 0; i < n; ++i) {
                uint32_t acc = 0;

                for (size_t j = 0; j < bit_length; ++j) {
                    const block r0 = raw[i * bit_length + j];
                    const block r1 = r0 ^ ot->Delta;

                    const uint32_t pad1 = H_u32_modp(r0);
                    const uint32_t pad2 = H_u32_modp(r1);

                    // msg = pad1 + pad2 + in[i] mod p
                    uint32_t msg = add_mod_u32(add_mod_u32(pad1, pad2, p), in[i], p);

                    // out += 2^j * (p - pad1) mod p
                    uint32_t neg_pad1 = (pad1 == 0 ? 0 : (p - pad1));
                    uint32_t term = mul_mod_u32(exp2_mod_p[j], neg_pad1, p);
                    acc = add_mod_u32(acc, term, p);

                    send_u32(io, msg);
                }

                out[i] = acc;
            }
            io->flush();

        } else {
            // Receiver side
            bool* bits = new bool[n * bit_length];

            for (size_t i = 0; i < n; ++i) {
                for (size_t j = 0; j < bit_length; ++j) {
                    bits[i * bit_length + j] = ((in[i] >> j) & 1u) != 0;
                }
            }

            ot->recv_cot(raw, bits, n * bit_length);

            for (size_t i = 0; i < n; ++i) {
                uint32_t acc = 0;

                for (size_t j = 0; j < bit_length; ++j) {
                    uint32_t tmp = 0;
                    recv_u32(io, tmp);

                    uint32_t msg = H_u32_modp(raw[i * bit_length + j]);

                    // if bit == 1: msg = tmp - msg mod p
                    if (bits[i * bit_length + j]) {
                        msg = sub_mod_u32(tmp, msg, p);
                    }

                    uint32_t term = mul_mod_u32(exp2_mod_p[j], msg, p);
                    acc = add_mod_u32(acc, term, p);
                }

                out[i] = acc;
            }

            delete[] bits;
        }

        delete[] raw;
    }
};

#endif

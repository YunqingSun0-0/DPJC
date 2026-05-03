#pragma once
#include "check.h"
#include "config.h"
#include <emp-tool/emp-tool.h>
#include <emp-tool/utils/aes.h>
#include <emp-tool/utils/block.h>
#include <vector>
#include <cassert>
#include <cstring> 

using namespace emp;

class AESGen {
private:
	AES_KEY aes;

public:
    AESGen(uint64_t id = 0) {
        block key = makeBlock(id, get_config().prg_seed);
        AES_set_encrypt_key(key, &aes);
        ASSERT_MSG(get_config().prg_dd * get_config().seed_size_bit <= 100, "cannot gen one index in one block"); 
    }

    uint64_t calc_counter(uint64_t round, uint64_t index) const {
        return round * get_config().universal_set_size * 2 + index;
    }

    void get_block(block *data, uint64_t counter, int nblocks=1) const {
		block tmp[AES_BATCH_SIZE];
        for(int i = 0; i < nblocks/AES_BATCH_SIZE; ++i) {
			for (int j = 0; j < AES_BATCH_SIZE; ++j)
				tmp[j] = makeBlock(0LL, counter++);
			AES_ecb_encrypt_blks<AES_BATCH_SIZE>(tmp, &aes);
			memcpy(data + i*AES_BATCH_SIZE, tmp, AES_BATCH_SIZE*sizeof(block));
		}
		int remain = nblocks % AES_BATCH_SIZE;
		for (int j = 0; j < remain; ++j)
			tmp[j] = makeBlock(0LL, counter++);
		AES_ecb_encrypt_blks(tmp, remain, &aes);
		memcpy(data + (nblocks/AES_BATCH_SIZE)*AES_BATCH_SIZE, tmp, remain*sizeof(block));
    }

    // generate seed (b bits)
    std::vector<bool> get_bits(uint64_t round, int n) const {
        std::vector<bool> res;
        int total_blocks = (n + 127) / 128;

        std::vector<block> data(total_blocks);
        get_block(data.data(), calc_counter(round, get_config().universal_set_size), total_blocks);

        for(int i = 0; i< total_blocks; ++i) {
            uint8_t* buf = (uint8_t*)&data[i];
            for(int j = 0; j < 16; ++j) {
                for(int k = 0; k < 8 && res.size() < n; ++k) {
                    res.push_back((buf[j] >> k) & 1);
                }
            }
        }
        res.resize(n);
        return res;
    }

    // get indexes
    std::vector<int> get_id_group(uint64_t round, int index) const {
        std::vector<int> res;
        block bl;
        get_block(&bl, calc_counter(round, index), 1);
        uint8_t* buf = (uint8_t*)&bl;
        for(int si = 0, bi = 0, bj = 0, repeat_fl; si < get_config().prg_dd; ++si) {
            int id = 0;
            for(int sj = 0; sj < get_config().seed_size_bit; ++sj) {
                id = (id << 1) | ((buf[bi] >> bj) & 1);
                if (++bj == 8) {
                    bj = 0;
                    ++bi;
                }
                ASSERT_MSG(bi < 16, "cannot gen one index in one block");
            }
            repeat_fl = 0;
            for(auto & x : res) if(x == id) {
                --si;
                repeat_fl = 1;
                break;
            }
            if(!repeat_fl) res.push_back(id);
        }
        return res;
    }
};

// refresh-marker: 20260503T033330Z

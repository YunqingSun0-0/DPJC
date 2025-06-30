#pragma once
#include "config.h"
#include "check.h"
#include <vector>
#include <string>
#include <random>
#include <sstream>
#include <algorithm>

class InputProvider {
public:
    virtual std::vector<int> get_input_set() = 0;
    virtual ~InputProvider() = default;
};

class RandomInputProvider : public InputProvider {
public:
    int party;
    RandomInputProvider(int party) : party(party) {}

    std::vector<int> get_input_set() override {
        const GlobalConfig& config = get_config();
        std::mt19937 rng(config.input_seed + party);
        std::uniform_int_distribution<int> dist(0, config.universal_set_size - 1);
    
        std::vector<int> input_set;
        for (int i = 0; i < config.universal_set_size / 2; ++i) {
            input_set.push_back(dist(rng));
        }

        sort(input_set.begin(), input_set.end());
        input_set.erase(std::unique(input_set.begin(), input_set.end()), input_set.end());
        shuffle(input_set.begin(), input_set.end(), rng);
        
        ASSERT_MSG(!input_set.empty(), "RandomInputProvider generated an empty input set. This should not happen.");
        return input_set;
    }
};

InputProvider* create_input_provider(int party_id) {
    return new RandomInputProvider(party_id);
}

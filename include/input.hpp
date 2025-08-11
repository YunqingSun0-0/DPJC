#pragma once
#include <vector>
#include <string>
#include <utility>
#include <iostream>
#include <fstream>

std::vector<int> read_set_from_file(const std::string& filename) {
    std::vector<int> set;
    std::ifstream file(filename);
    
    if (!file.is_open()) {
        std::cerr << "Error: Cannot open file " << filename << std::endl;
        return set;
    }
    
    int element;
    while (file >> element) {
        set.push_back(element);
    }
    
    file.close();
    return set;
}

class FileInputProvider {
private:
    std::vector<int> input_set;
    
public:
    FileInputProvider(const std::string& filename) {
        input_set = read_set_from_file(filename);
    }
    std::vector<int> get_input_set() const {
        return input_set;
    }
};

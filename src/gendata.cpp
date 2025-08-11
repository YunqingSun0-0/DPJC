#include "config.h"
#include <iostream>
#include <fstream>
#include <vector>
#include <set>
#include <random>
#include <algorithm>
#include <string>
#include <cstring>
#include <filesystem>

// 生成两个集合，指定交集大小
std::pair<std::vector<int>, std::vector<int>> generate_sets_with_intersection(
    int universal_size, int intersection_size, int set_size) {
    
    std::mt19937 rng(std::chrono::system_clock::now().time_since_epoch().count());
    
    // 生成交集元素
    std::vector<int> intersection;
    std::set<int> used_elements;
    
    for (int i = 0; i < intersection_size; ++i) {
        int element;
        do {
            element = std::uniform_int_distribution<int>(0, universal_size - 1)(rng);
        } while (used_elements.count(element));
        
        intersection.push_back(element);
        used_elements.insert(element);
    }
    
    // 生成集合1的剩余元素
    std::vector<int> set1 = intersection;
    for (int i = intersection_size; i < set_size; ++i) {
        int element;
        do {
            element = std::uniform_int_distribution<int>(0, universal_size - 1)(rng);
        } while (used_elements.count(element));
        
        set1.push_back(element);
        used_elements.insert(element);
    }
    
    // 生成集合2的剩余元素
    std::vector<int> set2 = intersection;
    for (int i = intersection_size; i < set_size; ++i) {
        int element;
        do {
            element = std::uniform_int_distribution<int>(0, universal_size - 1)(rng);
        } while (used_elements.count(element));
        
        set2.push_back(element);
        used_elements.insert(element);
    }
    
    // 随机打乱集合
    std::shuffle(set1.begin(), set1.end(), rng);
    std::shuffle(set2.begin(), set2.end(), rng);
    
    return {set1, set2};
}

// 将集合均分给clients
std::vector<std::vector<int>> distribute_set_to_clients(
    const std::vector<int>& set, int num_clients_per_server) {
    
    std::vector<std::vector<int>> client_sets(num_clients_per_server);
    
    for (size_t i = 0; i < set.size(); ++i) {
        int client_id = i % num_clients_per_server;
        client_sets[client_id].push_back(set[i]);
    }
    
    return client_sets;
}

// 将数据写入文件
void write_set_to_file(const std::vector<int>& set, const std::string& filename) {
    std::ofstream file(filename);
    if (!file.is_open()) {
        std::cerr << "Error: Cannot open file " << filename << std::endl;
        return;
    }
    
    for (int element : set) {
        file << element << std::endl;
    }
    
    file.close();
    std::cout << "Written " << set.size() << " elements to " << filename << std::endl;
}

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n"
              << "Options:\n"
              << "  --intersection_size=<n>     Size of intersection (default: 100)\n"
              << "  --universal_size_bit=<n>    Universal set size as 2^n (default: 12)\n"
              << "  --num_clients_per_server=<n> Number of clients per server (default: 1)\n"
              << "  --set_size=<n>              Size of each set (default: 1000)\n"
              << "  --output_dir=<dir>          Output directory for files (default: ./data)\n"
              << "  --help                      Show this help message\n";
}

int main(int argc, char** argv) {
    int intersection_size = 200;
    int universal_size_bit = 12;
    int num_clients_per_server = 1;
    int set_size = 600;
    std::string output_dir = "./data";
    
    // 解析命令行参数
    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        
        if (arg == "--help") {
            print_usage(argv[0]);
            return 0;
        }
        
        auto get_value = [&](const std::string& prefix) -> std::string {
            if (arg.substr(0, prefix.size()) == prefix) {
                return arg.substr(prefix.size());
            }
            return "";
        };
        
        if (auto val = get_value("--intersection_size="); !val.empty()) {
            intersection_size = std::stoi(val);
        } else if (auto val = get_value("--universal_size_bit="); !val.empty()) {
            universal_size_bit = std::stoi(val);
        } else if (auto val = get_value("--num_clients_per_server="); !val.empty()) {
            num_clients_per_server = std::stoi(val);
        } else if (auto val = get_value("--set_size="); !val.empty()) {
            set_size = std::stoi(val);
        } else if (auto val = get_value("--output_dir="); !val.empty()) {
            output_dir = val;
        } else {
            std::cerr << "Unknown argument: " << arg << std::endl;
            print_usage(argv[0]);
            return 1;
        }
    }
    
    // 验证参数
    int universal_size = 1 << universal_size_bit;
    if (intersection_size > set_size) {
        std::cerr << "Error: intersection_size cannot be larger than set_size" << std::endl;
        return 1;
    }
    if (set_size > universal_size) {
        std::cerr << "Error: set_size cannot be larger than universal_size" << std::endl;
        return 1;
    }
    
    std::cout << "Generating data with parameters:\n"
              << "  Universal size: 2^" << universal_size_bit << " = " << universal_size << "\n"
              << "  Set size: " << set_size << "\n"
              << "  Intersection size: " << intersection_size << "\n"
              << "  Clients per server: " << num_clients_per_server << "\n"
              << "  Output directory: " << output_dir << "\n\n";
    
    // 创建输出目录
    std::filesystem::create_directories(output_dir);
    
    // 生成两个集合
    auto [set1, set2] = generate_sets_with_intersection(universal_size, intersection_size, set_size);
    
    std::cout << "Generated sets:\n"
              << "  Set 1 size: " << set1.size() << "\n"
              << "  Set 2 size: " << set2.size() << "\n";
    
    // 验证交集大小
    std::set<int> set1_set(set1.begin(), set1.end());
    std::set<int> set2_set(set2.begin(), set2.end());
    std::vector<int> actual_intersection;
    std::set_intersection(set1_set.begin(), set1_set.end(),
                         set2_set.begin(), set2_set.end(),
                         std::back_inserter(actual_intersection));
    
    std::cout << "  Actual intersection size: " << actual_intersection.size() << "\n\n";
    
    // 将集合分配给clients
    auto client_sets_1 = distribute_set_to_clients(set1, num_clients_per_server);
    auto client_sets_2 = distribute_set_to_clients(set2, num_clients_per_server);
    
    // 写入文件模式
    std::cout << "Writing data to files:\n";
    
    // Server 1的clients
    for (int i = 0; i < num_clients_per_server; ++i) {
        std::string filename = output_dir + "/client" + std::to_string(1 + i) + "_1.txt";
        write_set_to_file(client_sets_1[i], filename);
    }
    
    // Server 2的clients
    for (int i = 0; i < num_clients_per_server; ++i) {
        std::string filename = output_dir + "/client" + std::to_string(1 + i) + "_2.txt";
        write_set_to_file(client_sets_2[i], filename);
    }
    
    // 写入配置文件
    std::ofstream config_file(output_dir + "/config.txt");
    config_file << "intersection_size=" << intersection_size << "\n";
    config_file << "universal_size_bit=" << universal_size_bit << "\n";
    config_file << "num_clients_per_server=" << num_clients_per_server << "\n";
    config_file << "set_size=" << set_size << "\n";
    config_file << "actual_intersection_size=" << actual_intersection.size() << "\n";
    config_file.close();
    
    std::cout << "Configuration written to " << output_dir << "/config.txt\n";
    
    std::cout << "\nData generation completed successfully!\n";
    return 0;
}

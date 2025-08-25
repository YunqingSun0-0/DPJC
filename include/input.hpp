#pragma once
#include <vector>
#include <string>
#include <utility>
#include <iostream>
#include <fstream>
#include <random>
#include <set>
#include <algorithm>
#include <unordered_map>
#include <mutex>

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

// 新增：全局数据管理器
class GlobalDataManager {
private:
    static GlobalDataManager* instance;
    static std::mutex instance_mutex;
    
    std::unordered_map<std::string, std::vector<int>> client_data_cache;
    std::mutex cache_mutex;
    std::mt19937 rng;
    bool data_generated = false;
    
    // 配置参数
    int universal_size = 1 << 20;
    int set_size = 10000;
    int intersection_size = 5000;
    int num_clients_per_server = 2;
    
    GlobalDataManager() : rng(std::chrono::system_clock::now().time_since_epoch().count()) {}
    
    std::string make_key(int client_id, int server_id) {
        return std::to_string(server_id) + "_" + std::to_string(client_id);
    }
    
    void generate_all_data() {
        if (data_generated) return;
        
        std::lock_guard<std::mutex> lock(cache_mutex);
        if (data_generated) return; // Double-check
        
        std::cout << "[DataManager] Generating test data..." << std::endl;
        
        // 生成交集元素（只有数据管理器知道，client不知道）
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
        
        // 为Server 1的每个client生成独立数据
        for (int client = 0; client < num_clients_per_server; ++client) {
            std::vector<int> client_set;
            std::set<int> client_used;
            
            // 每个client包含部分交集元素
            int intersection_per_client = intersection_size / num_clients_per_server;
            int start_idx = client * intersection_per_client;
            int end_idx = (client == num_clients_per_server - 1) ? intersection_size : (client + 1) * intersection_per_client;
            
            for (int i = start_idx; i < end_idx; ++i) {
                client_set.push_back(intersection[i]);
                client_used.insert(intersection[i]);
            }
            
            // 添加随机元素到指定大小
            while (client_set.size() < set_size / num_clients_per_server) {
                int element;
                do {
                    element = std::uniform_int_distribution<int>(0, universal_size - 1)(rng);
                } while (client_used.count(element) || used_elements.count(element));
                
                client_set.push_back(element);
                client_used.insert(element);
                used_elements.insert(element);
            }
            
            std::shuffle(client_set.begin(), client_set.end(), rng);
            client_data_cache[make_key(client + 1, 1)] = client_set;
        }
        
        // 为Server 2的每个client生成独立数据
        for (int client = 0; client < num_clients_per_server; ++client) {
            std::vector<int> client_set;
            std::set<int> client_used;
            
            // 每个client包含部分交集元素
            int intersection_per_client = intersection_size / num_clients_per_server;
            int start_idx = client * intersection_per_client;
            int end_idx = (client == num_clients_per_server - 1) ? intersection_size : (client + 1) * intersection_per_client;
            
            for (int i = start_idx; i < end_idx; ++i) {
                client_set.push_back(intersection[i]);
                client_used.insert(intersection[i]);
            }
            
            // 添加随机元素到指定大小
            while (client_set.size() < set_size / num_clients_per_server) {
                int element;
                do {
                    element = std::uniform_int_distribution<int>(0, universal_size - 1)(rng);
                } while (client_used.count(element) || used_elements.count(element));
                
                client_set.push_back(element);
                client_used.insert(element);
                used_elements.insert(element);
            }
            
            std::shuffle(client_set.begin(), client_set.end(), rng);
            client_data_cache[make_key(client + 1, 2)] = client_set;
        }
        
        data_generated = true;
        std::cout << "[DataManager] Test data generation completed" << std::endl;
    }
    
public:
    static GlobalDataManager* get_instance() {
        std::lock_guard<std::mutex> lock(instance_mutex);
        if (instance == nullptr) {
            instance = new GlobalDataManager();
        }
        return instance;
    }
    
    // 配置数据生成参数
    void configure(int universal_size_bit, int set_size, int intersection_size, int num_clients_per_server) {
        this->universal_size = 1 << universal_size_bit;
        this->set_size = set_size;
        this->intersection_size = intersection_size;
        this->num_clients_per_server = num_clients_per_server;
    }
    
    // 获取client数据
    std::vector<int> get_client_data(int client_id, int server_id) {
        // 确保数据已生成
        generate_all_data();
        
        std::lock_guard<std::mutex> lock(cache_mutex);
        std::string key = make_key(client_id, server_id);
        
        auto it = client_data_cache.find(key);
        if (it != client_data_cache.end()) {
            return it->second;
        }
        
        // 如果找不到数据，返回空向量
        std::cerr << "[DataManager] Warning: No data found for client " << client_id << " server " << server_id << std::endl;
        return std::vector<int>();
    }
    
    // 获取预期交集大小（用于验证）
    int get_expected_intersection_size() {
        generate_all_data();
        return intersection_size;
    }
    
    // 清理缓存
    void clear_cache() {
        std::lock_guard<std::mutex> lock(cache_mutex);
        client_data_cache.clear();
        data_generated = false;
    }
};

// 静态成员初始化
GlobalDataManager* GlobalDataManager::instance = nullptr;
std::mutex GlobalDataManager::instance_mutex;

// 新增：Client数据提供者
class ClientDataProvider {
private:
    std::vector<int> input_set;
    
public:
    ClientDataProvider(int client_id, int server_id) {
        GlobalDataManager* manager = GlobalDataManager::get_instance();
        input_set = manager->get_client_data(client_id, server_id);
    }
    
    std::vector<int> get_input_set() const {
        return input_set;
    }
};

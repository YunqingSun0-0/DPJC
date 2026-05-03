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
#include <sstream>
#include <stdexcept>
#include "config.h"
#include "psi.h"

std::vector<WeightedInput> read_set_from_file(const std::string& filename) {
    std::vector<WeightedInput> set;
    std::ifstream file(filename);
    
    if (!file.is_open()) {
        std::cerr << "Error: Cannot open file " << filename << std::endl;
        return set;
    }
    
    std::string line;
    int line_number = 0;
    const int max_value = get_config().universal_set_size;
    while (std::getline(file, line)) {
        ++line_number;
        if (line.empty()) {
            continue;
        }

        std::istringstream iss(line);
        WeightedInput item;
        if (!(iss >> item.value)) {
            continue;
        }
        if (!(iss >> item.weight)) {
            item.weight = 1;
        }
        if (item.value < 0 || item.value >= max_value) {
            throw std::runtime_error(
                "Input value out of range in file " + filename +
                " at line " + std::to_string(line_number) +
                ": value=" + std::to_string(item.value) +
                ", expected 0 <= value < " + std::to_string(max_value) +
                " (set by --universal_set_size_bit=" + std::to_string(get_config().universal_set_size_bit) + ")"
            );
        }
        set.push_back(item);
    }
    
    file.close();
    return set;
}

class FileInputProvider {
private:
    std::vector<WeightedInput> input_set;
    
public:
    FileInputProvider(const std::string& filename) {
        input_set = read_set_from_file(filename);
    }
    std::vector<WeightedInput> get_input_set() const {
        return input_set;
    }
};

// Added: Global data manager
class GlobalDataManager {
private:
    static GlobalDataManager* instance;
    static std::mutex instance_mutex;
    
    std::unordered_map<std::string, std::vector<WeightedInput>> client_data_cache;
    std::mutex cache_mutex;
    std::mt19937 rng;
    bool data_generated = false;
    
    // Configuration parameters
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
        
        // Generate intersection elements (known only to the data manager, not clients)
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
        
        // Generate independent data for each Server 1 client
        for (int client = 0; client < num_clients_per_server; ++client) {
            std::vector<WeightedInput> client_set;
            std::set<int> client_used;
            
            // Each client contains part of the intersection elements
            int intersection_per_client = intersection_size / num_clients_per_server;
            int start_idx = client * intersection_per_client;
            int end_idx = (client == num_clients_per_server - 1) ? intersection_size : (client + 1) * intersection_per_client;
            
            for (int i = start_idx; i < end_idx; ++i) {
                client_set.push_back({intersection[i], 1});
                client_used.insert(intersection[i]);
            }
            
            // Add random elements until reaching the target size
            while (client_set.size() < set_size / num_clients_per_server) {
                int element;
                do {
                    element = std::uniform_int_distribution<int>(0, universal_size - 1)(rng);
                } while (client_used.count(element) || used_elements.count(element));
                
                client_set.push_back({element, 1});
                client_used.insert(element);
                used_elements.insert(element);
            }
            
            std::shuffle(client_set.begin(), client_set.end(), rng);
            client_data_cache[make_key(client + 1, 1)] = client_set;
        }
        
        // Generate independent data for each Server 2 client
        for (int client = 0; client < num_clients_per_server; ++client) {
            std::vector<WeightedInput> client_set;
            std::set<int> client_used;
            
            // Each client contains part of the intersection elements
            int intersection_per_client = intersection_size / num_clients_per_server;
            int start_idx = client * intersection_per_client;
            int end_idx = (client == num_clients_per_server - 1) ? intersection_size : (client + 1) * intersection_per_client;
            
            for (int i = start_idx; i < end_idx; ++i) {
                client_set.push_back({intersection[i], 1});
                client_used.insert(intersection[i]);
            }
            
            // Add random elements until reaching the target size
            while (client_set.size() < set_size / num_clients_per_server) {
                int element;
                do {
                    element = std::uniform_int_distribution<int>(0, universal_size - 1)(rng);
                } while (client_used.count(element) || used_elements.count(element));
                
                client_set.push_back({element, 1});
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
    
    // Configure data generation parameters
    void configure(int universal_size_bit, int set_size, int intersection_size, int num_clients_per_server) {
        this->universal_size = 1 << universal_size_bit;
        this->set_size = set_size;
        this->intersection_size = intersection_size;
        this->num_clients_per_server = num_clients_per_server;
    }
    
    // Get client data
    std::vector<WeightedInput> get_client_data(int client_id, int server_id) {
        // Ensure data has been generated
        generate_all_data();
        
        std::lock_guard<std::mutex> lock(cache_mutex);
        std::string key = make_key(client_id, server_id);
        
        auto it = client_data_cache.find(key);
        if (it != client_data_cache.end()) {
            return it->second;
        }
        
        // If no data is found, return an empty vector
        std::cerr << "[DataManager] Warning: No data found for client " << client_id << " server " << server_id << std::endl;
        return std::vector<WeightedInput>();
    }
    
    // Get expected intersection size (for verification)
    int get_expected_intersection_size() {
        generate_all_data();
        return intersection_size;
    }
    
    // Clear cache
    void clear_cache() {
        std::lock_guard<std::mutex> lock(cache_mutex);
        client_data_cache.clear();
        data_generated = false;
    }
};

// Static member initialization
GlobalDataManager* GlobalDataManager::instance = nullptr;
std::mutex GlobalDataManager::instance_mutex;

// Added: Client data provider
class ClientDataProvider {
private:
    std::vector<WeightedInput> input_set;
    
public:
    ClientDataProvider(int client_id, int server_id) {
        GlobalDataManager* manager = GlobalDataManager::get_instance();
        input_set = manager->get_client_data(client_id, server_id);
    }
    
    std::vector<WeightedInput> get_input_set() const {
        return input_set;
    }
};

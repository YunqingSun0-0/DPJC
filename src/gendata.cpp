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
#include <cstdint>
#include <numeric>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>
#include <chrono>
#include <thread>
#include <stdexcept>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

struct WeightedWord {
    int value = 0;
    int64_t weight = 1;
};

struct UciDataset {
    int num_docs = 0;
    int vocab_size = 0;
    int nnz = 0;
    std::vector<std::vector<WeightedWord>> docs;
};

std::pair<std::vector<int>, std::vector<int>> generate_sets_with_intersection(
    int universal_size, int intersection_size, int set_size, std::mt19937& rng) {
    
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

template <typename T>
std::vector<std::vector<T>> distribute_set_to_clients(
    const std::vector<T>& set, int num_clients_per_server) {

    std::vector<std::vector<T>> client_sets(num_clients_per_server);

    for (size_t i = 0; i < set.size(); ++i) {
        int client_id = static_cast<int>(i % num_clients_per_server);
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

void write_weighted_set_to_file(const std::vector<WeightedWord>& set, const std::string& filename) {
    std::ofstream file(filename);
    if (!file.is_open()) {
        std::cerr << "Error: Cannot open file " << filename << std::endl;
        return;
    }

    for (const auto& element : set) {
        file << element.value << " " << element.weight << std::endl;
    }

    std::cout << "Written " << set.size() << " weighted elements to " << filename << std::endl;
}

// 读取 UCI 原始数据
UciDataset load_uci_dataset(const std::string& filename) {
    std::ifstream file(filename);
    if (!file.is_open()) {
        throw std::runtime_error("Cannot open UCI data file: " + filename);
    }

    UciDataset dataset;
    if (!(file >> dataset.num_docs >> dataset.vocab_size >> dataset.nnz)) {
        throw std::runtime_error("Invalid UCI header in file: " + filename);
    }

    dataset.docs.resize(dataset.num_docs + 1);

    int doc_id, word_id;
    int64_t count;
    while (file >> doc_id >> word_id >> count) {
        if (doc_id < 1 || doc_id > dataset.num_docs) {
            throw std::runtime_error("doc_id out of range in UCI file: " + std::to_string(doc_id));
        }
        dataset.docs[doc_id].push_back({word_id, count});
    }

    return dataset;
}

std::vector<int> collect_non_empty_doc_ids(const UciDataset& dataset) {
    std::vector<int> doc_ids;
    for (int doc_id = 1; doc_id <= dataset.num_docs; ++doc_id) {
        if (!dataset.docs[doc_id].empty()) {
            doc_ids.push_back(doc_id);
        }
    }
    return doc_ids;
}

std::unordered_map<int, int64_t> aggregate_weights(
    const std::vector<WeightedWord>& set) {

    std::unordered_map<int, int64_t> weights;
    for (const auto& item : set) {
        weights[item.value] += item.weight;
    }
    return weights;
}

std::unordered_map<int, int64_t> aggregate_weights(
    const std::vector<std::vector<WeightedWord>>& sets) {

    std::unordered_map<int, int64_t> weights;
    for (const auto& set : sets) {
        for (const auto& item : set) {
            weights[item.value] += item.weight;
        }
    }
    return weights;
}

int64_t compute_weighted_intersection_sum(
    const std::unordered_map<int, int64_t>& set1,
    const std::unordered_map<int, int64_t>& set2) {

    int64_t weighted_sum = 0;
    for (const auto& [value, weight1] : set1) {
        auto it = set2.find(value);
        if (it != set2.end()) {
            weighted_sum += weight1 * it->second;
        }
    }
    return weighted_sum;
}

int compute_distinct_intersection_size(
    const std::unordered_map<int, int64_t>& set1,
    const std::unordered_map<int, int64_t>& set2) {

    int intersection_size = 0;
    for (const auto& [value, _] : set1) {
        if (set2.count(value)) {
            ++intersection_size;
        }
    }
    return intersection_size;
}

std::string join_doc_ids(const std::vector<int>& doc_ids) {
    std::string result;
    for (size_t i = 0; i < doc_ids.size(); ++i) {
        if (i > 0) {
            result += ",";
        }
        result += std::to_string(doc_ids[i]);
    }
    return result;
}

// ==================== WAN distributed-data-generation (FHE only, non-weighted) ====================
// PJC-derived WAN networking: Server 1 generates both sets, sends Set 2 to Server 2 over TCP.
// Used by run_seed_tests.sh --mode wan and other FHE WAN tests. Non-weighted only in this build.

struct TransferHeader {
    int intersection_size;
    int universal_size_bit;
    int num_clients_per_server;
    int set_size;
    int actual_intersection_size;
    int set2_size;
};

void send_all(int fd, const void* data, size_t size) {
    const char* ptr = static_cast<const char*>(data);
    while (size > 0) {
        ssize_t sent = send(fd, ptr, size, 0);
        if (sent <= 0) {
            throw std::runtime_error("Socket send failed");
        }
        ptr += sent;
        size -= static_cast<size_t>(sent);
    }
}

void recv_all(int fd, void* data, size_t size) {
    char* ptr = static_cast<char*>(data);
    while (size > 0) {
        ssize_t received = recv(fd, ptr, size, 0);
        if (received <= 0) {
            throw std::runtime_error("Socket receive failed");
        }
        ptr += received;
        size -= static_cast<size_t>(received);
    }
}

int create_server_socket(int port) {
    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        throw std::runtime_error("Failed to create server socket");
    }
    int opt = 1;
    if (setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt)) < 0) {
        close(listen_fd);
        throw std::runtime_error("Failed to set SO_REUSEADDR");
    }
    sockaddr_in addr {};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(static_cast<uint16_t>(port));
    if (bind(listen_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        close(listen_fd);
        throw std::runtime_error("Failed to bind server socket on port " + std::to_string(port));
    }
    if (listen(listen_fd, 1) < 0) {
        close(listen_fd);
        throw std::runtime_error("Failed to listen on server socket");
    }
    return listen_fd;
}

int accept_connection(int listen_fd) {
    int conn_fd = accept(listen_fd, nullptr, nullptr);
    if (conn_fd < 0) {
        throw std::runtime_error("Failed to accept incoming WAN data connection");
    }
    return conn_fd;
}

int connect_to_server1(const std::string& host, int port) {
    sockaddr_in addr {};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(port));
    if (inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
        throw std::runtime_error("Invalid server1 host: " + host);
    }
    for (int attempt = 0; attempt < 30; ++attempt) {
        int sock_fd = socket(AF_INET, SOCK_STREAM, 0);
        if (sock_fd < 0) {
            throw std::runtime_error("Failed to create client socket");
        }
        if (connect(sock_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0) {
            return sock_fd;
        }
        close(sock_fd);
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    throw std::runtime_error(
        "Failed to connect to server1 at " + host + ":" + std::to_string(port));
}

int compute_actual_intersection_size_int(const std::vector<int>& set1, const std::vector<int>& set2) {
    std::set<int> set1_set(set1.begin(), set1.end());
    std::set<int> set2_set(set2.begin(), set2.end());
    std::vector<int> actual_intersection;
    std::set_intersection(set1_set.begin(), set1_set.end(),
                         set2_set.begin(), set2_set.end(),
                         std::back_inserter(actual_intersection));
    return static_cast<int>(actual_intersection.size());
}

// Write per-client non-weighted files, one element per line — matches PJC LAN/WAN format.
void write_client_files_int(const std::vector<int>& set, int num_clients_per_server,
                            int server_id, const std::string& output_dir) {
    auto client_sets = distribute_set_to_clients<int>(set, num_clients_per_server);
    for (int i = 0; i < num_clients_per_server; ++i) {
        std::string filename = output_dir + "/client" + std::to_string(i + 1) + "_" +
                               std::to_string(server_id) + ".txt";
        write_set_to_file(client_sets[i], filename);
        std::cout << "  Server " << server_id << " client " << (i + 1) << ": "
                  << client_sets[i].size() << " elements written to " << filename << "\n";
    }
}

void run_wan_mode_server1(int universal_size_bit, int intersection_size, int set_size,
                          int num_clients_per_server, const std::string& output_dir,
                          int port, uint64_t random_seed) {
    int universal_size = 1 << universal_size_bit;
    std::cout << "Generating WAN data on Server 1:\n"
              << "  Universal size: 2^" << universal_size_bit << " = " << universal_size << "\n"
              << "  Set size: " << set_size << "\n"
              << "  Intersection size: " << intersection_size << "\n"
              << "  Clients per server: " << num_clients_per_server << "\n"
              << "  Output directory: " << output_dir << "\n"
              << "  WAN transfer port: " << port << "\n\n";

    std::filesystem::create_directories(output_dir);
    std::mt19937 rng(static_cast<uint32_t>(random_seed));
    auto [set1, set2] = generate_sets_with_intersection(universal_size, intersection_size, set_size, rng);
    int actual_intersection_size = compute_actual_intersection_size_int(set1, set2);

    std::cout << "Generated sets:\n"
              << "  Set 1 size: " << set1.size() << "\n"
              << "  Set 2 size: " << set2.size() << "\n"
              << "  Actual intersection size: " << actual_intersection_size << "\n";

    std::cout << "Writing Server 1 client data:\n";
    write_client_files_int(set1, num_clients_per_server, 1, output_dir);

    TransferHeader header {
        intersection_size,
        universal_size_bit,
        num_clients_per_server,
        set_size,
        actual_intersection_size,
        static_cast<int>(set2.size())
    };

    int listen_fd = create_server_socket(port);
    std::cout << "Waiting for Server 2 to receive Set 2 on port " << port << std::endl;
    int conn_fd = accept_connection(listen_fd);
    send_all(conn_fd, &header, sizeof(header));
    send_all(conn_fd, set2.data(), set2.size() * sizeof(int));
    close(conn_fd);
    close(listen_fd);

    std::cout << "Sent Set 2 to Server 2\n";
    std::cout << "\nWAN data generation completed successfully on Server 1!\n";
}

void run_wan_mode_server2(int universal_size_bit, int intersection_size, int set_size,
                          int num_clients_per_server, const std::string& output_dir,
                          int port, const std::string& server1_host) {
    std::cout << "Receiving WAN data on Server 2:\n"
              << "  Server 1 host: " << server1_host << "\n"
              << "  WAN transfer port: " << port << "\n"
              << "  Output directory: " << output_dir << "\n\n";

    std::filesystem::create_directories(output_dir);
    int sock_fd = connect_to_server1(server1_host, port);

    TransferHeader header {};
    recv_all(sock_fd, &header, sizeof(header));

    if (header.num_clients_per_server != num_clients_per_server)
        throw std::runtime_error("num_clients_per_server mismatch between Server 1 and Server 2");
    if (header.set_size != set_size)
        throw std::runtime_error("set_size mismatch between Server 1 and Server 2");
    if (header.intersection_size != intersection_size)
        throw std::runtime_error("intersection_size mismatch between Server 1 and Server 2");
    if (header.universal_size_bit != universal_size_bit)
        throw std::runtime_error("universal_size_bit mismatch between Server 1 and Server 2");

    std::vector<int> set2(header.set2_size);
    recv_all(sock_fd, set2.data(), set2.size() * sizeof(int));
    close(sock_fd);

    std::cout << "Received Set 2 from Server 1:\n"
              << "  Set 2 size: " << set2.size() << "\n"
              << "  Actual intersection size: " << header.actual_intersection_size << "\n";

    std::cout << "Writing Server 2 client data:\n";
    write_client_files_int(set2, num_clients_per_server, 2, output_dir);

    std::cout << "\nWAN data generation completed successfully on Server 2!\n";
}

// ==================== End WAN block ====================

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n"
              << "Options:\n"
              << "  --intersection_size=<n>      Size of intersection for random mode (default: 100)\n"
              << "  --universal_size_bit=<n>     Universal set size as 2^n for random mode (default: 12)\n"
              << "  --num_clients_per_server=<n> Number of clients per server (default: 1)\n"
              << "  --set_size=<n>               Size of each set for random mode (default: 1000)\n"
              << "  --output_dir=<dir>           Output directory for files (default: ./data)\n"
              << "  --uci_data_file=<path>       Use a UCI Bag-of-Words file instead of random generation\n"
              << "  --random_seed=<n>            Optional deterministic seed for sampling\n"
              << "  --max_weight=<n>             Random mode: assign each element a uniform weight in [1, n]\n"
              << "                               (default 0 = no weight column, same as before)\n"
              << "  --network_mode=<lan|wan>     LAN (default, single host) or WAN (Server1 generates and sends to Server2)\n"
              << "  -p <1|2>                     For --network_mode=wan: which server is this process\n"
              << "  --port=<n>                   WAN transfer port (default: 23000)\n"
              << "  --server1_host=<host>        For --network_mode=wan + Server 2: address of Server 1\n"
              << "  --help                       Show this help message\n";
}

int main(int argc, char** argv) {
    int intersection_size = 200;
    int universal_size_bit = 12;
    int num_clients_per_server = 1;
    int set_size = 600;
    std::string output_dir = "./data";
    std::string uci_data_file;
    int64_t max_weight = 0;  // 0 = no weights (current default)
    uint64_t random_seed = std::chrono::system_clock::now().time_since_epoch().count();
    // WAN-mode-only args (LAN mode ignores these)
    std::string network_mode = "lan";
    int wan_port = 23000;
    std::string server1_host = "127.0.0.1";
    int wan_server_id = 0;

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

        if (arg == "-p" && i + 1 < argc) {
            wan_server_id = std::atoi(argv[++i]);
        } else if (auto val = get_value("--intersection_size="); !val.empty()) {
            intersection_size = std::stoi(val);
        } else if (auto val = get_value("--universal_size_bit="); !val.empty()) {
            universal_size_bit = std::stoi(val);
        } else if (auto val = get_value("--num_clients_per_server="); !val.empty()) {
            num_clients_per_server = std::stoi(val);
        } else if (auto val = get_value("--set_size="); !val.empty()) {
            set_size = std::stoi(val);
        } else if (auto val = get_value("--output_dir="); !val.empty()) {
            output_dir = val;
        } else if (auto val = get_value("--uci_data_file="); !val.empty()) {
            uci_data_file = val;
        } else if (auto val = get_value("--random_seed="); !val.empty()) {
            random_seed = std::stoull(val);
        } else if (auto val = get_value("--max_weight="); !val.empty()) {
            try {
                max_weight = std::stoll(val);
            } catch (const std::invalid_argument&) {
                std::cerr << "Error: invalid --max_weight value: " << val << std::endl;
                return 1;
            } catch (const std::out_of_range&) {
                std::cerr << "Error: --max_weight out of int64 range: " << val << std::endl;
                return 1;
            }
        } else if (auto val = get_value("--network_mode="); !val.empty()) {
            network_mode = val;
        } else if (auto val = get_value("--port="); !val.empty()) {
            wan_port = std::stoi(val);
        } else if (auto val = get_value("--server1_host="); !val.empty()) {
            server1_host = val;
        } else {
            std::cerr << "Unknown argument: " << arg << std::endl;
            print_usage(argv[0]);
            return 1;
        }
    }

    if (num_clients_per_server <= 0) {
        std::cerr << "Error: num_clients_per_server must be positive" << std::endl;
        return 1;
    }

    // WAN mode dispatch (FHE-only, non-weighted in this build)
    if (network_mode == "wan") {
        if (wan_server_id != 1 && wan_server_id != 2) {
            std::cerr << "Error: --network_mode=wan requires -p <1|2>" << std::endl;
            return 1;
        }
        if (!uci_data_file.empty()) {
            std::cerr << "Error: WAN mode does not support --uci_data_file (non-weighted only)" << std::endl;
            return 1;
        }
        try {
            if (wan_server_id == 1) {
                run_wan_mode_server1(universal_size_bit, intersection_size, set_size,
                                     num_clients_per_server, output_dir, wan_port, random_seed);
            } else {
                run_wan_mode_server2(universal_size_bit, intersection_size, set_size,
                                     num_clients_per_server, output_dir, wan_port, server1_host);
            }
        } catch (const std::exception& e) {
            std::cerr << "WAN gendata error: " << e.what() << std::endl;
            return 1;
        }
        return 0;
    }

    std::mt19937 rng(static_cast<uint32_t>(random_seed));
    std::filesystem::create_directories(output_dir);

    if (!uci_data_file.empty()) {
        std::cout << "Generating client data from UCI Bag-of-Words file:\n"
                  << "  UCI file: " << uci_data_file << "\n"
                  << "  Clients per server: " << num_clients_per_server << "\n"
                  << "  Output directory: " << output_dir << "\n"
                  << "  Random seed: " << random_seed << "\n\n";

        UciDataset dataset = load_uci_dataset(uci_data_file);
        std::vector<int> available_doc_ids = collect_non_empty_doc_ids(dataset);
        const int required_docs = 2 * num_clients_per_server;
        if (static_cast<int>(available_doc_ids.size()) < required_docs) {
            std::cerr << "Error: UCI dataset needs at least " << required_docs
                      << " non-empty documents for " << num_clients_per_server
                      << " clients per server" << std::endl;
            return 1;
        }

        std::shuffle(available_doc_ids.begin(), available_doc_ids.end(), rng);
        std::vector<int> server1_doc_ids(available_doc_ids.begin(),
                                         available_doc_ids.begin() + num_clients_per_server);
        std::vector<int> server2_doc_ids(available_doc_ids.begin() + num_clients_per_server,
                                         available_doc_ids.begin() + 2 * num_clients_per_server);

        std::vector<std::vector<WeightedWord>> client_sets_1, client_sets_2;
        client_sets_1.reserve(num_clients_per_server);
        client_sets_2.reserve(num_clients_per_server);
        for (int doc_id : server1_doc_ids) {
            client_sets_1.push_back(dataset.docs[doc_id]);
        }
        for (int doc_id : server2_doc_ids) {
            client_sets_2.push_back(dataset.docs[doc_id]);
        }

        std::cout << "Selected documents:\n";
        for (int i = 0; i < num_clients_per_server; ++i) {
            std::cout << "  Server 1 client " << (i + 1) << " doc_id: " << server1_doc_ids[i]
                      << " (" << client_sets_1[i].size() << " unique words)\n";
        }
        for (int i = 0; i < num_clients_per_server; ++i) {
            std::cout << "  Server 2 client " << (i + 1) << " doc_id: " << server2_doc_ids[i]
                      << " (" << client_sets_2[i].size() << " unique words)\n";
        }

        auto merged_set1 = aggregate_weights(client_sets_1);
        auto merged_set2 = aggregate_weights(client_sets_2);
        int actual_intersection_size = compute_distinct_intersection_size(merged_set1, merged_set2);
        int64_t weighted_intersection_sum = compute_weighted_intersection_sum(merged_set1, merged_set2);
        std::cout << "  Distinct intersection size: " << actual_intersection_size << "\n"
                  << "  Weighted intersection sum: " << weighted_intersection_sum << "\n\n";

        std::cout << "Writing weighted client files:\n";
        for (int i = 0; i < num_clients_per_server; ++i) {
            std::string filename = output_dir + "/client" + std::to_string(1 + i) + "_1.txt";
            write_weighted_set_to_file(client_sets_1[i], filename);
        }
        for (int i = 0; i < num_clients_per_server; ++i) {
            std::string filename = output_dir + "/client" + std::to_string(1 + i) + "_2.txt";
            write_weighted_set_to_file(client_sets_2[i], filename);
        }

        std::ofstream config_file(output_dir + "/config.txt");
        config_file << "mode=uci\n";
        config_file << "uci_data_file=" << uci_data_file << "\n";
        config_file << "random_seed=" << random_seed << "\n";
        config_file << "num_docs=" << dataset.num_docs << "\n";
        config_file << "vocab_size=" << dataset.vocab_size << "\n";
        config_file << "num_clients_per_server=" << num_clients_per_server << "\n";
        config_file << "server1_doc_ids=" << join_doc_ids(server1_doc_ids) << "\n";
        config_file << "server2_doc_ids=" << join_doc_ids(server2_doc_ids) << "\n";
        config_file << "server1_total_set_size=" << merged_set1.size() << "\n";
        config_file << "server2_total_set_size=" << merged_set2.size() << "\n";
        config_file << "actual_intersection_size=" << actual_intersection_size << "\n";
        config_file << "weighted_intersection_sum=" << weighted_intersection_sum << "\n";
        config_file.close();

        std::cout << "Configuration written to " << output_dir << "/config.txt\n";
        std::cout << "\nUCI data generation completed successfully!\n";
        return 0;
    }

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
              << "  Mode: random\n"
              << "  Universal size: 2^" << universal_size_bit << " = " << universal_size << "\n"
              << "  Set size: " << set_size << "\n"
              << "  Intersection size: " << intersection_size << "\n"
              << "  Clients per server: " << num_clients_per_server << "\n"
              << "  Output directory: " << output_dir << "\n"
              << "  Random seed: " << random_seed << "\n\n";

    auto [set1, set2] = generate_sets_with_intersection(universal_size, intersection_size, set_size, rng);
    
    std::cout << "Generated sets:\n"
              << "  Set 1 size: " << set1.size() << "\n"
              << "  Set 2 size: " << set2.size() << "\n";
    
    std::set<int> set1_set(set1.begin(), set1.end());
    std::set<int> set2_set(set2.begin(), set2.end());
    std::vector<int> actual_intersection;
    std::set_intersection(
        set1_set.begin(), set1_set.end(),
        set2_set.begin(), set2_set.end(),
        std::back_inserter(actual_intersection));

    std::cout << "  Actual intersection size: " << actual_intersection.size() << "\n\n";
    
    // 将集合分配给clients
    auto client_sets_1 = distribute_set_to_clients(set1, num_clients_per_server);
    auto client_sets_2 = distribute_set_to_clients(set2, num_clients_per_server);

    if (max_weight < 0) {
        std::cerr << "Error: --max_weight must be non-negative" << std::endl;
        return 1;
    }
    int64_t weighted_intersection_sum = 0;
    bool emit_weights = max_weight > 0;
    std::vector<std::vector<WeightedWord>> weighted_sets_1, weighted_sets_2;

    if (emit_weights) {
        std::uniform_int_distribution<int64_t> weight_dist(1, max_weight);
        weighted_sets_1.resize(num_clients_per_server);
        weighted_sets_2.resize(num_clients_per_server);
        for (int i = 0; i < num_clients_per_server; ++i) {
            weighted_sets_1[i].reserve(client_sets_1[i].size());
            for (int v : client_sets_1[i]) {
                weighted_sets_1[i].push_back({v, weight_dist(rng)});
            }
            weighted_sets_2[i].reserve(client_sets_2[i].size());
            for (int v : client_sets_2[i]) {
                weighted_sets_2[i].push_back({v, weight_dist(rng)});
            }
        }
        auto merged1 = aggregate_weights(weighted_sets_1);
        auto merged2 = aggregate_weights(weighted_sets_2);
        weighted_intersection_sum = compute_weighted_intersection_sum(merged1, merged2);
        std::cout << "  Max weight: " << max_weight << "\n"
                  << "  Weighted intersection sum: " << weighted_intersection_sum << "\n";
    }

    // 写入文件模式
    std::cout << "Writing data to files:\n";

    // Server 1的clients
    for (int i = 0; i < num_clients_per_server; ++i) {
        std::string filename = output_dir + "/client" + std::to_string(1 + i) + "_1.txt";
        if (emit_weights) {
            write_weighted_set_to_file(weighted_sets_1[i], filename);
        } else {
            write_set_to_file(client_sets_1[i], filename);
        }
    }

    // Server 2的clients
    for (int i = 0; i < num_clients_per_server; ++i) {
        std::string filename = output_dir + "/client" + std::to_string(1 + i) + "_2.txt";
        if (emit_weights) {
            write_weighted_set_to_file(weighted_sets_2[i], filename);
        } else {
            write_set_to_file(client_sets_2[i], filename);
        }
    }

    // 写入配置文件
    std::ofstream config_file(output_dir + "/config.txt");
    config_file << "mode=random\n";
    config_file << "random_seed=" << random_seed << "\n";
    config_file << "intersection_size=" << intersection_size << "\n";
    config_file << "universal_size_bit=" << universal_size_bit << "\n";
    config_file << "num_clients_per_server=" << num_clients_per_server << "\n";
    config_file << "set_size=" << set_size << "\n";
    config_file << "actual_intersection_size=" << actual_intersection.size() << "\n";
    config_file << "max_weight=" << max_weight << "\n";
    if (emit_weights) {
        config_file << "weighted_intersection_sum=" << weighted_intersection_sum << "\n";
    }
    config_file.close();
    
    std::cout << "Configuration written to " << output_dir << "/config.txt\n";
    
    std::cout << "\nData generation completed successfully!\n";
    return 0;
}

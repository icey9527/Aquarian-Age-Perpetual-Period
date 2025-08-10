#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <filesystem>
#include <regex>
#include <unordered_map>
#include <cstring>
#include <stdexcept>
#include <iomanip>

namespace fs = std::filesystem;

// CM��ʽ��ѹ����
std::vector<uint8_t> decompress_cm(const std::vector<uint8_t>& data, size_t max_output = 0) {
    if (data.size() < 12) {
        throw std::runtime_error("����̫�̣�ȱ��ͷ��");
    }
    
    // ���ħ��
    if (data[0] != 'C' || data[1] != 'M') {
        throw std::runtime_error("ħ����ƥ�䣬���� 'CM'");
    }
    
    // ��ȡͷ����С�ˣ�
    uint32_t out_len_header = *reinterpret_cast<const uint32_t*>(&data[4]);
    uint32_t token_len = *reinterpret_cast<const uint32_t*>(&data[8]);
    
    // Ŀ���������
    size_t target_len = (max_output == 0 || max_output >= out_len_header) ? out_len_header : max_output;
    
    size_t token_pos = 12;  // token����ʼ
    size_t flags_base = 12 + token_len;  // ��־λ����ʼ
    
    if (flags_base > data.size()) {
        throw std::runtime_error("���ݳ��Ȳ��㣺token��Խ��");
    }
    
    std::vector<uint8_t> out;
    out.reserve(target_len);
    
    size_t produced = 0;
    size_t bit_index = 0;  // �����ѱ�־λbit��
    
    while (produced < target_len) {
        // ȡ��ǰ��־λ��LSB-first��
        size_t flags_byte_idx = flags_base + (bit_index >> 3);
        if (flags_byte_idx >= data.size()) {
            throw std::runtime_error("��־λ�þ�/Խ��");
        }
        
        uint8_t flags_byte = data[flags_byte_idx];
        bool is_match = (flags_byte >> (bit_index & 7)) & 1;
        bit_index++;
        
        if (!is_match) {
            // ������
            if (token_pos >= flags_base) {
                throw std::runtime_error("token���þ�����Ҫ��������");
            }
            out.push_back(data[token_pos]);
            token_pos++;
            produced++;
        } else {
            // ƥ���2�ֽ�С�ˣ�
            if (token_pos + 2 > flags_base) {
                throw std::runtime_error("token���þ�����Ҫ2�ֽ�ƥ���");
            }
            uint16_t u16 = data[token_pos] | (data[token_pos + 1] << 8);
            token_pos += 2;
            
            size_t length = (u16 >> 12) + 3;
            size_t distance = (u16 & 0x0FFF) + 1;
            
            if (distance > out.size()) {
                throw std::runtime_error("��Ч���ݾ��룺" + std::to_string(distance) + 
                                       " > ����� " + std::to_string(out.size()));
            }
            
            // ���������ص�
            size_t to_copy = std::min(length, target_len - produced);
            while (to_copy > 0) {
                size_t chunk = std::min(distance, to_copy);
                size_t src_start = out.size() - distance;
                for (size_t i = 0; i < chunk; i++) {
                    out.push_back(out[src_start + i]);
                }
                to_copy -= chunk;
                produced += chunk;
            }
        }
    }
    
    out.resize(target_len);
    return out;
}

// ����ͷ�ļ���ȡ�ļ���ӳ��
std::unordered_map<int, std::string> parse_header_file(const fs::path& h_file_path) {
    std::unordered_map<int, std::string> name_mapping;
    
    if (!fs::exists(h_file_path)) {
        std::cout << "����: ͷ�ļ� " << h_file_path << " ������" << std::endl;
        return name_mapping;
    }
    
    std::ifstream file(h_file_path);
    if (!file.is_open()) {
        std::cout << "����: �޷���ͷ�ļ� " << h_file_path << std::endl;
        return name_mapping;
    }
    
    std::string line;
    std::regex pattern(R"(#define\s+(\S+)\s+(\d+))");
    
    while (std::getline(file, line)) {
        std::smatch match;
        if (std::regex_search(line, match, pattern)) {
            std::string name = match[1];
            int file_id = std::stoi(match[2]);
            name_mapping[file_id] = name;
        }
    }
    
    return name_mapping;
}

// ��ȡ�ļ���vector
std::vector<uint8_t> read_file(const fs::path& file_path) {
    std::ifstream file(file_path, std::ios::binary);
    if (!file.is_open()) {
        throw std::runtime_error("�޷����ļ�: " + file_path.string());
    }
    
    file.seekg(0, std::ios::end);
    size_t size = file.tellg();
    file.seekg(0, std::ios::beg);
    
    std::vector<uint8_t> data(size);
    file.read(reinterpret_cast<char*>(data.data()), size);
    
    return data;
}

// д��vector���ļ�
void write_file(const fs::path& file_path, const std::vector<uint8_t>& data) {
    std::ofstream file(file_path, std::ios::binary);
    if (!file.is_open()) {
        throw std::runtime_error("�޷������ļ�: " + file_path.string());
    }
    
    file.write(reinterpret_cast<const char*>(data.data()), data.size());
}

// �������DAT�ļ�
void extract_dat_file(const fs::path& dat_file_path, const fs::path& output_dir) {
    std::cout << "�����ļ�: " << dat_file_path << std::endl;
    
    std::vector<uint8_t> data = read_file(dat_file_path);
    
    if (data.size() < 12) {
        std::cout << "����: " << dat_file_path << " �ļ�̫С" << std::endl;
        return;
    }
    
    // ��ȡ������ͷ��
    uint32_t file_count = *reinterpret_cast<const uint32_t*>(&data[0]);
    uint32_t data_start_value = *reinterpret_cast<const uint32_t*>(&data[4]);
    
    // ����ʵ�ʵ�������ʼ��ַ
    uint32_t data_start_address = data_start_value * 32;
    
    std::cout << "�ļ�����: " << file_count << std::endl;
    std::cout << "������ʼ��ַ: 0x" << std::hex << std::setw(8) << std::setfill('0') 
              << data_start_address << " (ֵ: " << std::dec << data_start_value << ")" << std::endl;
    
    if (file_count == 0 || file_count > 1000) {
        std::cout << "����: �ļ������쳣 (" << file_count << ")" << std::endl;
        return;
    }
    
    // ���Ҷ�Ӧ��.h�ļ�
    fs::path base_name = dat_file_path.stem();
    fs::path h_file_path = dat_file_path.parent_path() / (base_name.string() + ".h");
    auto name_mapping = parse_header_file(h_file_path);
    
    // ��������ļ���
    fs::path output_folder = output_dir / base_name.filename();
    fs::create_directories(output_folder);
    
    // ��ȡ�ļ�����λ��������
    std::vector<uint32_t> end_positions;
    for (uint32_t i = 0; i < file_count; i++) {
        size_t offset = 8 + i * 4;
        if (offset + 4 <= data.size()) {
            uint32_t end_value = *reinterpret_cast<const uint32_t*>(&data[offset]);
            uint32_t end_position = end_value * 32;  // ����32�õ�ʵ�ʵ�ַ
            end_positions.push_back(end_position);
        } else {
            std::cout << "����: ���������ݲ���" << std::endl;
            return;
        }
    }
    
    // ��ʾǰ10������λ��ֵ
    std::cout << "����λ��ֵ: ";
    for (size_t i = 0; i < std::min(size_t(10), end_positions.size()); i++) {
        std::cout << end_positions[i] / 32 << " ";
    }
    if (end_positions.size() > 10) std::cout << "...";
    std::cout << std::endl;
    
    // ��ȡ�ļ�
    int extracted_count = 0;
    uint32_t current_start = data_start_address;
    
    for (uint32_t i = 0; i < file_count; i++) {
        uint32_t file_start = current_start;
        uint32_t file_end = end_positions[i];
        int32_t file_size = file_end - file_start;
        
        if (file_start >= data.size() || file_size <= 0) {
            std::cout << "������Ч�ļ� " << i << ": ��ʼλ�� 0x" << std::hex << file_start 
                      << ", ����λ�� 0x" << file_end << ", ��С " << std::dec << file_size << std::endl;
            current_start = file_end;
            continue;
        }
        
        if (file_end > data.size()) {
            std::cout << "����: �ļ� " << i << " �������ݷ�Χ���ضϵ��ļ�ĩβ" << std::endl;
            file_end = data.size();
            file_size = file_end - file_start;
        }
        
        // ��ȡ�ļ�����
        std::vector<uint8_t> file_data(data.begin() + file_start, data.begin() + file_end);
        
        // ȷ���ļ���
        std::string filename = std::to_string(i);
        auto it = name_mapping.find(i);
        if (it != name_mapping.end()) {
            filename += "." + it->second;
        }
        
        // �����ļ�����ѹ��
        fs::path output_path = output_folder / filename;
        try {
            std::vector<uint8_t> decompressed_data = decompress_cm(file_data);
            write_file(output_path, decompressed_data);
            std::cout << "  �ļ� " << std::setw(3) << i << ": " 
                      << std::left << std::setw(25) << filename 
                      << " (0x" << std::hex << std::setw(8) << std::setfill('0') << file_start 
                      << " - 0x" << std::setw(8) << std::setfill('0') << file_end 
                      << ", " << std::dec << std::setw(6) << file_size << " �ֽ�)" << std::endl;
        } catch (const std::exception& e) {
            std::cout << "  �ļ� " << std::setw(3) << i << ": ��ѹʧ�� - " << e.what() << std::endl;
        }
        
        extracted_count++;
        current_start = file_end;
    }
    
    std::cout << "���! ��ȡ�� " << extracted_count << " ���ļ��� " << output_folder << std::endl;
    std::cout << std::string(70, '-') << std::endl;
}

// ��������DAT�ļ�
void process_all_dat_files(const fs::path& input_dir, const fs::path& output_dir) {
    if (!fs::exists(input_dir)) {
        std::cout << "����: �����ļ��� " << input_dir << " ������" << std::endl;
        return;
    }
    
    std::vector<fs::path> dat_files;
    for (const auto& entry : fs::directory_iterator(input_dir)) {
        if (entry.path().extension() == ".dat") {
            dat_files.push_back(entry.path());
        }
    }
    
    if (dat_files.empty()) {
        std::cout << "�� " << input_dir << " ��û���ҵ�.dat�ļ�" << std::endl;
        return;
    }
    
    std::cout << "�ҵ� " << dat_files.size() << " ��.dat�ļ�" << std::endl;
    fs::create_directories(output_dir);
    
    for (const auto& dat_file : dat_files) {
        try {
            extract_dat_file(dat_file, output_dir);
        } catch (const std::exception& e) {
            std::cout << "���� " << dat_file << " ʱ����: " << e.what() << std::endl;
            continue;
        }
    }
}

int main(int argc, char* argv[]) {
    if (argc != 3) {
        std::cout << "�÷�: " << argv[0] << " <�����ļ���> <����ļ���>" << std::endl;
        return 1;
    }
    
    fs::path input_directory = argv[1];
    fs::path output_directory = argv[2];
    
    std::cout << "DAT�ļ�������� (C++�汾)" << std::endl;
    std::cout << std::string(70, '=') << std::endl;
    
    process_all_dat_files(input_directory, output_directory);
    std::cout << "\n������!" << std::endl;
    
    return 0;
}
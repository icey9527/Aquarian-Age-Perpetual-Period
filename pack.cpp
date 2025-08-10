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
#include <algorithm>
#include <cstdint>

namespace fs = std::filesystem;

// ���ߺ���
static inline size_t align_up(size_t x, size_t a) {
    return (x + a - 1) / a * a;
}

static inline void write_u32_le(std::ostream& os, uint32_t v) {
    char b[4];
    b[0] = static_cast<char>(v & 0xFF);
    b[1] = static_cast<char>((v >> 8) & 0xFF);
    b[2] = static_cast<char>((v >> 16) & 0xFF);
    b[3] = static_cast<char>((v >> 24) & 0xFF);
    os.write(b, 4);
}

static inline uint32_t read_u32_le(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

std::vector<uint8_t> read_file(const fs::path& file_path) {
    std::ifstream f(file_path, std::ios::binary);
    if (!f.is_open()) throw std::runtime_error("�޷����ļ�: " + file_path.string());
    f.seekg(0, std::ios::end);
    size_t sz = static_cast<size_t>(f.tellg());
    f.seekg(0, std::ios::beg);
    std::vector<uint8_t> buf(sz);
    if (sz) f.read(reinterpret_cast<char*>(buf.data()), sz);
    return buf;
}

// ========== CM ѹ���������ѹ��ƥ�䣩 ==========
// ���䣺���� 1..4096������ 3..18��4bit �泤��-3��12bit �����-1��
std::vector<uint8_t> compress_cm(const std::vector<uint8_t>& in) {
    const size_t n = in.size();
    std::vector<uint8_t> tokens;
    std::vector<uint8_t> flags;
    tokens.reserve(n); // ������ȫ��������
    flags.reserve((n + 7) / 8);

    uint8_t cur_flags = 0;
    int bit_pos = 0;

    auto push_flag = [&](bool is_match) {
        if (is_match) cur_flags |= (1u << bit_pos);
        bit_pos++;
        if (bit_pos == 8) {
            flags.push_back(cur_flags);
            cur_flags = 0;
            bit_pos = 0;
        }
    };

    size_t pos = 0;
    while (pos < n) {
        size_t best_len = 0;
        size_t best_dist = 0;

        size_t window_start = (pos > 4096) ? (pos - 4096) : 0;
        size_t max_len = std::min(static_cast<size_t>(18), n - pos);

        if (max_len >= 3) {
            // ���ػ����������ӽ���ԶѰ���ƥ�䣩
            for (size_t cand = pos; cand-- > window_start;) {
                if (in[cand] != in[pos]) continue;
                size_t len = 1;
                while (len < max_len && in[cand + len] == in[pos + len]) len++;
                if (len >= 3 && len > best_len) {
                    best_len = len;
                    best_dist = pos - cand;
                    if (best_len == 18) break; // �Ѵ�����
                }
            }
        }

        if (best_len >= 3 && best_dist >= 1 && best_dist <= 4096) {
            // ƥ����
            push_flag(true);
            uint16_t u16 = static_cast<uint16_t>(((best_len - 3) << 12) | ((best_dist - 1) & 0x0FFF));
            tokens.push_back(static_cast<uint8_t>(u16 & 0xFF));
            tokens.push_back(static_cast<uint8_t>(u16 >> 8));
            pos += best_len;
        } else {
            // ������
            push_flag(false);
            tokens.push_back(in[pos]);
            pos += 1;
        }
    }

    if (bit_pos != 0) {
        flags.push_back(cur_flags);
    }

    // ��װ�����ͷ(12B) + token�� + flags��
    std::vector<uint8_t> out;
    out.reserve(12 + tokens.size() + flags.size());
    out.push_back('C'); out.push_back('M');
    out.push_back(0); out.push_back(0);

    // ԭʼ�������
    uint32_t out_len = static_cast<uint32_t>(n);
    out.push_back(static_cast<uint8_t>(out_len & 0xFF));
    out.push_back(static_cast<uint8_t>((out_len >> 8) & 0xFF));
    out.push_back(static_cast<uint8_t>((out_len >> 16) & 0xFF));
    out.push_back(static_cast<uint8_t>((out_len >> 24) & 0xFF));

    // token������
    uint32_t token_len = static_cast<uint32_t>(tokens.size());
    out.push_back(static_cast<uint8_t>(token_len & 0xFF));
    out.push_back(static_cast<uint8_t>((token_len >> 8) & 0xFF));
    out.push_back(static_cast<uint8_t>((token_len >> 16) & 0xFF));
    out.push_back(static_cast<uint8_t>((token_len >> 24) & 0xFF));

    // token + flags
    out.insert(out.end(), tokens.begin(), tokens.end());
    out.insert(out.end(), flags.begin(), flags.end());
    return out;
}

// ����Ŀ¼���ļ���������ǰ׺����
struct Entry {
    uint32_t index;
    fs::path path;
};

std::vector<Entry> collect_entries(const fs::path& dir) {
    std::vector<Entry> v;
    std::regex pat(R"(^(\d+))", std::regex::ECMAScript);
    for (const auto& it : fs::directory_iterator(dir)) {
        if (!it.is_regular_file()) continue;
        std::string name = it.path().filename().string();
        std::smatch m;
        if (std::regex_search(name, m, pat)) {
            uint32_t idx = static_cast<uint32_t>(std::stoul(m[1].str()));
            v.push_back({ idx, it.path() });
        }
    }
    if (v.empty()) {
        throw std::runtime_error("Ŀ¼��δ�ҵ����� '0', '1.xxx' ���ļ�");
    }
    std::sort(v.begin(), v.end(), [](const Entry& a, const Entry& b) { return a.index < b.index; });

    // ��������� 0..N-1
    for (size_t i = 0; i < v.size(); ++i) {
        if (v[i].index != i) {
            throw std::runtime_error("�ļ����������������� " + std::to_string(i) + "�������� " + std::to_string(v[i].index));
        }
    }
    return v;
}

// ���һ��Ŀ¼Ϊһ�� .dat �ļ�
void pack_single_dat_folder(const fs::path& folder, const fs::path& out_dat_path) {
    std::cout << "���Ŀ¼: " << folder << " -> " << out_dat_path << std::endl;

    auto entries = collect_entries(folder);
    uint32_t file_count = static_cast<uint32_t>(entries.size());
    std::cout << "�ļ�����: " << file_count << std::endl;

    // ������ļ�
    std::ofstream out(out_dat_path, std::ios::binary | std::ios::trunc);
    if (!out.is_open()) {
        throw std::runtime_error("�޷���������ļ�: " + out_dat_path.string());
    }

    // Ԥ��ͷ+������
    size_t header_size = 8 + static_cast<size_t>(file_count) * 4;
    std::vector<char> zero(header_size, 0);
    out.write(zero.data(), zero.size());

    // ������ʼ��ַ��32�ֽڶ��룩
    size_t data_start_off = align_up(header_size, 32);
    if (data_start_off > header_size) {
        std::vector<char> pad(data_start_off - header_size, 0);
        out.write(pad.data(), pad.size());
    }

    // д������������¼����λ��ֵ����λ=32�ֽڣ�
    size_t cur_off = data_start_off;
    std::vector<uint32_t> end_values;
    end_values.reserve(file_count);

    for (uint32_t i = 0; i < file_count; ++i) {
        auto raw = read_file(entries[i].path);
        auto cm = compress_cm(raw);

        out.write(reinterpret_cast<const char*>(cm.data()), cm.size());
        cur_off += cm.size();

        // 32����
        size_t aligned = align_up(cur_off, 32);
        if (aligned > cur_off) {
            std::vector<char> pad(aligned - cur_off, 0);
            out.write(pad.data(), pad.size());
        }
        cur_off = aligned;

        uint32_t end_value = static_cast<uint32_t>(cur_off / 32);
        end_values.push_back(end_value);

        std::cout << "  �ļ� " << std::setw(3) << i
                  << ": ԭʼ " << std::setw(8) << raw.size()
                  << "B -> ѹ�� " << std::setw(8) << cm.size() << "B, ����ֵ=" << end_value
                  << " (ƫ��=0x" << std::hex << std::setw(8) << std::setfill('0') << (end_value * 32)
                  << std::dec << std::setfill(' ') << ")\n";
    }

    // ����ͷ������
    out.seekp(0, std::ios::beg);
    write_u32_le(out, file_count);
    write_u32_le(out, static_cast<uint32_t>(data_start_off / 32));
    for (uint32_t v : end_values) {
        write_u32_le(out, v);
    }

    out.flush();
    out.close();

    std::cout << "���! ������: " << out_dat_path << std::endl;
    std::cout << std::string(70, '-') << std::endl;
}

// ��������Ŀ¼��������Ŀ¼ -> ���Ŀ¼�¶�Ӧ.dat
void pack_all_folders(const fs::path& input_root, const fs::path& output_dir) {
    if (!fs::exists(input_root)) {
        std::cout << "����: �����ļ��� " << input_root << " ������\n";
        return;
    }
    fs::create_directories(output_dir);

    // �� input_root ֱ����һ������ȡ��ĵ����ļ��С���Ҳ����ֻ��һ��
    bool has_subdir = false;
    for (const auto& it : fs::directory_iterator(input_root)) {
        if (it.is_directory()) { has_subdir = true; break; }
    }

    if (!has_subdir) {
        // ֱ�Ӱ� input_root ���һ�� .dat������� output_dir/ͬ��.dat��
        fs::path out_dat = output_dir / (input_root.filename().string() + ".dat");
        try {
            pack_single_dat_folder(input_root, out_dat);
        } catch (const std::exception& e) {
            std::cout << "���ʧ��: " << e.what() << "\n";
        }
        return;
    }

    // ���򣺱���ÿ����Ŀ¼
    int packed = 0;
    for (const auto& it : fs::directory_iterator(input_root)) {
        if (!it.is_directory()) continue;
        fs::path folder = it.path();
        fs::path out_dat = output_dir / (folder.filename().string() + ".dat");
        try {
            pack_single_dat_folder(folder, out_dat);
            packed++;
        } catch (const std::exception& e) {
            std::cout << "��� " << folder << " ʱʧ��: " << e.what() << "\n";
        }
    }
    if (packed == 0) {
        std::cout << "δ�ҵ�Ҫ�������Ŀ¼\n";
    } else {
        std::cout << "ȫ��������! ������ " << packed << " �� .dat\n";
    }
}

int main(int argc, char* argv[]) {
    if (argc != 3) {
        std::cout << "�÷�: " << argv[0] << " <�����ļ���> <����ļ���>\n";
        std::cout << "˵��: ���������һ������ȡ��ĵ���Ŀ¼��������������Ŀ¼�ĸ�Ŀ¼��\n"
                     "      �����Ϊÿ����Ŀ¼����һ��ͬ�� .dat ������ļ��С�\n";
        return 1;
    }

    fs::path input_directory = argv[1];
    fs::path output_directory = argv[2];

    std::cout << "DAT�ļ�������� (C++�汾)\n";
    std::cout << std::string(70, '=') << std::endl;

    try {
        pack_all_folders(input_directory, output_directory);
        std::cout << "\n������!\n";
    } catch (const std::exception& e) {
        std::cout << "��������: " << e.what() << "\n";
        return 2;
    }

    return 0;
}
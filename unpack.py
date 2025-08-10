import os
import re
import struct
from pathlib import Path
import sys

def decompress_cm(data: bytes, max_output: int = 0) -> bytes:
    """
    解压 'CM' 格式数据。
    data:
      - 0x00: b'CM'
      - 0x04: uint32 LE 解压后总长度
      - 0x08: uint32 LE token 区长度
      - 0x0C: token 区（字面量 1B / 匹配项 2B 小端）
      - 之后: 标志位区（LSB-first）

    标志位:
      - 0 -> 字面量: 读 1 字节，直接输出
      - 1 -> 匹配项: 读 2 字节小端
           length = (u16 >> 12) + 3
           distance = (u16 & 0x0FFF) + 1
           从当前输出位置向前 distance 字节处开始复制 length 字节（允许重叠）

    max_output=0 表示按头部的完整长度解压；否则至多输出 max_output 字节。
    """
    if len(data) < 12:
        raise ValueError("数据太短，缺少头部")

    if data[0:2] != b'CM':
        raise ValueError("魔数不匹配，期望 'CM'")

    # 头部字段（小端）
    out_len_header, token_len = struct.unpack_from('<II', data, 4)

    # 目标输出长度（受 max_output 限制）
    target_len = out_len_header if not max_output or max_output >= out_len_header else max_output

    token_pos = 12  # token 区起始
    flags_base = 12 + token_len  # 标志位区起始

    if flags_base > len(data):
        raise ValueError("数据长度不足：token 区越界")

    out = bytearray()
    produced = 0
    bit_index = 0  # 已消费标志位 bit 数

    while produced < target_len:
        # 取当前标志位（LSB-first）
        flags_byte_idx = flags_base + (bit_index >> 3)
        if flags_byte_idx >= len(data):
            raise ValueError("标志位用尽/越界")

        flags_byte = data[flags_byte_idx]
        is_match = (flags_byte >> (bit_index & 7)) & 1
        bit_index += 1

        if is_match == 0:
            # 字面量
            if token_pos >= flags_base:
                raise ValueError("token 区用尽（需要字面量）")
            out.append(data[token_pos])
            token_pos += 1
            produced += 1
        else:
            # 匹配项（2 字节小端）
            if token_pos + 2 > flags_base:
                raise ValueError("token 区用尽（需要 2 字节匹配项）")
            u16 = data[token_pos] | (data[token_pos + 1] << 8)
            token_pos += 2

            length = (u16 >> 12) + 3
            distance = (u16 & 0x0FFF) + 1

            if distance > len(out):
                raise ValueError(f"无效回溯距离：{distance} > 已输出 {len(out)}")

            # 复制允许重叠：按块复制以保持正确的重叠语义
            to_copy = min(length, target_len - produced)
            while to_copy > 0:
                chunk = min(distance, to_copy)
                src_start = len(out) - distance
                out.extend(out[src_start:src_start + chunk])
                to_copy -= chunk
                produced += chunk

    return bytes(out[:target_len])

def parse_header_file(h_file_path):
    """解析.h文件获取文件ID到文件名的映射"""
    name_mapping = {}
    
    if not os.path.exists(h_file_path):
        print(f"警告: 头文件 {h_file_path} 不存在")
        return name_mapping
    
    with open(h_file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    pattern = r'#define\s+(\S+)\s+(\d+)'
    matches = re.findall(pattern, content)
    
    for name, file_id in matches:
        name_mapping[int(file_id)] = name
    
    return name_mapping

def extract_dat_file(dat_file_path, output_dir):
    """解包单个.dat文件"""
    print(f"处理文件: {dat_file_path}")
    
    with open(dat_file_path, 'rb') as f:
        data = f.read()
    
    if len(data) < 12:
        print(f"错误: {dat_file_path} 文件太小")
        return
    
    # 读取索引表头部
    file_count = struct.unpack('<I', data[0:4])[0]
    data_start_value = struct.unpack('<I', data[4:8])[0]
    
    # 计算实际的数据起始地址
    data_start_address = data_start_value * 32
    
    print(f"文件数量: {file_count}")
    print(f"数据起始地址: 0x{data_start_address:08X} (值: {data_start_value})")
    
    if file_count == 0 or file_count > 1000:
        print(f"错误: 文件数量异常 ({file_count})")
        return
    
    # 查找对应的.h文件
    base_name = os.path.splitext(dat_file_path)[0]
    h_file_path = base_name + '.h'
    name_mapping = parse_header_file(h_file_path)
    
    # 创建输出文件夹
    folder_name = os.path.basename(base_name)
    output_folder = os.path.join(output_dir, folder_name)
    os.makedirs(output_folder, exist_ok=True)
    
    # 读取文件结束位置索引表
    end_positions = []
    for i in range(file_count):
        offset = 8 + i * 4
        if offset + 4 <= len(data):
            end_value = struct.unpack('<I', data[offset:offset+4])[0]
            end_position = end_value * 32  # 乘以32得到实际地址
            end_positions.append(end_position)
        else:
            print(f"错误: 索引表数据不足")
            return
    
    print(f"结束位置值: {[pos//32 for pos in end_positions[:10]]}{'...' if len(end_positions) > 10 else ''}")
    
    # 提取文件
    extracted_count = 0
    current_start = data_start_address
    
    for i in range(file_count):
        file_start = current_start
        file_end = end_positions[i]
        file_size = file_end - file_start
        
        if file_start >= len(data) or file_size <= 0:
            print(f"跳过无效文件 {i}: 起始位置 0x{file_start:X}, 结束位置 0x{file_end:X}, 大小 {file_size}")
            current_start = file_end  # 下一个文件从这个文件的结束位置开始
            continue
        
        if file_end > len(data):
            print(f"警告: 文件 {i} 超出数据范围，截断到文件末尾")
            file_end = len(data)
            file_size = file_end - file_start
        
        # 提取文件数据
        file_data = data[file_start:file_end]
        
        # 确定文件名
        filename = str(i)
        if i in name_mapping:
            filename += '.' + name_mapping[i]
            
        # 保存文件（解压后）
        output_path = os.path.join(output_folder, filename)
        try:
            decompressed_data = decompress_cm(file_data)
            with open(output_path, 'wb') as f:
                f.write(decompressed_data)
            print(f"  文件 {i:3d}: {filename:<25} (0x{file_start:08X} - 0x{file_end:08X}, {file_size:6d} 字节)")
        except Exception as e:
            print(f"  文件 {i:3d}: 解压失败 - {e}")
            # 可选：保存原始压缩数据
            # with open(output_path + '.compressed', 'wb') as f:
            #     f.write(file_data)
        
        extracted_count += 1
        current_start = file_end  # 下一个文件从当前文件的结束位置开始
    
    print(f"完成! 提取了 {extracted_count} 个文件到 {output_folder}")
    print("-" * 70)

def process_all_dat_files(input_dir, output_dir="extracted"):
    """处理指定文件夹内所有的.dat文件"""
    input_path = Path(input_dir)
    
    if not input_path.exists():
        print(f"错误: 输入文件夹 {input_dir} 不存在")
        return
    
    dat_files = list(input_path.glob("*.dat"))
    
    if not dat_files:
        print(f"在 {input_dir} 中没有找到.dat文件")
        return
    
    print(f"找到 {len(dat_files)} 个.dat文件")
    os.makedirs(output_dir, exist_ok=True)
    
    for dat_file in dat_files:
        try:
            extract_dat_file(str(dat_file), output_dir)
        except Exception as e:
            print(f"处理 {dat_file} 时出错: {e}")
            continue

if __name__ == "__main__":
    input_directory = sys.argv[1]  # 当前文件夹，你可以修改为包含.dat文件的文件夹路径
    output_directory = sys.argv[2]  # 输出文件夹
    
    print("DAT文件解包工具 (修正版 - 结束位置)")
    print("=" * 70)
    
    process_all_dat_files(input_directory, output_directory)
    print("\n解包完成!")
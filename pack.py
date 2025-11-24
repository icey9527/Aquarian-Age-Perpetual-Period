import os
import re
import struct
from pathlib import Path
import sys

def compress_cm(data: bytes) -> bytes:
    """
    压缩数据为 'CM' 格式。
    使用简单的LZ77算法实现。
    """
    if not data:
        return b'CM' + struct.pack('<II', 0, 0)
    
    out_len = len(data)
    tokens = bytearray()
    flags = []
    
    i = 0
    while i < len(data):
        # 查找最佳匹配
        best_len = 0
        best_dist = 0
        
        # 搜索窗口大小为4095字节
        search_start = max(0, i - 4095)
        
        if i > 0:  # 只有在有历史数据时才查找匹配
            for j in range(search_start, i):
                match_len = 0
                max_possible = min(18, len(data) - i)  # 最大匹配长度为18 (15+3)
                
                while match_len < max_possible and j + match_len < i:
                    if data[j + match_len] == data[i + match_len]:
                        match_len += 1
                    else:
                        break
                
                if match_len >= 3 and match_len > best_len:  # 最小匹配长度为3
                    best_len = match_len
                    best_dist = i - j
        
        if best_len >= 3:
            # 使用匹配项
            flags.append(1)
            
            # 编码格式: 高4位是长度-3，低12位是距离-1
            encoded_len = min(best_len - 3, 15)
            encoded_dist = best_dist - 1
            
            u16 = (encoded_len << 12) | encoded_dist
            tokens.extend(struct.pack('<H', u16))
            
            i += encoded_len + 3
        else:
            # 使用字面量
            flags.append(0)
            tokens.append(data[i])
            i += 1
    
    # 构建标志位字节
    flag_bytes = bytearray()
    for i in range(0, len(flags), 8):
        byte = 0
        for j in range(8):
            if i + j < len(flags):
                byte |= flags[i + j] << j
        flag_bytes.append(byte)
    
    # 构建完整的压缩数据
    header = b'CM' + b'\x00\x00'  # 占位符
    header += struct.pack('<II', out_len, len(tokens))
    
    return header + tokens + flag_bytes

def parse_header_file(h_file_path):
    """解析.h文件获取文件名到文件ID的映射"""
    name_to_id = {}
    
    if not os.path.exists(h_file_path):
        return name_to_id
    
    with open(h_file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    pattern = r'#define\s+(\S+)\s+(\d+)'
    matches = re.findall(pattern, content)
    
    for name, file_id in matches:
        name_to_id[name] = int(file_id)
    
    return name_to_id

def create_header_file(output_path, file_mapping):
    """创建.h头文件"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("// Auto-generated header file\n\n")
        for name, file_id in sorted(file_mapping.items(), key=lambda x: x[1]):
            f.write(f"#define {name} {file_id}\n")

def pack_dat_file(input_folder, dat_output_path, h_output_path=None):
    """将文件夹中的文件打包成.dat文件"""
    input_path = Path(input_folder)
    
    if not input_path.exists():
        print(f"错误: 输入文件夹 {input_folder} 不存在")
        return False
    
    # 获取所有文件并排序
    files = []
    file_mapping = {}
    
    for file_path in sorted(input_path.iterdir()):
        if file_path.is_file():
            # 解析文件名格式: "序号.名称" 或 "序号"
            filename = file_path.name
            parts = filename.split('.', 1)
            
            try:
                file_id = int(parts[0])
                file_name = parts[1] if len(parts) > 1 else None
                
                files.append({
                    'id': file_id,
                    'name': file_name,
                    'path': file_path
                })
                
                if file_name:
                    file_mapping[file_name] = file_id
                    
            except ValueError:
                print(f"警告: 跳过无效文件名 {filename}")
                continue
    
    if not files:
        print(f"错误: 在 {input_folder} 中没有找到有效文件")
        return False
    
    # 按ID排序
    files.sort(key=lambda x: x['id'])
    
    # 确保文件ID连续
    expected_id = 0
    for f in files:
        if f['id'] != expected_id:
            print(f"警告: 文件ID不连续，期望 {expected_id}，实际 {f['id']}")
        expected_id = f['id'] + 1
    
    file_count = len(files)
    print(f"准备打包 {file_count} 个文件")
    
    # 压缩所有文件
    compressed_files = []
    for f in files:
        with open(f['path'], 'rb') as fp:
            raw_data = fp.read()
        compressed_data = compress_cm(raw_data)
        compressed_files.append(compressed_data)
        print(f"  压缩文件 {f['id']}: {f['name'] or '(无名)'} ({len(raw_data)} -> {len(compressed_data)} 字节)")
    
    # 计算索引表
    current_position = 0x800  # 第一个文件从0x800开始
    index_values = []
    
    for i, compressed_data in enumerate(compressed_files):
        if i > 0:  # 第一个文件不需要索引值（总是在0x800）
            # 计算索引值（位置除以32）
            index_value = current_position // 32
            index_values.append(index_value)
        
        # 对齐到32字节边界
        file_size = len(compressed_data)
        aligned_size = (file_size + 31) // 32 * 32
        current_position += aligned_size
    
    # 构建DAT文件
    output_data = bytearray()
    
    # 写入文件头
    output_data.extend(struct.pack('<I', file_count))  # 文件数量
    output_data.extend(struct.pack('<I', 0x20))        # 固定值（通常是0x20）
    
    # 写入索引表
    for value in index_values:
        output_data.extend(struct.pack('<I', value))
    
    # 填充到0x800
    while len(output_data) < 0x800:
        output_data.append(0)
    
    # 写入压缩的文件数据
    for compressed_data in compressed_files:
        start_pos = len(output_data)
        output_data.extend(compressed_data)
        
        # 对齐到32字节边界
        while len(output_data) % 32 != 0:
            output_data.append(0)
    
    # 写入DAT文件
    with open(dat_output_path, 'wb') as f:
        f.write(output_data)
    
    print(f"成功创建 {dat_output_path} ({len(output_data)} 字节)")
    
    # 创建.h文件（如果需要）
    if h_output_path and file_mapping:
        create_header_file(h_output_path, file_mapping)
        print(f"成功创建 {h_output_path}")
    
    return True

def pack_all_folders(input_dir, output_dir="packed"):
    """打包指定文件夹内的所有子文件夹"""
    input_path = Path(input_dir)
    
    if not input_path.exists():
        print(f"错误: 输入文件夹 {input_dir} 不存在")
        return
    
    # 获取所有子文件夹
    folders = [f for f in input_path.iterdir() if f.is_dir()]
    
    if not folders:
        print(f"在 {input_dir} 中没有找到子文件夹")
        return
    
    print(f"找到 {len(folders)} 个文件夹待打包")
    os.makedirs(output_dir, exist_ok=True)
    
    for folder in folders:
        folder_name = folder.name
        dat_output = os.path.join(output_dir, f"{folder_name}.dat")
        h_output = os.path.join(output_dir, f"{folder_name}.h")
        
        print(f"\n处理文件夹: {folder_name}")
        print("-" * 50)
        
        try:
            pack_dat_file(str(folder), dat_output, h_output)
        except Exception as e:
            print(f"打包 {folder_name} 时出错: {e}")
            continue

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python pack_dat.py <输入文件夹> <输出文件夹>")
        print("  输入文件夹: 包含解包后的子文件夹")
        print("  输出文件夹: 生成的DAT文件存放位置")
        sys.exit(1)
    
    input_directory = sys.argv[1]
    output_directory = sys.argv[2]
    
    print("DAT文件封包工具")
    print("=" * 70)
    
    pack_all_folders(input_directory, output_directory)
    print("\n封包完成!")
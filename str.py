#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import shutil
from collections import OrderedDict, defaultdict

# 结构常量
CHUNK_SIZE = 0x180   # 每块大小
TYPE_OFF   = 0x00    # 块内：类型
LEN_OFF    = 0x04    # 块内：字符长度（单位：字符）
TEXT_OFF   = 0x18    # 块内：文本起始偏移
TEXT_TYPE  = 0x01    # 1 = 文本块
MAX_TEXT_BYTES = CHUNK_SIZE - TEXT_OFF
REQUIRE_HEAD_0 = True   # 仅当文件前4字节==0才处理

# 将 Python 字符串渲染为 all.txt 的一行：
# - 连续的 U+0000 -> \fill(N)，N为00字节数(=个数*2)
# - \ -> \\
# - \n -> \n
# - \t -> \t
def to_visible_line(s: str) -> str:
    s = s.replace('\r\n', '\n').replace('\r', '\n')
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == '\x00':
            j = i
            while j < n and s[j] == '\x00':
                j += 1
            zeros = j - i
            out.append(f"\\fill({zeros * 2})")
            i = j
            continue
        c = s[i]
        if c == '\\':
            out.append('\\\\')
        elif c == '\n':
            out.append('\\n')
        elif c == '\t':
            out.append('\\t')
        else:
            out.append(c)
        i += 1
    return ''.join(out)

# 解析 all.txt 的一行为 Python 字符串：
# - \fill(N) -> 连续的 U+0000，数量为 N/2
# - \\ -> \
# - \n -> 换行
# - \t -> 制表
def from_visible_line(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '\\':
            # \fill(N)
            if s.startswith('fill(', i + 1):
                j = i + 1 + 5  # 指向数字起始
                k = j
                while k < n and s[k].isdigit():
                    k += 1
                if k < n and k > j and s[k] == ')':
                    num = int(s[j:k])
                    if num < 0:
                        num = 0
                    # num 必须为偶数，奇数则向下取偶并警告
                    if num % 2 != 0:
                        print(f"  [警告] \\fill({num}) 非偶数，已按 {num-1} 处理")
                        num -= 1
                    out.append('\x00' * (num // 2))
                    i = k + 1
                    continue
                # 若格式不完整，则按普通转义继续处理为字面字符
            # 常规转义
            if i + 1 < n:
                nxt = s[i + 1]
                if nxt == 'n':
                    out.append('\n')
                    i += 2
                    continue
                elif nxt == 't':
                    out.append('\t')
                    i += 2
                    continue
                elif nxt == '\\':
                    out.append('\\')
                    i += 2
                    continue
                else:
                    # 未知转义，保留其后字符
                    out.append(nxt)
                    i += 2
                    continue
            else:
                # 末尾单独的反斜杠，原样保留
                out.append('\\')
                i += 1
                continue
        else:
            out.append(c)
            i += 1
    return ''.join(out)

def encode_utf16le_fit(s: str, max_bytes: int):
    """
    将字符串严格编码为 UTF-16 LE；若超过 max_bytes，二分截断以适配。
    返回: (bytes_data, char_len)
    """
    b = s.encode('utf-16-le')
    if len(b) <= max_bytes:
        return b, len(b) // 2

    lo, hi = 0, len(s)
    best = b''
    best_chars = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        mb = s[:mid].encode('utf-16-le')
        if len(mb) <= max_bytes:
            best = mb
            best_chars = len(mb) // 2
            lo = mid + 1
        else:
            hi = mid - 1
    return best, best_chars

def extract_texts_from_file(path: str):
    """
    返回 ([(chunk_index, text_str)], skipped_reason)
    - 若要求首4字节==0 且不满足：([], 'head_not_zero')
    - 正常：([..], None)
    """
    with open(path, 'rb') as f:
        data = f.read()

    if len(data) < 4:
        return [], 'too_short'

    if REQUIRE_HEAD_0:
        head = int.from_bytes(data[0:4], 'little', signed=False)
        if head != 0:
            return [], 'head_not_zero'

    total = len(data)
    chunk_count = total // CHUNK_SIZE
    results = []

    for idx in range(chunk_count):
        base = idx * CHUNK_SIZE
        block = data[base: base + CHUNK_SIZE]
        type_val = int.from_bytes(block[TYPE_OFF:TYPE_OFF+4], 'little', signed=False)
        if type_val != TEXT_TYPE:
            continue

        char_len = int.from_bytes(block[LEN_OFF:LEN_OFF+4], 'little', signed=False)
        byte_len = char_len * 2
        if byte_len <= 0:
            continue
        if byte_len > MAX_TEXT_BYTES:
            byte_len = MAX_TEXT_BYTES

        text_bytes = block[TEXT_OFF: TEXT_OFF + byte_len]

        # 严格按 UTF-16 LE 解码（不做 errors='replace'）
        text = text_bytes.decode('utf-16-le')
        # 统一换行
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        results.append((idx, text))

    return results, None

def write_back_to_file(src_path: str, dst_path: str, updates: dict):
    """
    updates: {chunk_index: new_text_str} new_text_str 已是 Python 字符串（包含可能的 \x00）
    将 src_path 的指定 chunk 写入 new_text_str，保存到 dst_path
    """
    with open(src_path, 'rb') as f:
        data = bytearray(f.read())

    if len(data) < 4:
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        with open(dst_path, 'wb') as out:
            out.write(data)
        return

    if REQUIRE_HEAD_0:
        head = int.from_bytes(data[0:4], 'little', signed=False)
        if head != 0:
            # 头不为0，保持原样复制
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            with open(dst_path, 'wb') as out:
                out.write(data)
            return

    total = len(data)
    chunk_count = total // CHUNK_SIZE

    for idx, new_text in updates.items():
        if not (0 <= idx < chunk_count):
            print(f"  [警告] 索引越界: {src_path} chunk {idx}")
            continue

        base = idx * CHUNK_SIZE
        block = memoryview(data)[base: base + CHUNK_SIZE]

        type_val = int.from_bytes(block[TYPE_OFF:TYPE_OFF+4], 'little', signed=False)
        if type_val != TEXT_TYPE:
            print(f"  [警告] 非文本块: {src_path} chunk {idx} type={type_val}，跳过回写")
            continue

        # 严格编码并适配长度
        enc, char_len = encode_utf16le_fit(new_text, MAX_TEXT_BYTES)

        # 写长度
        block[LEN_OFF:LEN_OFF+4] = char_len.to_bytes(4, 'little', signed=False)

        # 清空文本区并写入
        block[TEXT_OFF:TEXT_OFF+MAX_TEXT_BYTES] = b'\x00' * MAX_TEXT_BYTES
        block[TEXT_OFF:TEXT_OFF+len(enc)] = enc

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(dst_path, 'wb') as out:
        out.write(data)

def extract_mode(in_dir: str, out_dir: str, dedup: bool):
    all_entries = []  # [(relpath, chunk_idx, text_str)]
    any_file = False

    for root, _dirs, files in os.walk(in_dir):
        for fn in files:
            any_file = True
            in_path = os.path.join(root, fn)
            rel_path = os.path.relpath(in_path, in_dir)
            try:
                texts, reason = extract_texts_from_file(in_path)
            except Exception as e:
                print(f"处理失败: {rel_path}，错误：{e}")
                continue

            if reason == 'head_not_zero':
                print(f"跳过(前4字节!=0): {rel_path}")
                continue
            if reason == 'too_short':
                print(f"跳过(文件过短): {rel_path}")
                continue

            for idx, text in texts:
                all_entries.append((rel_path, idx, text))

    if not any_file:
        print("提示：未在输入文件夹及其子文件夹中找到任何文件。")
        return

    os.makedirs(out_dir, exist_ok=True)
    all_txt_path = os.path.join(out_dir, 'all.txt')
    line_txt_path = os.path.join(out_dir, 'line.txt')

    total_lines = 0
    if dedup:
        buckets = OrderedDict()  # text_str -> list[(relpath, idx)]
        for rel, idx, text in all_entries:
            if text not in buckets:
                buckets[text] = []
            buckets[text].append((rel, idx))
        with open(all_txt_path, 'w', encoding='utf-8', newline='\n') as fa, \
             open(line_txt_path, 'w', encoding='utf-8', newline='\n') as fl:
            for text, pairs in buckets.items():
                fa.write(to_visible_line(text) + '\n')
                fl.write(' | '.join([f"{rel}\t{idx}" for rel, idx in pairs]) + '\n')
                total_lines += 1
    else:
        with open(all_txt_path, 'w', encoding='utf-8', newline='\n') as fa, \
             open(line_txt_path, 'w', encoding='utf-8', newline='\n') as fl:
            for rel, idx, text in all_entries:
                fa.write(to_visible_line(text) + '\n')
                fl.write(f"{rel}\t{idx}\n")
                total_lines += 1

    print(f"完成：写出 {total_lines} 行 -> {all_txt_path} 和 {line_txt_path}")

def writeback_mode(in_dir: str, out_dir: str):
    cwd = os.getcwd()
    all_txt_path = os.path.join(cwd, 'all.txt')
    line_txt_path = os.path.join(cwd, 'line.txt')

    if not os.path.isfile(all_txt_path) or not os.path.isfile(line_txt_path):
        print("错误：当前目录下缺少 all.txt 或 line.txt")
        sys.exit(1)

    with open(all_txt_path, 'r', encoding='utf-8', newline=None) as fa:
        all_lines = [line.rstrip('\n') for line in fa.readlines()]
    with open(line_txt_path, 'r', encoding='utf-8', newline=None) as fl:
        line_lines = [line.rstrip('\n') for line in fl.readlines()]

    if len(all_lines) != len(line_lines):
        print(f"错误：all.txt({len(all_lines)}) 与 line.txt({len(line_lines)}) 行数不一致")
        sys.exit(1)

    # 构建回写映射：relpath -> {chunk_idx: text_str}
    updates_by_file = defaultdict(dict)
    for i, (visible, map_line) in enumerate(zip(all_lines, line_lines), start=1):
        text = from_visible_line(visible)

        # 解析 line.txt 同行
        pairs = []
        for seg in [seg.strip() for seg in map_line.split('|')]:
            if not seg:
                continue
            if '\t' not in seg:
                print(f"  [警告] 第{i}行格式异常，缺少制表符分隔(relpath\\tidx)：{seg}")
                continue
            rel, idx_str = seg.split('\t', 1)
            rel = rel.strip()
            try:
                idx = int(idx_str.strip())
            except ValueError:
                print(f"  [警告] 第{i}行块索引不是整数：{idx_str}")
                continue
            pairs.append((rel, idx))

        for rel, idx in pairs:
            updates_by_file[rel][idx] = text  # 同一处多次出现，以最后一次为准

    processed_files = set()
    for root, _dirs, files in os.walk(in_dir):
        for fn in files:
            in_path = os.path.join(root, fn)
            rel_path = os.path.relpath(in_path, in_dir)
            out_path = os.path.join(out_dir, rel_path)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            if rel_path in updates_by_file:
                print(f"回写: {rel_path} -> {os.path.relpath(out_path, out_dir)} ({len(updates_by_file[rel_path])} 处)")
                write_back_to_file(in_path, out_path, updates_by_file[rel_path])
                processed_files.add(rel_path)
            else:
                shutil.copy2(in_path, out_path)

    for rel in updates_by_file.keys():
        if rel not in processed_files:
            print(f"  [警告] 未找到文件（未回写）：{rel}")

    print(f"回写完成：输出到 {out_dir}")

def main():
    if len(sys.argv) < 2:
        print("用法：")
        print("  提取：python convert_bin.py -e <输入文件夹> <输出文件夹> [-m]")
        print("  回写：python convert_bin.py -w <输入文件夹> <输出文件夹>")
        sys.exit(1)

    mode = sys.argv[1].lower()
    if mode == '-e':
        if len(sys.argv) < 4:
            print("用法：python convert_bin.py -e <输入文件夹> <输出文件夹> [-m]")
            sys.exit(1)
        in_dir = os.path.abspath(sys.argv[2])
        out_dir = os.path.abspath(sys.argv[3])
        dedup = ('-m' in sys.argv[4:])
        if not os.path.isdir(in_dir):
            print(f"错误：输入路径不是文件夹：{in_dir}")
            sys.exit(1)
        extract_mode(in_dir, out_dir, dedup)
    elif mode == '-w':
        if len(sys.argv) < 4:
            print("用法：python convert_bin.py -w <输入文件夹> <输出文件夹>")
            sys.exit(1)
        in_dir = os.path.abspath(sys.argv[2])
        out_dir = os.path.abspath(sys.argv[3])
        if not os.path.isdir(in_dir):
            print(f"错误：输入路径不是文件夹：{in_dir}")
            sys.exit(1)
        writeback_mode(in_dir, out_dir)
    else:
        print("错误：未知模式。使用 -e 进行提取，-w 进行回写。")
        sys.exit(1)

if __name__ == "__main__":
    main()
"""
文件溯源工具：读取带水印的文件，查出是谁下载的
用法：python trace.py <文件路径>
"""
import sys
import os
import json
import sqlite3
from PIL import Image


def read_lsb_watermark(image_path: str) -> str:
    """从 PNG 图片里读出 LSB 隐写术水印。"""
    img = Image.open(image_path)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    pixels = img.load()
    width, height = img.size

    binary = ""
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            binary += str(r & 1)
            binary += str(g & 1)
            binary += str(b & 1)
            # 每 8 位转一个字符
            if len(binary) >= 8:
                char = chr(int(binary[:8], 2))
                binary = binary[8:]
                # 检查结束标记
                if char == '\x00':
                    break
                # 检查是不是 watermark 开头
                if binary.startswith('1111111111111110'):
                    break
        else:
            continue
        break

    # 重新正确读取
    return _read_lsb_correct(image_path)


def _read_lsb_correct(image_path: str) -> str:
    """正确读取 LSB 水印。"""
    img = Image.open(image_path)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    pixels = img.load()
    width, height = img.size

    bits = []
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            bits.append(r & 1)
            bits.append(g & 1)
            bits.append(b & 1)
            # 检查结束标记（16位：1111111111111110）
            if len(bits) >= 16:
                last16 = ''.join(map(str, bits[-16:]))
                if last16 == '1111111111111110':
                    bits = bits[:-16]
                    break
        else:
            continue
        break

    # 每 8 位转一个字符
    message = ""
    for i in range(0, len(bits) - len(bits) % 8, 8):
        byte = bits[i:i+8]
        char = chr(int(''.join(map(str, byte)), 2))
        # 只保留可打印字符，遇到非可打印字符就停
        if ord(char) < 32 or ord(char) > 126:
            break
        message += char

    return message


def read_watermark(file_path: str) -> str:
    """读取文件里的水印，返回用户ID。"""
    ext = os.path.splitext(file_path)[1].lower()

    if ext in ['.png', '.bmp', '.tiff']:
        # 图片：读 LSB
        wm = _read_lsb_correct(file_path)
        if wm.startswith('user_'):
            return wm.replace('user_', '')
        return None

    elif ext == '.json':
        # JSON：读 _watermark 字段
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if '_watermark' in data:
                return data['_watermark'].replace('user_', '')
        except:
            return None

    elif ext in ['.txt', '.md', '.html', '.xml', '.yml', '.yaml']:
        # 文本：找末尾的注释
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            # 找 watermark: user_xxxxx
            import re
            match = re.search(r'watermark:\s*user_(\d+)', content)
            if match:
                return match.group(1)
        except:
            return None

    return None


def lookup_user(user_id: str):
    """查数据库里的领取记录。"""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''
        SELECT r.file_id, f.filename, f.version, r.claimed_at
        FROM claims r
        JOIN resource_files f ON r.file_id = f.file_id
        WHERE r.user_id = ?
        ORDER BY r.claimed_at DESC
        LIMIT 10
    ''', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法：python trace.py <文件路径>")
        sys.exit(1)

    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"文件不存在：{file_path}")
        sys.exit(1)

    print(f"🔍 正在读取水印：{file_path}")
    user_id = read_watermark(file_path)

    if not user_id:
        print("❌ 没找到水印，这个文件可能不是从本社区下载的，或者水印已被破坏。")
        sys.exit(0)

    print(f"✅ 找到用户ID：{user_id}")
    print(f"📋 该用户最近的领取记录：")
    print("-" * 60)
    records = lookup_user(user_id)
    if not records:
        print("（数据库里没有领取记录）")
    else:
        for i, (file_id, filename, version, claimed_at) in enumerate(records, 1):
            print(f"{i}. {claimed_at}")
            print(f"   文件：{filename}（版本：{version}）")
            print(f"   记录ID：{file_id}")
    print("-" * 60)
    print(f"💡 去 Discord 开发者工具或服务器成员列表，查这个用户ID对应的账号。")

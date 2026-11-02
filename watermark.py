"""
文件水印模块：给用户下载的文件加上隐形溯源标记
- 图片（PNG）：LSB 隐写术，藏在像素里，肉眼看不见
- 文本/JSON：末尾加隐藏注释 / JSON 加不影响使用的字段
- 所有文件：文件名里加用户ID
"""
import os
import json
import tempfile
from PIL import Image


def _lsb_encode(image_path: str, message: str, output_path: str):
    """把消息藏进 PNG 图片的像素最低位（LSB 隐写术）。"""
    img = Image.open(image_path)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    pixels = img.load()
    width, height = img.size

    # 把消息转成二进制
    binary = ''.join([format(ord(c), '08b') for c in message])
    # 加结束标记
    binary += '1111111111111110'

    idx = 0
    for y in range(height):
        for x in range(width):
            if idx >= len(binary):
                break
            r, g, b = pixels[x, y]
            # 修改红色通道的最低位
            if idx < len(binary):
                r = (r & 0xFE) | int(binary[idx])
                idx += 1
            if idx < len(binary):
                g = (g & 0xFE) | int(binary[idx])
                idx += 1
            if idx < len(binary):
                b = (b & 0xFE) | int(binary[idx])
                idx += 1
            pixels[x, y] = (r, g, b)
        if idx >= len(binary):
            break

    img.save(output_path, 'PNG')


def _add_text_watermark(file_path: str, user_id: str, output_path: str):
    """给文本文件加隐藏水印（末尾加一行注释）。"""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    watermark = f"\n<!-- watermark: user_{user_id} -->\n"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content + watermark)


def _add_json_watermark(file_path: str, user_id: str, output_path: str):
    """给 JSON 文件加不影响使用的水印字段。"""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        data = json.load(f)
    data['_watermark'] = f"user_{user_id}"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_watermark(original_path: str, user_id: str, original_filename: str) -> tuple:
    """
    给文件加隐形水印。
    返回：(带水印的临时文件路径, 新文件名)
    """
    # 新文件名：原名_用户ID.扩展名
    name, ext = os.path.splitext(original_filename)
    new_filename = f"{name}_user{user_id}{ext}"

    # 生成临时文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    tmp.close()
    output_path = tmp.name

    ext_lower = ext.lower()

    if ext_lower in ['.png', '.bmp', '.tiff']:
        # 图片：LSB 隐写术
        _lsb_encode(original_path, f"user_{user_id}", output_path)
    elif ext_lower in ['.json']:
        # JSON：加隐藏字段
        try:
            _add_json_watermark(original_path, user_id, output_path)
        except:
            # JSON 解析失败，当文本处理
            _add_text_watermark(original_path, user_id, output_path)
    elif ext_lower in ['.txt', '.md', '.html', '.xml', '.yml', '.yaml']:
        # 文本文件：末尾加注释
        _add_text_watermark(original_path, user_id, output_path)
    else:
        # 其他格式（JPG、MP3 等）：直接复制原文件，只靠文件名水印
        import shutil
        shutil.copy2(original_path, output_path)

    return output_path, new_filename

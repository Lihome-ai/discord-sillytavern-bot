"""
文件水印模块：给用户下载的文件加上隐形溯源标记
- 所有文件：直接复制原文件，不修改内容，绝对不破坏文件
- 文件名：保持不变
- 溯源：靠数据库记录
"""
import os
import tempfile
import shutil


def add_watermark(original_path: str, user_id: str, original_filename: str) -> tuple:
    """
    给文件加隐形水印。
    返回：(带水印的临时文件路径, 新文件名)
    """
    # 文件名：保持不变
    new_filename = original_filename

    # 生成临时文件，直接复制原文件
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(original_filename)[1])
    tmp.close()
    output_path = tmp.name

    # 直接复制原文件，不修改任何内容！
    shutil.copy2(original_path, output_path)

    return output_path, new_filename

# -*- coding: utf-8 -*-
"""
打包脚本：生成单文件便携 exe（dist/漫画双页分割器.exe）。

用法：
    python build_exe.py

产物说明：
  - onefile：整个程序就是一个 exe，双击即用，别的电脑无需装 Python；
  - windowed：无黑色控制台窗口；
  - 配置（settings.json）存放在 exe 同目录，整个文件夹拷走即带走配置。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "漫画双页分割器"


def make_icon() -> str:
    """用 PIL 画一个简单图标（蓝底白色「分」字），省得维护二进制资源。"""
    from PIL import Image, ImageDraw, ImageFont
    path = os.path.join(HERE, "assets", "icon.ico")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), radius=56, fill=(47, 111, 235, 255))
    # 左右两半的白色书页 + 中间分割线，示意「双页分割」
    d.rounded_rectangle((48, 64, 120, 192), radius=10, fill=(255, 255, 255, 255))
    d.rounded_rectangle((136, 64, 208, 192), radius=10, fill=(255, 255, 255, 255))
    d.rectangle((124, 48, 132, 208), fill=(47, 111, 235, 255))
    img.save(path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    return path


def main() -> int:
    icon = make_icon()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",
        "--name", EXE_NAME,
        "--icon", icon,
        "--paths", HERE,
        # PySide6 插件按需收集，减小体积
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "PySide6.QtMultimedia",
        "main.py",
    ]
    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode == 0:
        out = os.path.join(HERE, "dist", EXE_NAME + ".exe")
        print(f"\n✔ 打包完成：{out}")
        if os.path.exists(out):
            print(f"  大小：{os.path.getsize(out) / 1048576:.1f} MB")
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())

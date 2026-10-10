# -*- coding: utf-8 -*-
"""
打包脚本：默认生成「文件夹版」目录 + 便于分发的 zip。

用法：
    python build_exe.py            # 文件夹版（推荐，兼容性最好）
    python build_exe.py --onefile  # 额外再打一个单文件版

为什么默认用文件夹版（onedir）：
  单文件版（onefile）每次运行都要把自身解压到 %TEMP%\_MEIxxxx ——
  在部分机器上会被安全策略拦掉，报 "Could not create temporary directory!"。
  文件夹版直接从 exe 旁边的 _internal 目录加载，完全不解压临时目录，
  启动也更快（实测省 1~3 秒），代价是必须整个文件夹一起拷贝。
"""
import os
import re
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXE_NAME = "漫画双页分割器"

COMMON = [
    "--noconfirm", "--clean", "--windowed",
    "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.Qt3DCore",
    "--exclude-module", "PySide6.QtCharts",
    "--exclude-module", "PySide6.QtMultimedia",
]


def make_icon() -> str:
    """用 PIL 画一个图标（蓝底白色双页 + 中缝），省得维护二进制资源。"""
    from PIL import Image, ImageDraw
    path = os.path.join(HERE, "assets", "icon.ico")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), radius=56, fill=(47, 111, 235, 255))
    d.rounded_rectangle((48, 64, 120, 192), radius=10, fill=(255, 255, 255, 255))
    d.rounded_rectangle((136, 64, 208, 192), radius=10, fill=(255, 255, 255, 255))
    d.rectangle((124, 48, 132, 208), fill=(47, 111, 235, 255))
    img.save(path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    return path


def version() -> str:
    src = open(os.path.join(HERE, "manga_splitter", "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__ = "([^"]+)"', src).group(1)


def build(onefile: bool, icon: str) -> int:
    mode = ["--onefile"] if onefile else ["--onedir"]
    name = EXE_NAME + ("-单文件版" if onefile else "")
    cmd = [sys.executable, "-m", "PyInstaller", *mode, *COMMON,
           "--name", name, "--icon", icon, "--paths", HERE, "main.py"]
    print(" ".join(cmd))
    return subprocess.run(cmd, cwd=HERE).returncode


def make_zip(folder: str, ver: str) -> str:
    """把文件夹版打成 zip（内容在压缩包根目录，便于更新时直接解压）。"""
    out = os.path.join(HERE, "dist", f"MangaSplitter-{ver}-win64.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, folder))
    return out


def main() -> int:
    icon = make_icon()
    ver = version()
    wants_onefile = "--onefile" in sys.argv

    print(f"=== 打包 {EXE_NAME} v{ver}（文件夹版）===")
    if build(False, icon) != 0:
        return 1
    folder = os.path.join(HERE, "dist", EXE_NAME)
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _d, fs in os.walk(folder) for f in fs) / 1048576
    zp = make_zip(folder, ver)
    print(f"\n✔ 文件夹版：{folder}  ({size:.1f} MB)")
    print(f"✔ 分发包：{zp}  ({os.path.getsize(zp) / 1048576:.1f} MB)")
    print("   分发/拷贝请用这个 zip（解压后双击里面的 exe 即可）")

    if wants_onefile:
        print("\n=== 额外打包单文件版 ===")
        if build(True, icon) != 0:
            return 1
        print(f"✔ 单文件版：{os.path.join(HERE, 'dist', EXE_NAME + '-单文件版.exe')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""开发 / 打包共用入口：python main.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from manga_splitter.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

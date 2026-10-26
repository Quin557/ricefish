"""启动入口。

用法：
    python run.py               # 正常启动
    python run.py --dry-run     # 试运行，不真的发消息
    python run.py --check       # 只自检
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 保证相对路径（config/、data/）都相对项目根目录
os.chdir(ROOT)

from ricefish.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

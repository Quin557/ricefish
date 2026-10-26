"""测试用临时目录：绕开 Python tempfile 在受限令牌下的 0700 DACL。

普通 tempfile.mkdtemp() 创建的目录只有当前用户可写；DSH 的沙箱令牌
访问不了，测试会报 PermissionError。这里用 os.makedirs(mode=0o777)
在项目 .tmp/ 下创建，CI 和本地沙箱都能用。
"""

from __future__ import annotations

import os
import shutil
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, ".tmp")


def make_temp_dir(prefix: str = "ricefish-") -> str:
    os.makedirs(BASE, exist_ok=True)
    for _ in range(100):
        path = os.path.join(BASE, prefix + uuid.uuid4().hex[:8])
        try:
            os.makedirs(path, mode=0o777)
            return path
        except FileExistsError:
            continue
    raise FileExistsError("could not create temp dir")


class TempDir:
    """尽量兼容 tempfile.TemporaryDirectory 的最小实现。"""

    def __init__(self, prefix: str = "ricefish-") -> None:
        self.name = make_temp_dir(prefix)

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, *exc) -> None:
        self.cleanup()

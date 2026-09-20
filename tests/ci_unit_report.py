# -*- coding: utf-8 -*-
"""把 unittest 的失败详情转成 GitHub annotation。

为什么需要它：公共仓库的 Actions **日志**要管理员权限才能下载，但 annotation
任何人都能通过 API 读到。所以在 CI 里把失败摘出来发成 `::error::`，
维护者不必登录也能知道挂在哪、为什么挂。

用法：
    python tests/ci_unit_report.py unit.log
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_ANNOTATIONS = 8


def esc(s: str, limit: int = 900) -> str:
    """GitHub workflow command 转义（% 必须最先处理）。"""
    s = s.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    return s[:limit]


def main() -> int:
    log_path = Path(sys.argv[1] if len(sys.argv) > 1 else "unit.log")
    if not log_path.exists():
        print("::error::找不到单元测试日志文件")
        return 0
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    heads = [i for i, l in enumerate(lines) if re.match(r"^(FAIL|ERROR): ", l)]
    print(f"共发现 {len(heads)} 个失败用例")

    if not heads:
        tail = [l for l in lines if l.strip()][-15:]
        print("::error::未识别到单个用例失败，整体输出尾部：" + esc(" | ".join(t.strip() for t in tail)))
        return 0

    for n, i in enumerate(heads[:MAX_ANNOTATIONS]):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        block = [l for l in lines[i:end] if l.strip()]
        # 关键行：用例名 + 最后的断言/异常信息
        key = [l.strip() for l in block if l.startswith(("FAIL:", "ERROR:"))][:1]
        detail = [l.strip() for l in block][-10:]
        msg = " || ".join(key + detail)
        print("::error::" + esc(msg))

    tail = [l.strip() for l in lines[-8:] if l.strip()]
    print("::error::单元测试汇总 || " + esc(" | ".join(tail), 600))
    return 0


if __name__ == "__main__":
    sys.exit(main())

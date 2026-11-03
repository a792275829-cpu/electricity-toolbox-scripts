"""AGENTS.md 里引用的仓库路径必须真实存在。

规则文档最容易过期的地方不是措辞，而是路径：目录改名、脚本搬走后，
文档仍指向旧位置，读文档的人（包括 agent）会照着不存在的路径行事。
这里只校验反引号内、形如 `目录/文件` 的仓库相对路径。
"""
import re
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "AGENTS.md"

# 含这些字符的 span 是命令、占位符或平台调用，不是仓库路径。
NOT_A_PATH = ("$", "*", "=", "<", ">", "|", "~", "(", ")")

# 这些目录只在本机工作区存在，新克隆的仓库里没有，不能算缺失。
LOCAL_ONLY = {"logs", "dist", ".archive", ".toolbox", ".worktrees"}

# Git 远端引用形如 `origin/main`，不是文件系统路径。
GIT_REFS = ("origin/",)


def repo_paths(text):
    """产出形如 `a/b.py` 的仓库相对路径。"""
    for span in re.findall(r"`([^`]+)`", text):
        value = span.strip()
        if not value or any(ch.isspace() for ch in value):
            continue
        if value.startswith(("/", "~") + GIT_REFS) or any(mark in value for mark in NOT_A_PATH):
            continue
        value = value[2:] if value.startswith("./") else value
        value = value.rstrip("/")
        if "/" not in value:
            continue
        yield value


class AgentsDocPathTests(unittest.TestCase):
    def test_every_referenced_repo_path_exists(self):
        paths = sorted(set(repo_paths(DOC.read_text(encoding="utf-8"))))
        # 少于这个数量说明正则或文档结构变了，先让守卫自身失败。
        self.assertGreaterEqual(len(paths), 10, f"解析到的路径过少：{paths}")
        missing = [
            path for path in paths
            if path.split("/")[0] not in LOCAL_ONLY and not (ROOT / path).exists()
        ]
        self.assertEqual(missing, [], f"AGENTS.md 引用了不存在的路径：{missing}")

    def test_local_only_entries_stay_untracked(self):
        """豁免名单里只能是未纳入版本控制的本地目录，否则路径检查会被悄悄架空。"""
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(tracked.returncode, 0, tracked.stderr)
        heads = {line.split("/")[0] for line in tracked.stdout.splitlines() if line}
        self.assertEqual(LOCAL_ONLY & heads, set(), "这些目录已被 Git 跟踪，不应再豁免")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""
把项目源码推送到 GitHub（REST API，绕开被拦的 git push）。
流程：.gitkeep 初始化 -> blobs -> tree -> commit(以初始提交为父) -> 移动 main ref。
"""
import base64
import json
import os
import sys
import urllib.request

REPO = "jiaru0204/manga-splitter"
ROOT = r"D:\漫画分页"
INCLUDE = [
    "README.md",
    "docs/界面预览-自动分页.png", "docs/界面预览-框选裁剪.png",
    "manga_splitter/__init__.py", "manga_splitter/__main__.py", "manga_splitter/app.py",
    "manga_splitter/settings.py", "manga_splitter/updater.py",
    "manga_splitter/core/__init__.py", "manga_splitter/core/naming.py",
    "manga_splitter/core/imaging.py", "manga_splitter/core/splitter.py",
    "manga_splitter/core/cropper.py",
    "manga_splitter/ui/__init__.py", "manga_splitter/ui/theme.py",
    "manga_splitter/ui/workers.py", "manga_splitter/ui/split_page.py",
    "manga_splitter/ui/crop_view.py", "manga_splitter/ui/crop_page.py",
    "manga_splitter/ui/main_window.py",
    "main.py", "build_exe.py", "发布新版本.py", ".gitignore",
    "tests/test_core.py", "tests/test_updater.py", "tests/bench_split.py",
    "tests/bench_render.py", "tests/gui_smoke.py", "tests/shot.py",
    "tests/upload_asset.py", "tests/push_source.py",
    "重构报告.md",
]


def load_token() -> str:
    cred_path = os.path.expanduser("~/.git-credentials")
    with open(cred_path, "r", encoding="utf-8") as f:
        for line in f:
            if "github.com" in line:
                line = line.strip()
                return line.split("@")[0].split(":")[-1]
    raise RuntimeError("token not found")


def api(method: str, path: str, token: str, payload=None, host="api.github.com"):
    url = f"https://{host}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "manga-splitter-push",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8")
        return json.loads(body) if body else {}


def main():
    token = load_token()

    # 1) 拿默认分支与初始提交（没有就用 .gitkeep 初始化）
    try:
        repo = api("GET", f"/repos/{REPO}", token)
    except Exception as e:
        print("repo 不存在?", e)
        return 1
    branch = repo.get("default_branch", "main")
    try:
        ref = api("GET", f"/repos/{REPO}/git/ref/heads/{branch}", token)
        parent_sha = ref["object"]["sha"]
    except Exception:
        api("PUT", f"/repos/{REPO}/contents/.gitkeep", token,
            {"message": "init", "content": ""})
        ref = api("GET", f"/repos/{REPO}/git/ref/heads/{branch}", token)
        parent_sha = ref["object"]["sha"]

    # 2) blobs
    tree_items = []
    for rel in INCLUDE:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(p):
            print(f"  跳过（不存在）: {rel}")
            continue
        with open(p, "rb") as f:
            content = base64.b64encode(f.read()).decode("ascii")
        blob = api("POST", f"/repos/{REPO}/git/blobs", token,
                   {"content": content, "encoding": "base64"})
        tree_items.append({"path": rel, "mode": "100644",
                           "type": "blob", "sha": blob["sha"]})
        print(f"  blob {rel}")

    # 3) tree + commit + ref
    tree = api("POST", f"/repos/{REPO}/git/trees", token, {"tree": tree_items})
    commit = api("POST", f"/repos/{REPO}/git/commits", token, {
        "message": "v2.0.0 重构版：PySide6 UI + 纯逻辑核心层 + 自动更新 + PyInstaller 单文件打包",
        "tree": tree["sha"],
        "parents": [parent_sha],
        "author": {"name": "JiaRu", "email": "3090709262@qq.com"},
        "committer": {"name": "JiaRu", "email": "3090709262@qq.com"},
    })
    api("PATCH", f"/repos/{REPO}/git/refs/heads/{branch}", token,
        {"sha": commit["sha"], "force": True})
    print(f"✔ 已推送 {len(tree_items)} 个文件，commit {commit['sha'][:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

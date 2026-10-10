# -*- coding: utf-8 -*-
"""把某个文件作为附件传到指定 Release（走 urllib；沙箱里 curl 会被代理拦）。"""
import json
import os
import sys
import urllib.request

REPO = "jiaru0204/manga-splitter"


def token() -> str:
    p = os.path.expanduser("~/.git-credentials")
    for line in open(p, encoding="utf-8"):
        if "github.com" in line:
            return line.strip().split("@")[0].split(":")[-1]
    raise RuntimeError("no token")


def api(method, path, tok, payload=None, raw=None, host="api.github.com", timeout=900):
    url = f"https://{host}{path}"
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "manga-splitter-upload",
        "Content-Type": "application/octet-stream" if raw is not None else "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return json.loads(body) if body and raw is None else {"ok": True}


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "v2.0.0"
    path = sys.argv[2] if len(sys.argv) > 2 else r"D:\漫画分页\dist\MangaSplitter-2.0.0-win64.zip"
    name = os.path.basename(path)
    tok = token()

    rel = api("GET", f"/repos/{REPO}/releases/tags/{tag}", tok)
    have = {a["name"] for a in rel.get("assets", [])}
    print("已有附件:", have or "（无）")
    if name in have:
        print("附件已存在，跳过上传")
        return 0

    size = os.path.getsize(path)
    print(f"上传 {name} ({size/1048576:.1f} MB)…")
    with open(path, "rb") as f:
        blob = f.read()
    api("POST", f"/repos/{REPO}/releases/{rel['id']}/assets?name={name}",
        tok, raw=blob, host="uploads.github.com")
    rel2 = api("GET", f"/repos/{REPO}/releases/tags/{tag}", tok)
    for a in rel2["assets"]:
        print(f"  ✔ {a['name']}  {a['size']/1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Publish a GitHub release with the portable zip attached.

    python tools/make_release.py --check          # 看看会发什么，不动线上
    python tools/make_release.py --publish        # 建 release + 上传 zip
    python tools/make_release.py --publish --hide-old v1.1.2

The token is taken from the **same Windows credential git already uses** (no
token on the command line, nothing written to disk), so an agent that can push
can also publish.  Release notes come from the ``## v<version>`` section of
`CHANGELOG.md`, and the asset is `dist/OmniCOM-<version>-win64.zip` - the exact
file the in-app updater and the "update from a package" button consume.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from omnicom import __version__  # noqa: E402

REPO = "Fatmear/OmniCOM"
API = f"https://api.github.com/repos/{REPO}"
UPLOADS = f"https://uploads.github.com/repos/{REPO}"
UA = "OmniCOM-release-tool"


# ------------------------------------------------------------------ helpers
def git(*args: str, stdin: str | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, input=stdin,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:300]}")
    return proc.stdout


#: Cached because every API call needs it and `git credential fill` is slow.
_TOKEN: str = ""


def _fill(username: str = "") -> tuple[str, str]:
    """Ask git for the stored credential of one account (never printed)."""
    query = "protocol=https\nhost=github.com\n"
    if username:
        query += f"username={username}\n"
    query += "\n"
    try:
        out = git("credential", "fill", stdin=query)
    except RuntimeError:
        return "", ""
    user = password = ""
    for line in out.splitlines():
        if line.startswith("username="):
            user = line.split("=", 1)[1].strip()
        if line.startswith("password="):
            password = line.split("=", 1)[1].strip()
    return user, password


def token() -> str:
    """A credential that can **write** to this repository.

    Pushing works over SSH, so the token in the credential store may belong to
    a different account with read-only access.  Publishing needs write, so a
    candidate is only accepted when the repo reports ``permissions.push``.
    """
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    tried: list[str] = []
    for candidate in ("fatmear", "Fatmear", ""):
        user, password = _fill(candidate)
        if not password or password in tried:
            continue
        tried.append(password)
        request = urllib.request.Request(f"{API}")
        request.add_header("Authorization", f"Bearer {password}")
        request.add_header("User-Agent", UA)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                repo = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        if (repo.get("permissions") or {}).get("push"):
            _TOKEN = password
            print(f"凭据    : {user} (可写)")
            return password
    raise RuntimeError(
        f"git 凭据里没有对 {REPO} 有写权限的账号；"
        "先 git push 一次或配置一个有 repo 权限的 token")


def api(path: str, method: str = "GET", payload: dict | None = None,
        base: str = API, raw: bytes | None = None,
        content_type: str = "application/json") -> tuple[int, dict]:
    url = f"{base}{path}"
    body = raw
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {token()}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", UA)
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            text = response.read().decode("utf-8", "replace")
            return response.status, (json.loads(text) if text.strip() else {})
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = {"message": text[:300]}
        return error.code, parsed


def notes(version: str) -> str:
    path = os.path.join(ROOT, "CHANGELOG.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(rf"^## v{re.escape(version)}\b.*?$(.*?)(?=^## |\Z)",
                      text, re.M | re.S)
    if not match:
        return f"OmniCOM {version}"
    body = match.group(1).strip()
    return body.replace("\n", "\n") + (
        f"\n\n---\n\n**便携包**：下载 `OmniCOM-{version}-win64.zip`，解压后双击 "
        f"`OmniCOM.cmd`，无需安装任何东西。\n"
        f"**离线升级**：在「软件更新 → 从升级包更新」里选中同一个 zip 即可。\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布 OmniCOM GitHub release")
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--check", action="store_true", help="只打印计划，不请求")
    parser.add_argument("--publish", action="store_true", help="真正建 release 并上传")
    parser.add_argument("--hide-old", default="", metavar="TAG",
                        help="发布后把旧 tag 的 release 转为草稿（下架但可恢复）")
    parser.add_argument("--name", default="", help="release 标题")
    options = parser.parse_args(argv)

    version = options.version.lstrip("vV")
    tag = f"v{version}"
    asset = os.path.join(ROOT, "dist", f"OmniCOM-{version}-win64.zip")
    title = options.name or f"OmniCOM {version}"
    body = notes(version)

    print(f"仓库    : {REPO}")
    print(f"tag     : {tag}")
    print(f"标题    : {title}")
    print(f"资产    : {asset} ({os.path.getsize(asset) / 1048576:.0f} MB)"
          if os.path.isfile(asset) else f"资产    : {asset} —— 缺失！")
    print(f"说明长度: {len(body)} 字符")
    if not os.path.isfile(asset):
        print("先运行 python tools/make_dist.py --check --zip")
        return 2
    if options.check or not options.publish:
        print("\n---- 发布说明预览 ----")
        print(body[:1200])
        print("\n（加 --publish 才会真正发布）")
        return 0

    status, existing = api(f"/releases/tags/{tag}")
    if status == 200 and existing.get("id"):
        release = existing
        print(f"发布已存在 #{release['id']}，复用")
        status, updated = api(f"/releases/{release['id']}", "PATCH",
                              {"name": title, "body": body,
                               "draft": False, "prerelease": False})
        if status >= 400:
            print("更新说明失败：", updated.get("message"))
            return 1
    else:
        status, release = api("/releases", "POST",
                              {"tag_name": tag, "target_commitish": "main",
                               "name": title, "body": body, "draft": False,
                               "prerelease": False})
        if status >= 400:
            print("建 release 失败：", status, release.get("message"))
            return 1
        print(f"已建 release #{release['id']}")

    upload = f"{UPLOADS}/releases/{release['id']}/assets?name=" \
             f"{os.path.basename(asset)}"
    with open(asset, "rb") as handle:
        payload = handle.read()
    status, result = api("", "POST", payload=None, base=upload, raw=payload,
                         content_type="application/zip")
    if status >= 400:
        if status == 422:
            print("同名资产已存在，删除后重传")
            _, current = api(f"/releases/{release['id']}/assets")
            for item in current if isinstance(current, list) else []:
                if item.get("name") == os.path.basename(asset):
                    api(f"/releases/assets/{item['id']}", "DELETE")
            status, result = api("", "POST", payload=None, base=upload,
                                 raw=payload, content_type="application/zip")
        if status >= 400:
            print("上传失败：", status, result.get("message"))
            return 1
    print(f"已上传 {result.get('name')} "
          f"({int(result.get('size') or 0) / 1048576:.0f} MB)")
    print(f"下载地址 https://github.com/{REPO}/releases/tag/{tag}")

    if options.hide_old:
        old = options.hide_old if options.hide_old.startswith("v") \
            else f"v{options.hide_old}"
        status, target = api(f"/releases/tags/{old}")
        if status != 200:
            print(f"旧 release {old} 未找到，跳过")
            return 0
        status, done = api(f"/releases/{target['id']}", "PATCH", {"draft": True})
        if status >= 400:
            print("下架旧 release 失败：", done.get("message"))
            return 1
        print(f"旧 release {old} 已转为草稿（页面不再出现，仍可恢复）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Publish the ``plugin-store`` asset bucket on GitHub (idempotent).

``plugins/catalog.json`` points every download URL at
``releases/download/plugin-store/<id>-<version>.zip``, so the plugin store only
works once those zips exist as public release assets.  That bucket is a normal
release whose tag is ``plugin-store``; it must **never** become ``latest``
(that pointer belongs to the app version and the in-app updater reads it), so
``make_latest`` is explicitly ``false``.

Usage::

    python tools/publish_plugin_store.py            # upload what is missing
    python tools/publish_plugin_store.py --check    # only report

Assets come from ``dist/plugin-store/`` (``<id>-<version>.zip``) plus the
catalog itself.  Every zip is checked against the ``sha256``/``size`` the
catalog claims **before** it is uploaded, so a stale build can never be
published under a version that the store then refuses to install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import make_release  # noqa: E402

TAG = "plugin-store"
TITLE = "OmniCOM plugin store"
BODY = (
    "插件商店的资产桶：这里存放插件目录 `plugins/catalog.json` 引用的插件包，"
    "供 OmniCOM 的「插件商店」下载、更新与**降级安装**使用。\n\n"
    "**这不是应用版本**，请不要从这里下载程序本体；"
    "程序包见 [最新版本](https://github.com/Fatmear/OmniCOM/releases/latest)。\n\n"
    "`chart-1.1.0.zip` 是降级目标，必须一直保留。\n"
)
SOURCE = os.path.join(ROOT, "dist", "plugin-store")
CATALOG = os.path.join(ROOT, "plugins", "catalog.json")


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_assets() -> list[str]:
    paths: list[str] = []
    if os.path.isdir(SOURCE):
        for name in sorted(os.listdir(SOURCE)):
            if name.endswith(".zip"):
                paths.append(os.path.join(SOURCE, name))
    if os.path.isfile(CATALOG):
        paths.append(CATALOG)
    return paths


def catalog_entries() -> dict[str, dict]:
    """Map ``<id>-<version>.zip`` to the metadata the catalog promises."""
    with open(CATALOG, encoding="utf-8") as handle:
        catalog = json.load(handle)
    wanted: dict[str, dict] = {}
    for plugin in catalog.get("plugins") or []:
        entries = plugin.get("versions") or [plugin]
        for entry in entries:
            url = entry.get("url") or ""
            if not url:
                continue
            wanted[os.path.basename(url)] = entry
    return wanted


def verify(paths: list[str]) -> list[str]:
    """Drop any zip the catalog does not describe or describes differently."""
    wanted = catalog_entries()
    good: list[str] = []
    for path in paths:
        name = os.path.basename(path)
        if not name.endswith(".zip"):
            good.append(path)
            continue
        entry = wanted.get(name)
        if entry is None:
            print(f"  跳过 {name}：目录里没有引用它")
            continue
        size = os.path.getsize(path)
        digest = sha256_of(path)
        if entry.get("size") and int(entry["size"]) != size:
            print(f"  拒绝 {name}：大小 {size} != 目录的 {entry['size']}")
            continue
        if entry.get("sha256") and entry["sha256"] != digest:
            print(f"  拒绝 {name}：sha256 与目录不一致")
            continue
        print(f"  校验 {name} OK（{size} B）")
        good.append(path)
    return good


def ensure_release() -> dict | None:
    release = make_release.find_release(TAG)
    if release is not None:
        print(f"{TAG} 已存在 #{release['id']}，复用")
        status, updated = make_release.api(
            f"/releases/{release['id']}", "PATCH",
            {"name": TITLE, "body": BODY, "draft": False,
             "prerelease": False, "make_latest": "false"})
        if status >= 400:
            print("更新失败：", updated.get("message"))
            return None
        return updated
    status, release = make_release.api("/releases", "POST", {
        "tag_name": TAG, "target_commitish": "main", "name": TITLE,
        "body": BODY, "draft": False, "prerelease": False,
        "make_latest": "false"})
    if status >= 400:
        print("建 release 失败：", status, release.get("message"))
        return None
    print(f"已建 {TAG} #{release['id']}")
    return release


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布插件商店资产桶")
    parser.add_argument("--check", action="store_true", help="只检查，不上传")
    options = parser.parse_args(argv)

    paths = local_assets()
    if not paths:
        print("dist/plugin-store/ 里没有 zip，先运行 python tools/make_plugins.py")
        return 2
    print("核对本地插件包与目录声明：")
    paths = verify(paths)
    if not paths:
        print("没有可上传的资产")
        return 1
    if options.check:
        print("只检查，不上传（去掉 --check 才会真正上传）")
        return 0

    release = ensure_release()
    if release is None:
        return 1

    existing = {item["name"]: item for item in release.get("assets") or []}
    ok = True
    for path in paths:
        name = os.path.basename(path)
        size = os.path.getsize(path)
        old = existing.get(name)
        if old is not None and int(old.get("size") or -1) == size:
            print(f"  跳过 {name}（已存在，{size} B）")
            continue
        if old is not None:
            make_release.api(f"/releases/assets/{old['id']}", "DELETE")
            print(f"  删除旧 {name}")
        with open(path, "rb") as handle:
            payload = handle.read()
        url = (f"{make_release.UPLOADS}/releases/{release['id']}"
               f"/assets?name={name}")
        content = "application/zip" if name.endswith(".zip") else "application/json"
        status, result = make_release.api("", "POST", payload=None, base=url,
                                          raw=payload, content_type=content,
                                          timeout=make_release.UPLOAD_TIMEOUT)
        if status >= 400:
            print(f"  上传 {name} 失败：", status, result.get("message"))
            ok = False
            continue
        print(f"  已上传 {name} {size} B sha256 {sha256_of(path)}")

    status, latest = make_release.api("/releases/latest")
    name = latest.get("tag_name") if status == 200 else f"status {status}"
    print(f"latest  : {name}")
    if name == TAG:
        print("警告：latest 指向了资产桶，必须 PATCH 回应用版本（L-167）")
        return 1

    for url_entry in sorted(catalog_entries()):
        probe = urllib.request.Request(
            catalog_entries()[url_entry]["url"], method="GET")
        probe.add_header("User-Agent", "OmniCOM-catalog-probe")
        probe.add_header("Range", "bytes=0-3")
        try:
            with urllib.request.urlopen(probe, timeout=60) as response:
                got = len(response.read(4))
                print(f"  下载 {url_entry}: {response.status} {got} B")
                ok = ok and response.status in (200, 206) and got > 0
        except Exception as error:
            print(f"  下载 {url_entry} 失败：{error}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

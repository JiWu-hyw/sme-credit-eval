"""把 deliverables/ 下各目录打成一个完整交付 zip（含数据库）。

分两个包：
    wenlv_full_delivery_YYYYMMDD.zip   全量，含 05_数据库（约 60–70 MB）
    wenlv_delivery_YYYYMMDD.zip        轻量，不含库（约 210 KB），微信/邮件能发

为什么分两个：104 MB 的库压完还有几十 MB，很多聊天工具发不出去；但接收方
真要自己写 SQL 又必须有库。所以两者并存，文件名上区分清楚。
"""
from __future__ import annotations

import hashlib
import os
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "deliverables")

LIGHT_PARTS = ["00_先看这个.md", "01_算法端", "02_前端", "03_交付说明",
               "04_数据字典与数据集"]
FULL_PARTS = LIGHT_PARTS + ["05_数据库"]


def _collect(parts):
    files = []
    for p in parts:
        full = os.path.join(OUT, p)
        if not os.path.exists(full):
            continue
        if os.path.isfile(full):
            files.append((p, full))
        else:
            for dp, _, fns in os.walk(full):
                for fn in sorted(fns):
                    src = os.path.join(dp, fn)
                    rel = os.path.relpath(src, OUT).replace(os.sep, "/")
                    files.append((rel, src))
    return files


def pack(zip_name: str, parts, compresslevel: int = 9) -> dict:
    files = _collect(parts)
    zp = os.path.join(OUT, zip_name)
    if os.path.exists(zp):
        os.remove(zp)

    # 先算校验，再写 zip；MANIFEST 里带 sha256，接收方可验证传输没坏
    manifest = ["交付包清单  " + zip_name,
                "生成时间  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "文件数    %d" % len(files), "",
                "sha256  %-12s  %s" % ("大小(字节)", "路径"), "-" * 96]
    for rel, src in files:
        h = hashlib.sha256()
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        manifest.append("%s  %-12d  %s" % (h.hexdigest(), os.path.getsize(src), rel))

    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as zf:
        for rel, src in files:
            zf.write(src, rel)
        zf.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")

    return {"path": zp, "n": len(files) + 1,
            "mb": round(os.path.getsize(zp) / 1024 / 1024, 2),
            "bad": zipfile.ZipFile(zp).testzip()}


if __name__ == "__main__":
    stamp = datetime.now().strftime("%Y%m%d")
    for name, parts in [("wenlv_delivery_%s.zip" % stamp, LIGHT_PARTS),
                        ("wenlv_full_delivery_%s.zip" % stamp, FULL_PARTS)]:
        r = pack(name, parts)
        print("📦 %-38s %3d 个文件  %8.2f MB  testzip=%s"
              % (os.path.basename(r["path"]), r["n"], r["mb"], r["bad"]))

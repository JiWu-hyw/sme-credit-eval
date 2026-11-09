# -*- coding: utf-8 -*-
"""SHAP 方向语义守卫。

防的是：v1.9 把标签方向纠正成「1=好企业 / P(好企业)」之后，
前端 P5 原型契约仍按「+1=推高违约概率」展示，且没有任何声明，
算法端把 mock 里的因子方向当成实测信用方向引用。

同时验证 batch_evidence.json 的 trace 路径基准（前端 extracted 根目录），
避免后人从仓库根去判断、误报「悬空引用」又去造一份假契约。

用法：python tests/test_shap_direction_notice.py
"""
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FE = os.path.join(ROOT, "src", "app", "frontend", "extracted")
CONTRACTS = os.path.join(FE, "outputs", "contracts")
SHAP_JSONS = (
    "shap_global.json", "shap_local.json",
    "shap_local_label_pending.json", "shap_local_sparse.json",
)
API_TS = os.path.join(FE, "src", "api", "index.ts")
NOTICE = os.path.join(ROOT, "outputs", "shap_legacy_notice.md")
LEGACY = os.path.join(ROOT, "outputs", "shap_global.json")
CURRENT = os.path.join(ROOT, "outputs", "shap_credit_direction.json")
EVIDENCE = os.path.join(CONTRACTS, "batch_evidence.json")

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))
    except Exception as e:                       # noqa: BLE001
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  ✗ %s —— %s: %s" % (name, type(e).__name__, e))


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def main():

    def t_all_contracts_valid():
        for fn in SHAP_JSONS:
            p = os.path.join(CONTRACTS, fn)
            assert os.path.exists(p), "缺契约 %s" % fn
            d = load(p)
            assert set(d) >= {"meta", "data"}, "%s 结构异常" % fn

    def t_direction_fields():
        for fn in SHAP_JSONS:
            m = load(os.path.join(CONTRACTS, fn))["meta"]
            assert m.get("direction_notice", "").startswith("v1"), (
                "%s 缺 direction_notice=v1" % fn)
            assert "PD 风险方向" in m.get("label_direction", ""), (
                "%s 未标明 PD 风险方向" % fn)
            assert "shap_credit_direction.json" in m.get("current_shap_entry", ""), (
                "%s 未指向当前口径产物" % fn)

    def t_original_semantics_preserved():
        """注入只加字段，不得把旧的 PD 措辞洗掉或改数值。"""
        g = load(os.path.join(CONTRACTS, "shap_global.json"))
        assert "风险因子" in g["meta"].get("disclaimer", ""), (
            "旧 disclaimer 被改写（会掩盖真实方向）")
        assert g["meta"].get("source") == "measured", "meta.source 被改动"
        assert g["meta"].get("semantic_known") is False, "meta.semantic_known 被改动"
        assert "models" in g["data"] and "conclusion" in g["data"], (
            "data 结构被改动")
        loc = load(os.path.join(CONTRACTS, "shap_local.json"))
        assert loc["meta"].get("contract_status") == "mock", "局部契约状态被改动"
        assert loc["meta"].get("label_ref") is None, "label_ref 不应被编造"
        assert loc["meta"].get("redline", {}).get("no_mixed_dataset") is True, (
            "A/B 不混用红线被改动")

    def t_inject_idempotent():
        before = {fn: hashlib.sha256(
            open(os.path.join(CONTRACTS, fn), "rb").read()).hexdigest()
            for fn in SHAP_JSONS}
        p = subprocess.run(
            [sys.executable, "-m", "src.data.inject_shap_direction_notice"],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, "注入脚本执行失败：%s" % p.stdout.strip()[-300:]
        assert "0/4" in p.stdout, "重复执行仍在改写文件（非幂等）"
        after = {fn: hashlib.sha256(
            open(os.path.join(CONTRACTS, fn), "rb").read()).hexdigest()
            for fn in SHAP_JSONS}
        assert before == after, "重复注入改变了文件字节"

    def t_api_comment():
        s = read(API_TS)
        assert "PD 风险方向" in s and "shap_credit_direction" in s, (
            "P5 接口注释缺方向警告")
        assert "禁止互引数值" in s, "P5 接口注释缺禁止混用声明"

    def t_notice_doc():
        s = read(NOTICE)
        for key in ("前端原型契约", "shap_local_label_pending.json", "路径基准",
                    "src/app/frontend/extracted/", "batch_evidence.json"):
            assert key in s, "shap_legacy_notice.md 缺要点：%s" % key

    def t_legacy_and_current():
        leg = load(LEGACY)
        assert "_direction_warning" in leg, "旧 shap_global.json 缺内嵌方向警告"
        cur = load(CURRENT)
        blob = json.dumps(cur, ensure_ascii=False)
        assert "P(好企业)" in blob or "credit" in blob.lower(), (
            "当前 SHAP 产物未标明 P(好企业) 口径")

    def t_evidence_path_baseline():
        """trace 路径按前端根目录解析必须全部存在；按仓库根解析不作要求。"""
        ev = load(EVIDENCE)
        s = json.dumps(ev, ensure_ascii=False)
        refs = set(re.findall(r'"local_contract":\s*"([^"]+)"', s))
        assert refs, "batch_evidence.json 无 local_contract 字段"
        missing = [r for r in refs
                   if not os.path.exists(os.path.join(FE, r)) and
                   not os.path.exists(os.path.join(ROOT, r))]
        assert not missing, "契约路径在两种基准下都不存在：%s" % missing[:3]
        fe_only = [r for r in refs if os.path.exists(os.path.join(FE, r))]
        assert fe_only, "没有任何路径按前端根目录解析成功（路径基准变了）"
        assert read(NOTICE).count("前端根目录") >= 1, "文档未写明正确路径基准"

    for name, fn in [
        ("4 份前端 SHAP 契约均为合法 JSON", t_all_contracts_valid),
        ("方向声明三字段齐全", t_direction_fields),
        ("旧 PD 语义 / 数值 / 红线未被改写", t_original_semantics_preserved),
        ("注入脚本幂等（重复执行零改写）", t_inject_idempotent),
        ("P5 接口注释含方向警告", t_api_comment),
        ("说明文档含前端契约与路径基准", t_notice_doc),
        ("旧产物警告 + 当前口径产物齐备", t_legacy_and_current),
        ("batch_evidence 路径基准验证", t_evidence_path_baseline),
    ]:
        check(name, fn)

    print("\n%d 项通过 / %d 项失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  FAIL %s: %s" % (n, e))
        return 1
    print("全绿 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

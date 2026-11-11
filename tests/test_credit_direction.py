# -*- coding: utf-8 -*-
"""P1-续守卫：标签方向纠正后的 as-of 训练 / AUC 恒等式 / SHAP / 分队列复核。

只读校验，不训练、不改库、不产出交付包。校验对象：
  - 库：v_model_train_ready_credit 与旧视图逐行互补，286 正 / 21 负；
  - outputs/credit_direction_asof.json：同一打分翻标签 → AUC 互为 1−AUC（严格恒等式）；
    对称配置重训练 → p_credit = 1−p_risk、两方向 AUC **相等**（不是互补）；
    balanced 超参陷阱已被记录；分切面 / 分队列复核结果齐备；
  - outputs/shap_credit_direction.*：方向语义为 P(好企业)，+1=增信因子；
    旧 shap_global.json 保持历史口径未被覆盖。

用法：python tests/test_credit_direction.py
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "db", "zjtech_credit.db")
OUT = os.path.join(ROOT, "outputs")
RES = os.path.join(OUT, "credit_direction_asof.json")
REPORT = os.path.join(OUT, "credit_direction_asof_report.md")
SHAP = os.path.join(OUT, "shap_credit_direction.json")
SHAP_CSV = os.path.join(OUT, "shap_credit_direction.csv")
SHAP_PNG = os.path.join(OUT, "shap_credit_direction.png")
OLD_SHAP = os.path.join(OUT, "shap_global.json")
SCRIPT = os.path.join(ROOT, "src", "model", "baseline_credit_direction.py")

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, repr(e)))
        print("  ✗ %s —— %r" % (name, e))


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def con_ro():
    return sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)


def main():
    print("=" * 68)
    print("  P1-续 方向纠正 / AUC 恒等式 / SHAP 守卫")
    print("=" * 68)
    # outputs/ 被 .gitignore 忽略（干净检出常见）；缺产物时按项目约定 SKIP，
    # 不把「未生成本地产物」误报成守卫失败。库口径由 test_multilabel_eval.py 覆盖。
    if not os.path.exists(RES):
        print("  ⚠ 缺 %s —— outputs/ 产物被 .gitignore 忽略（干净检出常见）" % RES)
        print("    先跑 <带科学栈的 py39> -m src.model.baseline_credit_direction，本次 SKIP")
        sys.exit(0)
    R = load(RES)
    S = load(SHAP)
    report = read(REPORT)
    c = con_ro()
    one = lambda s: c.execute(s).fetchone()[0]

    # ---------------- A. 库口径 ----------------
    def a_ver():
        # v2.0 只新增 A 轴司法/人员三表四视图（并列候选层），
        # 未改 ads_label / 方向映射视图 / 训练入口，故 v1.9 与 v2.0 都合规。
        ver = one("SELECT version FROM meta_schema_version "
                  "ORDER BY applied_at DESC LIMIT 1")
        assert ver in ("v1.9", "v2.0"), "库版本 %s 不在方向纠正后的允许范围" % ver
        assert one("SELECT COUNT(*) FROM v_model_train_ready_credit") == 307, "训练视图不是 307 行"

    def a_complement():
        n, bad, pos_new, pos_old = c.execute(
            "SELECT COUNT(*), SUM(CASE WHEN a.label = 1-b.label THEN 0 ELSE 1 END), "
            "SUM(a.label), SUM(b.label) FROM v_model_train_ready_credit a "
            "JOIN v_model_train_ready b USING(enterprise_id)"
        ).fetchone()
        assert n == 307 and bad == 0, "新旧标签未逐行互补（bad=%r）" % bad
        assert (pos_new, pos_old) == (286, 21), "正例数异常 %r" % ((pos_new, pos_old),)

    def a_direction_view():
        got = c.execute("SELECT credit_label, COUNT(*) FROM v_label_direction "
                        "GROUP BY 1 ORDER BY 1").fetchall()
        assert got == [(0, 21), (1, 286)], "方向视图分布异常 %r" % (got,)

    def a_asof_policy():
        got = dict(c.execute(
            "SELECT as_of_policy, COUNT(*) FROM v_model_dataset_asof_flat "
            "WHERE label_value IS NOT NULL GROUP BY 1").fetchall())
        assert got == {"cohort": 307, "due_minus_1": 307}, "两套切面行数异常 %r" % (got,)
        assert one("SELECT SUM(label_value) FROM v_model_dataset_asof_flat "
                   "WHERE as_of_policy='cohort'") == 21, "as-of 视图旧方向标签被改动"

    check("库 v1.9 / 正确方向训练视图 307 行", a_ver)
    check("新旧标签逐行互补，286 正 / 21 负", a_complement)
    check("v_label_direction 分布 [(0,21),(1,286)]", a_direction_view)
    check("as-of 两套切面各 307、旧底表口径未改", a_asof_policy)

    # ---------------- B. AUC 恒等式 ----------------
    def b_meta():
        assert R["schema_version"] == "v1.9", "结果 schema_version 不对"
        assert R["quick_mode"] is False, "这是 --quick 冒烟结果，不可作为交付依据"
        assert R["direction"]["label_rule"] == "credit_label = 1 - ads_label.label_value"
        assert R["protocol"]["verification_weighting"].startswith("symmetric")

    def b_flip():
        f = R["flip_identity"]
        assert f["passed"] is True, "AUC 方向翻转恒等式未通过"
        assert f["checked"] > 0 and f["max_abs_error"] <= 1e-9, \
            "恒等式误差 %.3g 超容差" % f["max_abs_error"]
        assert "AUC(1-y, p) = 1 - AUC(y, p)" in f["statement"], "恒等式表述缺失"

    def b_symmetry():
        s = R["score_symmetry"]
        assert s["passed"] is True, "双方向打分不互补"
        assert s["max_abs_prob_error"] <= s["tolerance"], \
            "p_credit + p_risk 误差 %.3g" % s["max_abs_prob_error"]
        assert s["max_abs_auc_error"] <= s["tolerance"], \
            "AUC 相等性误差 %.3g" % s["max_abs_auc_error"]
        assert "AUC(y_credit,p_credit) = AUC(y_risk,p_risk)" in s["statement"], \
            "「相等而非互补」的表述缺失"

    def b_equivalence():
        for m, d in R["retrain_equivalence"].items():
            assert d["abs_gap_equality"] < 0.01, \
                "%s 双方向 AUC 相差 %.4g（应相等）" % (m, d["abs_gap_equality"])
            assert abs(d["auc_credit_on_same_risk_score"]
                       - (1.0 - d["auc_risk_direction"])) < 1e-9, \
                "%s 同一打分翻标签的 1−AUC 计算错误" % m

    def b_trap():
        t = R["balanced_trap"]
        assert t["xgboost"] <= 0.51, "balanced 配置下 XGB 未记录退化现象"
        assert "scale_pos_weight" in t["note"] and "best_iteration=0" in t["note"], \
            "超参陷阱根因说明缺失"

    def b_main():
        m = R["main"]["risk_direction"]
        assert (m["n_samples"], m["n_positive"], m["n_negative"]) == (307, 21, 286), \
            "旧方向主配置样本分布异常"
        n = R["main"]["credit_direction"]
        assert (n["n_samples"], n["n_positive"], n["n_negative"]) == (307, 286, 21), \
            "新方向主配置样本分布异常"
        assert n["weighting"] == "symmetric" and n["relax_split_threshold"] is False, \
            "主配置权重/阈值标记异常"

    def b_perm():
        p = R["main"]["permutation"]
        for k in ("logistic_regression", "xgboost"):
            v = p[k]["auc_mean"]
            assert 0.30 < v < 0.70, "置换对照 %s AUC=%.4f 不像噪声地板" % (k, v)

    check("结果非 quick、方向规则与验证权重正确", b_meta)
    check("同一打分翻标签 AUC 互为 1−AUC（误差 ≤1e-9）", b_flip)
    check("对称重训练 p_credit=1−p_risk 且 AUC 相等", b_symmetry)
    check("双方向 AUC 相等、1−AUC 换算正确", b_equivalence)
    check("balanced 超参陷阱已如实记录", b_trap)
    check("主配置 307 / 286 正 / symmetric 权重", b_main)
    check("置换对照落在噪声地板区间", b_perm)

    # ---------------- C. 分切面 / 分队列 ----------------
    def c_policy():
        assert set(R["by_as_of_policy"]) == {"cohort", "due_minus_1"}, "切面不全"
        for k, v in R["by_as_of_policy"].items():
            assert (v["n_samples"], v["n_positive"]) == (307, 286), "%s 样本异常" % k
            assert v["relax_split_threshold"] is False, "%s 不应放宽分裂门槛" % k
            assert v["weighting"] == "symmetric"

    def c_cohort():
        g = R["by_cohort_year"]
        assert set(g) == {"2021", "2022"}, "队列不全"
        assert sum(v["n_samples"] for v in g.values()) == 307, "队列样本和不为 307"
        assert sum(v["n_negative"] for v in g.values()) == 21, "队列负例和不为 21"
        for k, v in g.items():
            assert v["relax_split_threshold"] is True, "%s 队列应放宽分裂门槛" % k
            assert "permutation" in v, "%s 缺置换对照" % k

    check("分 as_of_policy 两套切面复核齐备", c_policy)
    check("分队列 2021/2022 复核 + 置换对照齐备", c_cohort)

    # ---------------- D. SHAP ----------------
    def d_semantics():
        assert S["label_direction"] == "credit_good_positive", "SHAP 方向标记不对"
        assert "P(好企业)" in S["target"], "目标语义不是 P(好企业)"
        n = S["note"]
        assert "增信因子" in n and "风险因子" in n and "不可混用" in n, \
            "方向语义声明不完整"
        assert S["weighting"].startswith("symmetric"), "SHAP 未用对称配置"

    def d_rank():
        for key in ("xgb_top20", "lr_top20"):
            rows = S[key]
            assert len(rows) >= 1, "%s 为空" % key
            vals = [r["mean_abs_shap"] for r in rows]
            assert all(a >= b - 1e-12 for a, b in zip(vals, vals[1:])), \
                "%s 未按 mean|SHAP| 降序" % key
            assert all(r["direction"] in (-1, 0, 1) for r in rows), \
                "%s 方向取值非法" % key

    def d_files():
        assert os.path.getsize(SHAP_PNG) > 1000, "SHAP 图为空"
        with open(SHAP_CSV, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == min(20, S["n_features_used"]), "CSV 行数异常"
        assert set(rows[0]) == {"rank", "feature", "mean_abs_shap", "direction"}, \
            "CSV 表头异常"

    def d_old_untouched():
        old = load(OLD_SHAP)
        assert "违约概率" in old.get("note", ""), \
            "旧 shap_global.json 的 v1.8 历史口径被覆盖"

    def d_res_shap():
        sh = R["shap"]
        assert sh["xgb_nonzero_features"] >= 1, "XGB 无任何非零 SHAP 贡献"
        assert len(sh["lr_top5"]) == 5, "报告缺 LR Top-5 交叉参照"

    check("SHAP 语义为 P(好企业)，+1=增信因子", d_semantics)
    check("SHAP Top-K 降序且方向取值合法", d_rank)
    check("SHAP CSV / PNG 产物完整", d_files)
    check("旧 shap_global.json 保持历史口径未覆盖", d_old_untouched)
    check("结果 JSON 含非零贡献数与 LR 交叉参照", d_res_shap)

    # ---------------- E. 报告与脚本 ----------------
    def e_report():
        for i in range(1, 9):
            assert "## %d. " % i in report, "报告缺第 %d 节" % i
        assert "相等，不是互补" in report, "报告未纠正「重训练应互补」的错误预期"
        assert "未重打交付包" in report, "报告缺交付包边界声明"
        assert "--quick" not in report.split("## 1.")[0], "报告头部带 quick 警告"

    def e_script():
        s = read(SCRIPT)
        for token in ("verify_flip_identity", "verify_score_symmetry",
                      "pred_contribs", "make_sub_splits", "v_model_train_ready_credit"):
            assert token in s, "脚本缺 %s" % token

    check("报告 8 节齐全且口径边界声明完整", e_report)
    check("训练脚本关键实现齐备", e_script)

    c.close()
    print("=" * 68)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for n, e in FAIL:
            print("  - %s: %s" % (n, e))
        sys.exit(1)
    print("✅ CREDIT DIRECTION TESTS PASSED")
    print("=" * 68)


if __name__ == "__main__":
    main()

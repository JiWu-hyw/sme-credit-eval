"""真实面板数据预处理流水线（T11 —— 多源异构数据采集与预处理）。

输入
----
data/raw/zhejiang_real/zhejiang_ALL_data(1).parquet
    浙江信贷面板（204 列）：label / userid / apply_time / x1..x200 / region_code

输出
----
data/processed/zhejiang_panel_clean.parquet    行级面板（清洗 + 标准化，含 split 列）
data/processed/zhejiang_company_clean.parquet  企业级聚合表（每企业取最新一条记录）
data/processed/preprocess_meta.json            预处理元信息（统计量 / 列清单 / 划分）
data/processed/data_quality_report.md          数据质量报告

设计要点
--------
1. **划分先行**：先按 userid 做 8:2 分组切分（GroupShuffleSplit，同一企业的记录
   绝不跨训练/测试集），再把 `split` 列落进表里。下游基线 / SHAP / GAT 全部消费
   同一份划分 —— 这是三组结果可横向对比的前提。
2. **零泄漏**：缺失中位数、缩尾分位边界、Z-score 均值方差**只在训练集上拟合**，
   再 transform 到测试集。
3. **接口契约**：本表是"数据 → 图谱 → 模型"链路的第一道契约，列名与顺序冻结。
   下游若要改口径，须先改本模块再改下游。

用法
----
    .venv\\Scripts\\python.exe -m src.data.preprocess
    .venv\\Scripts\\python.exe src/data/preprocess.py
"""

import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_PARQUET = os.path.join(
    ROOT, "data", "raw", "zhejiang_real", "zhejiang_ALL_data(1).parquet"
)
PROC_DIR = os.path.join(ROOT, "data", "processed")
PANEL_OUT = os.path.join(PROC_DIR, "zhejiang_panel_clean.parquet")
COMPANY_OUT = os.path.join(PROC_DIR, "zhejiang_company_clean.parquet")
META_OUT = os.path.join(PROC_DIR, "preprocess_meta.json")
REPORT_OUT = os.path.join(PROC_DIR, "data_quality_report.md")

META_COLS = ["label", "userid", "apply_time", "region_code"]
RANDOM_SEED = 42
TEST_SIZE = 0.2
WINSOR_Q = 0.01            # 1% / 99% 分位缩尾
MISS_IND_THRESHOLD = 0.20  # 训练集缺失率超过此值的列，额外生成缺失指示位

# xlsx 附录中提供方标注的字段语义（"可能对应指标"，仅覆盖 x1..x20）
FIELD_HINTS = {
    "x1": "LIMIT_BAL / 授信额度",
    "x2": "SEX / 性别",
    "x3": "EDUCATION / 教育程度",
    "x4": "MARRIAGE / 婚姻状况",
    "x5": "AGE / 年龄",
    "x6": "PAY_0 / 还款状态",
    "x7": "PAY_2 / 历史还款状态",
    "x8": "PAY_3 / 历史还款状态",
    "x9": "PAY_4 / 历史还款状态",
    "x10": "PAY_5 / 历史还款状态",
    "x11": "PAY_6 / 历史还款状态",
    "x12": "BILL_AMT1 / 账单金额",
    "x13": "BILL_AMT2 / 历史账单金额",
    "x14": "BILL_AMT3 / 历史账单金额",
    "x15": "BILL_AMT4 / 历史账单金额",
    "x16": "BILL_AMT5 / 历史账单金额",
    "x17": "BILL_AMT6 / 历史账单金额",
    "x18": "PAY_AMT1 / 已支付金额",
    "x19": "PAY_AMT2 / 历史支付金额",
    "x20": "PAY_AMT3 / 历史支付金额",
}


def feature_cols(df: pd.DataFrame) -> List[str]:
    """返回按数值序号排序的 x 特征列名（x1, x2, ..., x200）。"""
    cols = [c for c in df.columns if c.startswith("x") and c[1:].isdigit()]
    return sorted(cols, key=lambda c: int(c[1:]))


def load_raw() -> pd.DataFrame:
    """读取原始面板，剔除无标签/无主体的行（附录行、空行）。"""
    df = pd.read_parquet(RAW_PARQUET)
    for c in META_COLS:
        if c not in df.columns:
            raise ValueError("原始面板缺少关键列: %s" % c)
    n0 = len(df)
    df = df[df["label"].notna() & df["userid"].notna()].copy()
    dropped = n0 - len(df)
    if dropped:
        print("[preprocess] 剔除无效行: %d" % dropped)
    df["userid"] = df["userid"].astype(str)
    df["apply_time"] = df["apply_time"].astype(int)
    return df.reset_index(drop=True)


def make_split(df: pd.DataFrame, seed: int = RANDOM_SEED, test_size: float = TEST_SIZE):
    """按 userid 分组切分。返回 (split 数组, 测试集 userid 列表)。"""
    gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    tr_idx, te_idx = next(gss.split(df, groups=df["userid"]))
    split = np.empty(len(df), dtype=object)
    split[tr_idx] = "train"
    split[te_idx] = "test"
    test_users = sorted(df.iloc[te_idx]["userid"].unique().tolist())
    return split, test_users


def build(seed: int = RANDOM_SEED, test_size: float = TEST_SIZE) -> Dict[str, object]:
    """执行完整预处理，写出全部产物，返回元信息字典。"""
    os.makedirs(PROC_DIR, exist_ok=True)
    df = load_raw()
    feats = feature_cols(df)
    if not feats:
        raise ValueError("未识别到任何 x 特征列")

    split, test_users = make_split(df, seed, test_size)
    df = df.copy()
    df["split"] = split
    tr = df["split"] == "train"
    print(
        "[preprocess] 划分完成: 训练 %d 行 / %d 企业, 测试 %d 行 / %d 企业"
        % (
            int(tr.sum()),
            df.loc[tr, "userid"].nunique(),
            int((~tr).sum()),
            df.loc[~tr, "userid"].nunique(),
        )
    )

    # ---- 1) 缺失：训练集中位数填充 ----
    med = df.loc[tr, feats].median()
    miss_rate_train = df.loc[tr, feats].isna().mean()

    # ---- 2) 异常值：训练集 1%/99% 分位缩尾（重尾列先取对数无必要，缩尾已足够）----
    filled_all = df[feats].fillna(med)
    q_lo = filled_all.loc[tr].quantile(WINSOR_Q)
    q_hi = filled_all.loc[tr].quantile(1 - WINSOR_Q)
    wins = filled_all.clip(lower=q_lo, upper=q_hi, axis=1)

    # ---- 3) 标准化：训练集 Z-score ----
    mu = wins.loc[tr].mean()
    sd = wins.loc[tr].std(ddof=0).replace(0.0, 1.0)
    z = (wins - mu) / sd
    z = z.astype(np.float32)

    # ---- 4) 缺失指示位（仅高缺失列）----
    ind_cols = [c for c in feats if miss_rate_train[c] > MISS_IND_THRESHOLD]
    ind = df[ind_cols].isna().astype(np.float32)
    ind.columns = ["miss_%s" % c for c in ind_cols]

    # ---- 5) 时间索引 ----
    months = sorted(df["apply_time"].unique().tolist())
    month_map = {m: i for i, m in enumerate(months)}

    base = pd.DataFrame(
        {
            "userid": df["userid"].values,
            "apply_time": df["apply_time"].values,
            "month_index": df["apply_time"].map(month_map).astype(int).values,
            "label": df["label"].astype(int).values,
            "region_code": df["region_code"].values,
            "split": df["split"].values,
        }
    )
    panel = pd.concat([base, z.reset_index(drop=True), ind.reset_index(drop=True)], axis=1)
    panel.to_parquet(PANEL_OUT, index=False)
    print("[preprocess] 写出: %s  shape=%s" % (PANEL_OUT, panel.shape))

    # ---- 6) 企业级聚合表（每企业取最新一条记录，划分随 userid）----
    company = (
        df.sort_values(["userid", "apply_time"])
        .groupby("userid", as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )
    c_feats = df.loc[company.index, feats].fillna(med)
    c_feats = c_feats.clip(lower=q_lo, upper=q_hi, axis=1)
    c_z = ((c_feats - mu) / sd).astype(np.float32)
    c_base = pd.DataFrame(
        {
            "userid": company["userid"].values,
            "apply_time": company["apply_time"].values,
            "label": company["label"].astype(int).values,
            "region_code": company["region_code"].values,
            "split": company["split"].values,
            "n_records": company["userid"].map(df["userid"].value_counts()).values,
        }
    )
    comp_panel = pd.concat(
        [c_base, c_z.reset_index(drop=True), ind.loc[company.index].reset_index(drop=True)],
        axis=1,
    )
    comp_panel.to_parquet(COMPANY_OUT, index=False)
    print("[preprocess] 写出: %s  shape=%s" % (COMPANY_OUT, comp_panel.shape))

    # ---- 7) 元信息 ----
    meta = {
        "source": os.path.relpath(RAW_PARQUET, ROOT).replace("\\", "/"),
        "random_seed": seed,
        "test_size": test_size,
        "split_protocol": "GroupShuffleSplit by userid（同企业记录不跨集）",
        "winsor_q": WINSOR_Q,
        "miss_ind_threshold": MISS_IND_THRESHOLD,
        "feature_cols": feats,
        "miss_indicator_cols": ind_cols,
        "n_rows": int(len(panel)),
        "n_companies": int(df["userid"].nunique()),
        "months": months,
        "medians": {k: float(v) for k, v in med.items()},
        "winsor_lower": {k: float(v) for k, v in q_lo.items()},
        "winsor_upper": {k: float(v) for k, v in q_hi.items()},
        "scaler_mean": {k: float(v) for k, v in mu.items()},
        "scaler_std": {k: float(v) for k, v in sd.items()},
        "test_userids": test_users,
        "field_hints": FIELD_HINTS,
    }
    with open(META_OUT, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("[preprocess] 写出: %s" % META_OUT)

    # ---- 8) 数据质量报告 ----
    write_report(df, panel, comp_panel, feats, ind_cols, miss_rate_train, q_lo, q_hi, months, meta)
    print("[preprocess] 写出: %s" % REPORT_OUT)
    return meta


def write_report(df, panel, comp_panel, feats, ind_cols, miss_rate_train, q_lo, q_hi, months, meta):
    """生成数据质量报告（中文 Markdown）。"""
    tr = panel["split"] == "train"
    rows = []
    rows.append("# 数据质量报告（真实面板）\n")
    rows.append("> 生成：`src/data/preprocess.py`（T11 预处理流水线）｜项目：中小科技企业信用智能评估\n")
    rows.append("> 数据源：`%s`\n" % meta["source"])

    rows.append("\n## 1. 规模与结构\n")
    rows.append("| 项目 | 值 |")
    rows.append("|---|---|")
    rows.append("| 行数（申请记录） | %d |" % len(panel))
    rows.append("| 企业数（userid） | %d |" % meta["n_companies"])
    rows.append("| 原始特征数 | %d（x1–x200） |" % len(feats))
    rows.append("| 附加缺失指示位 | %d |" % len(ind_cols))
    rows.append("| 时间跨度 | %d–%d（%d 个月） |" % (months[0], months[-1], len(months)))
    rows.append("| 地区 | 全部为 %s |" % panel["region_code"].iloc[0])

    rows.append("\n## 2. 标签分布\n")
    n_pos_r = int(panel["label"].sum())
    rows.append("| 粒度 | 样本数 | 正例数 | 正例率 |")
    rows.append("|---|---|---|---|")
    rows.append("| 行级（申请记录） | %d | %d | %.2f%% |" % (len(panel), n_pos_r, 100.0 * n_pos_r / len(panel)))
    n_pos_c = int(comp_panel["label"].sum())
    rows.append("| 企业级（取最新记录） | %d | %d | %.2f%% |" % (len(comp_panel), n_pos_c, 100.0 * n_pos_c / len(comp_panel)))
    rows.append("\n> 类别不均衡（正例约 9.5%%），训练时须用 `scale_pos_weight` / `class_weight` 处理，"
                "评估以 AUC / KS 为主、准确率为辅。\n")

    rows.append("\n## 3. 面板纵深\n")
    cnt = df.groupby("userid").size()
    rows.append("| 指标 | 值 |")
    rows.append("|---|---|")
    rows.append("| 每企业平均观测月数 | %.2f |" % cnt.mean())
    rows.append("| 每企业观测月数中位数 | %d |" % int(cnt.median()))
    rows.append("| 观测月数最多 | %d |" % int(cnt.max()))
    rows.append("| 有 ≥2 条记录的企业 | %d（%.1f%%） |" % ((cnt >= 2).sum(), 100.0 * (cnt >= 2).mean()))
    rows.append("| 有 ≥6 条记录的企业 | %d（%.1f%%） |" % ((cnt >= 6).sum(), 100.0 * (cnt >= 6).mean()))
    rows.append("\n> ⚠️ 纵深很浅（平均 2 个月），**逐企业长序列建模不可行**。"
                "时空图模型的“时间维”应以“跨申请月的图切片”实现，而非企业内长序列。\n")

    rows.append("\n## 4. 缺失与异常\n")
    rows.append("| 指标 | 值 |")
    rows.append("|---|---|")
    rows.append("| 特征平均缺失率（训练集） | %.2f%% |" % (100.0 * miss_rate_train.mean()))
    rows.append("| 缺失率 >50%% 的列数 | %d |" % int((miss_rate_train > 0.5).sum()))
    rows.append("| 生成缺失指示位的列（>%d%%） | %d |" % (int(MISS_IND_THRESHOLD * 100), len(ind_cols)))
    n_clip = 0
    for c in feats:
        if q_lo[c] > -np.inf:
            n_clip += 1
    rows.append("| 缩尾处理列数 | %d（全部，1%%/99%% 分位） |" % n_clip)
    rows.append("\n处理口径：训练集中位数填充 → 训练集 1%%/99%% 分位缩尾 → 训练集 Z-score 标准化。"
                "**三步统计量均只在训练集拟合**，杜绝跨集泄漏。\n")
    if ind_cols:
        rows.append("\n缺失率最高的 10 列：\n")
        rows.append("| 列 | 训练集缺失率 |")
        rows.append("|---|---|")
        for c in miss_rate_train.sort_values(ascending=False).head(10).index:
            rows.append("| %s | %.1f%% |" % (c, 100.0 * miss_rate_train[c]))

    rows.append("\n## 5. 划分协议\n")
    rows.append("| 项目 | 训练集 | 测试集 |")
    rows.append("|---|---|---|")
    rows.append("| 申请记录数 | %d | %d |" % (int(tr.sum()), int((~tr).sum())))
    rows.append("| 企业数 | %d | %d |" % (df.loc[df["split"] == "train", "userid"].nunique(),
                                        df.loc[df["split"] == "test", "userid"].nunique()))
    rows.append("| 正例率 | %.2f%% | %.2f%% |" % (
        100.0 * panel.loc[tr, "label"].mean(), 100.0 * panel.loc[~tr, "label"].mean()))
    rows.append("\n口径：`GroupShuffleSplit(test_size=%.2f, random_state=%d)`，按 userid 分组，"
                "同一企业记录不跨集。**基线 / SHAP / GAT 全部复用本划分，结果可直接横向对比。**\n"
                % (meta["test_size"], meta["random_seed"]))

    rows.append("\n## 6. 字段语义（已知部分）\n")
    rows.append("数据提供方在 xlsx 附录中标注了 x1–x20 的“可能对应指标”（与 UCI 台湾信用卡违约数据集同构）：\n\n")
    rows.append("| 变量 | 可能对应指标 | 变量 | 可能对应指标 |")
    rows.append("|---|---|---|---|")
    keys = list(FIELD_HINTS.keys())
    for i in range(0, len(keys), 2):
        k1 = keys[i]
        k2 = keys[i + 1] if i + 1 < len(keys) else ""
        rows.append("| %s | %s | %s | %s |" % (
            k1.upper(), FIELD_HINTS[k1], k2.upper(), FIELD_HINTS.get(k2, "")))
    rows.append("\n> ⚠️ 该对照为提供方**推测口径**；**x21–x200 共 180 维语义仍未知**（占特征面 90%），"
                "须向数据提供方索取字段字典。\n")

    rows.append("\n## 7. 已知局限\n")
    rows.append("1. **无企业侧实体数据**：面板只有 label + 时间轴 + 脱敏行为特征，"
                "技术要素（专利/研发/团队）与成长轨迹（融资/机构）15 个业务指标**全部缺失**；"
                "且无 userid → 统一社会信用代码映射，无法关联外部数据。\n")
    rows.append("2. **无类别型字段**：x1–x200 全为连续值，无行业码/地区细分，"
                "异质图的“实体类型”无法从本数据获得。\n")
    rows.append("3. **面板纵深浅**：平均 2.0 个月，长序列时序建模不可行（见 §3）。\n")
    rows.append("4. **标签口径未确认**：`label=1` 的精确定义（逾期天数 / 观察窗长度）待提供方确认。\n")

    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(rows))


def main():
    meta = build()
    print("\n[preprocess] 完成。特征 %d 维 + 缺失指示 %d 维。"
          % (len(meta["feature_cols"]), len(meta["miss_indicator_cols"])))


if __name__ == "__main__":
    main()

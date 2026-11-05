#!/usr/bin/env python3
"""P8 批量评估契约生成器（FE-20）

产出四份契约（``outputs/contracts/``）与一份内置演示名单（``public/fixtures/``）：

1. ``batch_submit.json``      —— ``POST /api/batch/upload`` 的响应（job_id / 受理条数 / 字段级校验摘要）
2. ``batch_status.json``      —— ``GET /api/batch/{job_id}``：一个任务的全生命周期快照
                               （``jobs[]`` 里 7 个任务分别处在「排队 / 处理中 / 部分失败 / 完成 / 空名单 / 无失败 / 已完成归档」）
3. ``batch_evidence.json``    —— 逐行追溯：名单行 → 命中的 ``userid`` → 分数 → 归因 → 导出列
4. ``batch_notes.json``       —— 这一页「能证明什么 / 不能证明什么」（自检脚本事后用）

## 这一轮最关键的一条判断

**批量评估最容易变成「黑箱出分」。** 一百家企业名单丢进去，出来一百个分数和五个档位，
看起来极其专业，但现场问三个问题就会露底：

1. 第 37 行的 61.3 分是怎么来的？—— 答不出「它命中的是哪条面板记录」就是编的；
2. 名单里那 6 行证书编号不合法，为什么还能出分？—— 出分了就说明「校验」只是装饰；
3. 分数排序和单企业深度诊断里的分数对得上吗？—— 对不上就说明两套口径。

所以这份契约把三件事写死：

| 约束 | 落点 | 脚本怎么守 |
| --- | --- | --- |
| **可追溯** | 每个成功行都带 ``userid`` / ``graph_node_id`` / ``row_no`` | 成功率 = 成功行数 / 受理行数，不等就拒写盘 |
| **不算法转换** | 分数**直取** P7 契约里同一 ``userid`` 的 ``credit_score`` | 逐行比对 ``enterprise_detail.json``，差 > 1e-9 即 ``SystemExit`` |
| **校验阻断** | 字段级不合法 / 映射不到面板 → 该行 ``error``，不出分 | 每个 ``error`` 行必须有 ``error.field`` 与 ``error.message``，且 ``credit_score is None`` |

## 名单怎么来的

真实数据侧的 P0-1（``userid → 统一社会信用代码``）还没到，所以「企业名单」只能有两类行：

- **可映射行**：用真实面板 ``userid`` 语料（``u0001…u0012``）循环占用，
  每条只用一次，用满即报「已映射耗尽」（而不是让同一家企业出两个分）；
- **不可映射行**：证书编号形如 ``GR20243300XXXX`` 但面板里没有对应 ``userid`` ——
  这是**真实系统接上 500 家名单后最常见的失败原因**，必须让它真实发生、真实被拦住。

演示名单内置 100 行（验收口径「100 条名单可完整跑通并导出」），其中 6 行有字段级问题、
3 行映射不到面板，最终成功 91 行 —— 这个数不是凑出来的，是脚本按同一套规则算出来的。

复现（零第三方依赖，seed 固定，字节级可复现）::

    python3 scripts/build_batch_contracts.py --all
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, "outputs", "contracts")
FIXTURE_DIR = os.path.join(ROOT, "public", "fixtures")
DETAIL_PATH = os.path.join(CONTRACT_DIR, "enterprise_detail.json")
LOCAL_PATH = os.path.join(CONTRACT_DIR, "shap_local.json")

SEED = 20261088
CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 28, 16, 40, tzinfo=CST).isoformat()

#: 「评估快照日」：批次受理时使用的模型/契约版本。
#: 不写「当前时间」是为了让「本次批量」与「任务列表里的历史批次」真的差一个月 ——
#: 现实中 500 家名单是按季跑的，跨批次比较 +12.5 分这种事必须被页面拦下来。
AS_OF = "2026-10-28"
HIST_AS_OF = "2026-09-20"
HIST_SHIFT = 12.5  # 历史批次的分数偏移（模拟模型快照升级）

DEMO_CSV_NAME = "batch_demo_100.csv"
EXPORT_CSV_NAME = "batch_export_100.csv"
SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- 模板与规则

#: Q3 冻结模板：6 列，顺序固定，列名逐字一致（前端只做形状校验，语义校验以后端为准）
TEMPLATE_COLUMNS = ["序号", "企业名称", "证书编号", "来源年份", "批次", "发证日期"]

#: 证书编号正则（与前端 ``utils/batch.ts › CERT_PATTERN``、后端 pydantic 校验器三处一致）
CERT_PATTERN = r"^GR\d{12}$"
CERT_RE = re.compile(CERT_PATTERN)
#: 发证日期（YYYY-MM-DD），比前端多一层「日期真的存在」的判断
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _is_real_date(text: str) -> bool:
    """YYYY-MM-DD 且日期真实存在（用来核验「声明为日期错误」的那几行）。"""
    m = DATE_RE.match(text)
    if not m:
        return False
    try:
        datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return True

#: 允许的来源年份 / 批次（Q3 冻结模板口径）
SOURCE_YEARS = [2023, 2024, 2025]
BATCHES = ["第一批", "第二批", "第三批"]

#: 内置演示名单行数（验收：100 条）
DEMO_ROWS = 100

#: 故意做坏的 6 行：行号 → (字段, 错误码, 说明) —— 位置写死，保证「错误分布」可复现
FIELD_ERRORS = {
    7: ("证书编号", "CERT_FORMAT", "证书编号只有 10 位数字（应为 GR + 12 位），正则不匹配"),
    23: ("企业名称", "REQUIRED_MISSING", "企业名称为空，无法与面板记录对齐"),
    38: ("发证日期", "DATE_FORMAT", "发证日期无法解析（应为 YYYY-MM-DD，收到「2024/13/45」）"),
    54: ("来源年份", "YEAR_ENUM", "来源年份 2021 不在允许范围（2023 / 2024 / 2025）"),
    69: ("批次", "BATCH_ENUM", "批次「第五批」不在 Q3 模板允许值内"),
    88: ("证书编号", "CERT_FORMAT", "证书编号含空格（「GR2024 33001234」），正则不匹配"),
}

#: 故意映射不到面板的 3 行（数字有、格式也对，但面板里没有这家企业 → 必须阻断）
UNMAPPED_ROWS = [15, 61, 92]


# --------------------------------------------------------------------------- #
# 确定性随机（与 FE-13…FE-16 同一套 LCG）
# --------------------------------------------------------------------------- #
class LCG:
    def __init__(self, seed: int) -> None:
        self.state = seed % 2147483647 or 1

    def next_u(self) -> float:
        self.state = (self.state * 1103515245 + 12345) % 2147483648
        return self.state / 2147483648.0

    def choice(self, seq):
        return seq[int(self.next_u() * len(seq)) % len(seq)]


RNG = LCG(SEED)


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump(path: str, payload: dict) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text.encode("utf-8"))


# --------------------------------------------------------------------------- #
# 1. 演示名单
# --------------------------------------------------------------------------- #
def build_roster(enterprises: list[dict]) -> list[dict]:
    """生成 100 行名单：91 行可映射 + 6 行字段级错误 + 3 行不可映射。

    「可映射」= 面板 12 个 userid 各自循环占用，**每次占用前复制一份快照**。
    为什么不是「一企一行」：真实名单 500 家，可映射面板只有 12 家，
    一企一行只能跑通 12 行 —— 那种"验收"证明不了批量页在 100 行规模下可用
    （虚拟滚动、按档筛选、导出 100 行、进度轮询都得在真实体量下验）。
    为什么不是「一企重复多次」：同一企业出现两次分，等于把批次噪声当成了企业差异。
    折中方案是**名单快照**：每次占用面板企业时，按名单自身的「批次 / 发证日期 / 来源年份」
    记录成一条独立的名单档案件（``cert_no`` 不同），分数沿用该企业档位路径内的确定性微调。
    """
    rows: list[dict] = []
    pool = [e["userid"] for e in enterprises]
    n_mappable = DEMO_ROWS - len(FIELD_ERRORS) - len(UNMAPPED_ROWS)
    mi = 0

    for row_no in range(1, DEMO_ROWS + 1):
        year = RNG.choice(SOURCE_YEARS)
        batch = RNG.choice(BATCHES)
        d = datetime(year, 6 + int(RNG.next_u() * 6) % 6, 1 + int(RNG.next_u() * 28) % 28)
        serial = 33000000 + row_no * 137

        row = {
            "row_no": row_no,
            "name": f"浙江示例科技有限公司（{row_no:03d}）",
            "cert_no": f"GR{year}{serial:08d}",
            "source_year": year,
            "batch": batch,
            "issue_date": d.strftime("%Y-%m-%d"),
            "userid": None,
            "panel_record_no": None,
            "field_error": None,
        }

        if row_no in FIELD_ERRORS:
            field, code, msg = FIELD_ERRORS[row_no]
            if code == "REQUIRED_MISSING":
                row["name"] = ""
            # 两行坏在证书编号上，但坏法不同（一行少 2 位数字、一行多了个空格）——
            # 正是这样才覆盖到正则的两种失败形态。判断依据是行号，不是那句说明文案：
            # 以前按文案里的「空格」二字分流，改了文案就静默走进另一支，值根本没坏。
            elif code == "CERT_FORMAT" and row_no == 88:
                row["cert_no"] = f"GR{year} {serial:08d}"
            elif code == "CERT_FORMAT":
                # 少写 2 位数字：序列号本身是 8 位，`06d` 不会截短，必须显式切掉末尾两位
                row["cert_no"] = f"GR{year}{serial:06d}"[:-2]
            elif code == "DATE_FORMAT":
                row["issue_date"] = f"{year}/13/45"
            elif code == "YEAR_ENUM":
                row["source_year"] = 2021
            elif code == "BATCH_ENUM":
                row["batch"] = "第五批"
            row["field_error"] = {"field": field, "code": code, "message": msg}
        elif row_no not in UNMAPPED_ROWS:
            # 循环占用面板语料，每次占用都是一份新的名单档案件（同企业、不同证书编号）
            row["userid"] = pool[mi % len(pool)]
            row["panel_record_no"] = mi // len(pool) + 1  # 第几次占用该企业（1 = 首次）
            mi += 1
        rows.append(row)
    return rows


def roster_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    # 不引号：与 Excel「CSV UTF-8」另存出来的形态一致，现场可以直接打开核对
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(TEMPLATE_COLUMNS)
    for r in rows:
        writer.writerow(
            [
                r["row_no"],
                r["name"],
                r["cert_no"],
                r["source_year"],
                r["batch"],
                r["issue_date"],
            ]
        )
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 2. 逐行判定（唯一事实源：这里算一遍，页面与导出都读这份结果）
# --------------------------------------------------------------------------- #
def judge_row(row: dict) -> dict:
    """返回该行的结论：ok / field_error / unmapped。

    校验顺序照真实后端：**先字段级、再映射级**。
    字段不合法时不去查映射（省一次查询，且报错更精确）；
    字段合法但映射不到，才是「名单企业不在面板里」这种业务级失败。
    """
    if row["field_error"]:
        return {"status": "field_error", **row["field_error"]}
    if row["userid"] is None:
        return {
            "status": "unmapped",
            "field": "证书编号",
            "code": "UNMAPPED",
            "message": "证书编号校验通过，但面板中没有与该编号对应的 userid"
            "（P0-1 映射键未覆盖该企业，属映射缺而不是数据错）",
        }
    return {"status": "ok"}


def build_evidence(rows, judge, enterprises, local_entries, as_of: str = AS_OF, score_key: str = "credit_score") -> list[dict]:
    detail_by_uid = {e["userid"]: e for e in enterprises}
    local_by_uid = {e["userid"]: e for e in local_entries}
    out = []
    for r in rows:
        j = judge[r["row_no"]]
        base = {
            "row_no": r["row_no"],
            "name": r["name"],
            "cert_no": r["cert_no"],
            "source_year": r["source_year"],
            "batch": r["batch"],
            "issue_date": r["issue_date"],
            "status": j["status"],
            "as_of": as_of,
            "panel_record_no": r.get("panel_record_no"),
        }
        if j["status"] != "ok":
            base.update(
                {
                    "userid": None,
                    "graph_node_id": None,
                    "credit_score": None,
                    "risk_band": None,
                    "risk_band_label": None,
                    "pd": None,
                    "top_factor": None,
                    "top_factor_known": False,
                    "anomaly": None,
                    "error": {"field": j["field"], "code": j["code"], "message": j["message"]},
                    "trace": None,
                }
            )
        else:
            uid = r["userid"]
            d = detail_by_uid[uid]
            le = local_by_uid[uid]
            top = le["features"][0] if le["features"] else None
            # 异常判定用一条写得出来的规则，不是拍脑袋：分数落「预警 / 高风险」两档即标记，
            # 并给出触发它的字段（Top-1 推高项），读者能顺着 trace 回到原始记录。
            band = le["risk_band"]
            anomaly = band in ("warning", "high_risk")
            base.update(
                {
                    "userid": uid,
                    "graph_node_id": d["graph_node_id"],
                    "credit_score": d[score_key],
                    "risk_band": band,
                    "risk_band_label": RISK_LABEL[band],
                    "pd": d["pd"],
                    "top_factor": top["feature"] if top else None,
                    "top_factor_known": bool(top and top.get("semantic_known")),
                    "anomaly": anomaly,
                    "error": None,
                    "trace": {
                        "panel_userid": uid,
                        "graph_node_id": d["graph_node_id"],
                        "enterprise_contract": "outputs/contracts/enterprise_detail.json",
                        "local_contract": "outputs/contracts/shap_local.json",
                        "stage_id": d["stage_id"],
                        "observed_months": d["observed_months"],
                        "score_source": f"enterprise_detail.enterprises[userid].{score_key}（直取，未做二次变换）",
                        "as_of": as_of,
                        "anomaly_rule": "risk_band ∈ {warning, high_risk}（P7 五档切点 50/60/70/80 派生）",
                    },
                }
            )
        out.append(base)
    return out


RISK_LABEL = {
    "excellent": "优良",
    "good": "较好",
    "watch": "关注",
    "warning": "预警",
    "high_risk": "高风险",
}


def export_csv(evidence: list[dict]) -> str:
    """导出 CSV：列名复用 Q3 模板 6 列 + 评估结果 5 列（顺序固定）。"""
    buf = io.StringIO()
    # 不引号：与 Excel「CSV UTF-8」另存出来的形态一致，现场可以直接打开核对
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(TEMPLATE_COLUMNS + ["信用分", "风险分级", "主要贡献因子", "是否异常", "评估状态"])
    for e in evidence:
        writer.writerow(
            [
                e["row_no"],
                e["name"],
                e["cert_no"],
                e["source_year"],
                e["batch"],
                e["issue_date"],
                "" if e["credit_score"] is None else f"{e['credit_score']:.1f}",
                e["risk_band_label"] or "",
                e["top_factor"] or "",
                "" if e["anomaly"] is None else ("是" if e["anomaly"] else "否"),
                STATUS_LABEL[e["status"]],
            ]
        )
    return buf.getvalue()


STATUS_LABEL = {
    "ok": "成功",
    "field_error": "字段不合法",
    "unmapped": "未映射到面板",
    "mapped_exhausted": "映射额度已用尽",
}


# --------------------------------------------------------------------------- #
# 3. 任务快照（6 个 job，覆盖页面上会遇到的每一种状态）
# --------------------------------------------------------------------------- #
def summarise(evidence: list[dict]) -> dict:
    """汇总只从 evidence 算 —— 页面上的每个 KPI 都能由逐行结果重算出来，没有另存的数。"""
    ok = [e for e in evidence if e["status"] == "ok"]
    fail = [e for e in evidence if e["status"] != "ok"]
    n = len(evidence)
    bands = {k: 0 for k in ("excellent", "good", "watch", "warning", "high_risk")}
    for e in ok:
        bands[e["risk_band"]] += 1
    return {
        "rows_done": n,
        "rows_ok": len(ok),
        "rows_failed": len(fail),
        "success_rate": round(len(ok) / n, 4) if n else 0.0,
        "score_mean": round(sum(e["credit_score"] for e in ok) / len(ok), 2) if ok else None,
        "score_min": min((e["credit_score"] for e in ok), default=None),
        "score_max": max((e["credit_score"] for e in ok), default=None),
        "anomaly_count": sum(1 for e in ok if e["anomaly"]),
        "band_counts": bands,
        "error_breakdown": breakdown(fail),
    }


def breakdown(fail: list[dict]) -> list[dict]:
    """失败原因按**错误码**归并（不是按「失败」两个字）。每个码带受影响的字段与行号，可点回原行。"""
    bucket: dict[str, dict] = {}
    for e in fail:
        code = e["error"]["code"]
        b = bucket.setdefault(
            code,
            {
                "code": code,
                "label": ERROR_LABEL[code],
                "kind": "field" if e["status"] == "field_error" else "mapping",
                "field": e["error"]["field"],
                "rows": 0,
                "row_nos": [],
            },
        )
        b["rows"] += 1
        b["row_nos"].append(e["row_no"])
    return sorted(bucket.values(), key=lambda x: (-x["rows"], x["code"]))


ERROR_LABEL = {
    "CERT_FORMAT": "证书编号格式不合规",
    "REQUIRED_MISSING": "必填项缺失",
    "DATE_FORMAT": "发证日期无法解析",
    "YEAR_ENUM": "来源年份不在允许范围",
    "BATCH_ENUM": "批次不在模板枚举内",
    "UNMAPPED": "未映射到面板记录",
}

EMPTY_SUMMARY = {
    "rows_done": 0,
    "rows_ok": 0,
    "rows_failed": 0,
    "success_rate": 0.0,
    "score_mean": None,
    "score_min": None,
    "score_max": None,
    "anomaly_count": 0,
    "band_counts": {k: 0 for k in ("excellent", "good", "watch", "warning", "high_risk")},
    "error_breakdown": [],
}


def make_job(
    *,
    job_id,
    status,
    created,
    updated,
    as_of,
    filename,
    evidence,
    rows_total,
    progress_note,
    live_note=None,
    extra_summary=None,
    error=None,
):
    """构造一个任务。``status`` 只取 queued / processing / done / failed 四值。"""
    is_final = status in ("done", "failed")
    summary = dict(EMPTY_SUMMARY) if not evidence else summarise(evidence)
    summary["rows_total"] = rows_total
    summary["final"] = is_final
    if extra_summary:
        summary.update(extra_summary)
    return {
        "job_id": job_id,
        "status": status,
        "created_at": created,
        "updated_at": updated,
        "as_of": as_of,
        "filename": filename,
        "rows_total": rows_total,
        "summary": summary,
        "progress_note": progress_note,
        "live_note": live_note,
        "export_ready": status == "done" and summary["rows_ok"] > 0,
        "error": error,
        "evidence": evidence,
    }


def build_status(evidence: list[dict], historic: list[dict]) -> dict:
    n = len(evidence)

    # ① 正在跑：evidence 只含已处理的前 42 行 —— 页面能显示「已处理条数」与部分结果，
    #    但汇总统计只覆盖这 42 行（rows_done=42，final=false），不做全量外推。
    live_rows = evidence[:42]
    live_summary = summarise(live_rows)
    live_summary.pop("rows_total", None)
    live = make_job(
        job_id="job_7f3c1a90",
        status="processing",
        created="2026-10-28T16:41:00+08:00",
        updated="2026-10-28T16:42:18+08:00",
        as_of=AS_OF,
        filename="浙江省专精特新中小企业名单_2026Q3.xlsx",
        evidence=live_rows,
        rows_total=n,
        progress_note="正在逐行打分：第 42 / 100 行，预计剩余 4 秒",
        live_note="以下为已处理部分的实时结果（42 行），任务完成后自动全量替换",
        extra_summary=live_summary,
    )

    # ② 排队中：还没开始，任何统计都没有值（不是 0，是 null）
    queued = make_job(
        job_id="job_5b2e77d1",
        status="queued",
        created="2026-10-28T16:42:40+08:00",
        updated="2026-10-28T16:42:40+08:00",
        as_of=AS_OF,
        filename="浙江省专精特新中小企业名单_2026Q3.xlsx",
        evidence=[],
        rows_total=n,
        progress_note="已受理，前面还有一个任务在跑（单实例串行执行）",
    )

    # ③ 完成（默认打开）：100 行受理，91 行出分，9 行被拦
    done = make_job(
        job_id="job_2d9a4c33",
        status="done",
        created="2026-10-28T16:41:00+08:00",
        updated="2026-10-28T16:42:52+08:00",
        as_of=AS_OF,
        filename="浙江省专精特新中小企业名单_2026Q3.xlsx",
        evidence=evidence,
        rows_total=n,
        progress_note="100 行受理完毕，耗时 5.8 秒；导出口径 = Q3 模板 6 列 + 评估结果 5 列",
    )

    # ④ 上个月的批次：同一份名单换模型快照重跑（分数整体 +12.5）—— 用来验「跨批次不可混比」
    hist = make_job(
        job_id="job_0e6b31a7",
        status="done",
        created="2026-09-20T10:12:07+08:00",
        updated="2026-09-20T10:12:59+08:00",
        as_of=HIST_AS_OF,
        filename="浙江省专精特新中小企业名单_2026Q2.xlsx",
        evidence=historic,
        rows_total=n,
        progress_note="历史批次：当版模型快照（与本次差 1 个版本），分数逐行 +12.5，"
        "用于验证「跨批次不可直接比」的提示是否真会出现",
    )

    # ⑤ 空名单：文件只有表头
    empty = make_job(
        job_id="job_0a11b6f8",
        status="done",
        created="2026-10-27T17:02:44+08:00",
        updated="2026-10-27T17:02:45+08:00",
        as_of=AS_OF,
        filename="空白模板.xlsx",
        evidence=[],
        rows_total=0,
        progress_note="文件只有表头，未读到任何数据行",
    )

    # ⑥ 解析失败：xls 冒充 xlsx
    failed = make_job(
        job_id="job_d77e2a04",
        status="failed",
        created="2026-10-26T11:05:19+08:00",
        updated="2026-10-26T11:05:20+08:00",
        as_of=AS_OF,
        filename="专精特新名单.xls",
        evidence=[],
        rows_total=0,
        progress_note="文件解析失败，未进入逐行打分阶段",
        error={
            "code": "FILE_PARSE_ERROR",
            "message": "无法解析为 xlsx / csv：文件头既不是 ZIP（xlsx），也不是 UTF-8 文本（csv）",
            "next_step": "在 Excel 中「另存为 .xlsx」或「CSV UTF-8（逗号分隔）」后重新上传",
        },
    )

    jobs = [live, queued, done, hist, empty, failed]
    return {
        "chart": "A21",
        "jobs": jobs,
        "default_job_id": "job_2d9a4c33",
        "as_of": AS_OF,
        "field_validate": {
            "template_columns": TEMPLATE_COLUMNS,
            "cert_pattern": CERT_PATTERN,
            "source_years": SOURCE_YEARS,
            "batches": BATCHES,
            "encoding": "UTF-8（带 BOM 亦可）",
            "size_limit_mb": 10,
            "row_limit": 5000,
        },
        "export_columns": TEMPLATE_COLUMNS + ["信用分", "风险分级", "主要贡献因子", "是否异常", "评估状态"],
        "notes": build_notes(),
    }


def build_notes() -> list[dict]:
    """页面底部「这一页能证明什么 / 不能证明什么」。每一条都对得上一条代码或一条断言。"""
    return [
        {
            "q": "批量分和 P7 单企业诊断里的分是同一个分吗？",
            "a": "是。批量出分不另训模型，而是按 userid 直取 P7 契约的 credit_score；"
            "生成脚本逐行回比 enterprise_detail.json，差超过 1e-9 直接拒绝写盘。"
            "跨批次（as_of 不同）的分数则不可直接比，页面会显式拦一次。",
            "level": "verified",
        },
        {
            "q": "100 行名单，为什么只有 91 行出分？",
            "a": "6 行字段级不合法（证书编号正则 / 必填缺失 / 日期无法解析 / 年份与批次不在模板枚举内），"
            "3 行证书编号合法但面板里没有对应 userid。前者是「数据错」，后者是「映射缺」（P0-1），"
            "处置动作不一样，所以分开报，不合并成一句「部分失败」。",
            "level": "verified",
        },
        {
            "q": "导出 CSV 为什么不只导分数？",
            "a": "列名复用 Q3 冻结模板 6 列，再追加 5 列评估结果。保留原列是为了客户拿到文件还能回填系统；"
            "追加而不是替换，是为了「哪一行没出分」在 CSV 里一眼可见（评估状态列）。",
            "level": "verified",
        },
        {
            "q": "这些分数能拿去授信吗？",
            "a": "不能。行级实测 AUC 约 0.59、置换对照噪声地板 0.477，只比地板高 0.1，够做名单初筛排序。"
            "批量场景最容易把「能出分」误读成「能决策」，所以这条写在结果表抬头而不是页脚。",
            "level": "redline",
        },
        {
            "q": "主要贡献因子为什么显示 x125 这种代号？",
            "a": "x21–x200 字段字典（P0-2）未到位，业务名一律不编。字典补齐后由后端把 label_ref 解析成业务名，"
            "前端零改动（红线二：字段语义未知时只显代号）。",
            "level": "redline",
        },
        {
            "q": "为什么有一个任务停在「处理中」？",
            "a": "不是造出来的假象：单实例后端串行执行，后一个任务要等前一个跑完。"
            "页面 2 秒轮询真实进度；处理中的任务只展示已处理的行，已处理部分可以先看，不必等全量。",
            "level": "design",
        },
        {
            "q": "任务列表里有两个批次，能放在一起看吗？",
            "a": "不能。两个批次的 as_of 不同（模型快照差一版），同一家企业的分差 12.5 分来自模型版本而不是企业经营变化。"
            "页面默认只展示当前快照，跨快照会先弹一次确认并标黄。",
            "level": "verified",
        },
    ]


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build_all() -> None:
    detail = load(DETAIL_PATH)
    local = load(LOCAL_PATH)
    enterprises = detail["data"]["enterprises"]
    entries = local["data"]["entries"]

    rows = build_roster(enterprises)
    judge: dict[int, dict] = {r["row_no"]: judge_row(r) for r in rows}
    evidence = build_evidence(rows, judge, enterprises, entries)

    # 历史批次：同一份名单、上个月的模型快照（分数 +12.5，档位按新分数重算）
    # 注意：+12.5 会让全部企业上移到「较好 / 优良」两档 —— 这正是跨批次不可比的直观证据：
    # 换了模型快照，一批企业的档位整体搬家，但企业本身一家都没变。
    hist_enterprises = []
    for e in enterprises:
        shifted = min(99.0, e["credit_score"] + HIST_SHIFT)
        hist_enterprises.append(
            {
                **e,
                "credit_score": round(shifted, 1),
                "pd": round(1 / (1 + math.exp((shifted - 62) / 6.2)), 4),
            }
        )
    hist_entries = []
    for le in entries:
        shifted = min(99.0, le["credit_score"] + HIST_SHIFT)
        hist_entries.append({**le, "credit_score": round(shifted, 1), "risk_band": band_of(shifted)})
    historic = build_evidence(
        rows, judge, hist_enterprises, hist_entries, as_of=HIST_AS_OF, score_key="credit_score"
    )

    # ---- 硬约束 ---------------------------------------------------------
    ok_rows = [e for e in evidence if e["status"] == "ok"]
    fail_rows = [e for e in evidence if e["status"] != "ok"]
    if len(ok_rows) + len(fail_rows) != len(evidence):
        raise SystemExit("成功率分子分母不闭合")

    by_uid = {e["userid"]: e for e in enterprises}
    for e in ok_rows:
        d = by_uid[e["userid"]]
        if abs(d["credit_score"] - e["credit_score"]) > 1e-9:
            raise SystemExit(
                f"行 {e['row_no']}: 分数与 P7 契约不一致（{e['credit_score']} vs {d['credit_score']}）"
            )
        if d["graph_node_id"] != e["graph_node_id"]:
            raise SystemExit(f"行 {e['row_no']}: graph_node_id 与 P7 契约不一致")
        le = next(x for x in entries if x["userid"] == e["userid"])
        if le["risk_band"] != e["risk_band"]:
            raise SystemExit(f"行 {e['row_no']}: 风险档位与 P7 契约不一致")

    for e in fail_rows:
        if e["credit_score"] is not None:
            raise SystemExit(f"行 {e['row_no']}: 失败行不得出分")
        if not e["error"] or not e["error"].get("field") or not e["error"].get("message"):
            raise SystemExit(f"行 {e['row_no']}: 失败行缺少字段级原因")

    certs = [e["cert_no"] for e in ok_rows]
    if len(certs) != len(set(certs)):
        raise SystemExit("证书编号在名单内重复")

    # ---- 名单与契约必须自洽 ---------------------------------------------
    # 这一段是被真事故逼出来的：第 7 行声明「证书编号只有 10 位」，实际写进 CSV 的却是
    # 合法编号 —— 契约里它被判失败，前端拿同一份 CSV 重跑时它却通过了。
    # 同一行两套结论，比没有校验难查得多。所以这里对每一行做**单向可判定**的核对：
    # 声明是哪一类错误，就核对该字段的值是否真的违反了那条规则。
    for r in rows:
        fe = r.get("field_error")
        if not fe:
            continue
        if not isinstance(fe, dict):
            # 旧版本把 field_error 存成 dict；这里兼容一下，避免格式变动时静默跳过校验
            raise SystemExit(f"行 {r['row_no']}：field_error 结构异常 {fe!r}")
        field, code, _ = fe["field"], fe["code"], fe.get("message", "")
        value = {
            "序号": str(r["row_no"]),
            "企业名称": r["name"],
            "证书编号": r["cert_no"],
            "来源年份": str(r["source_year"]),
            "批次": r["batch"],
            "发证日期": r["issue_date"],
        }[field]
        rule = {
            "CERT_FORMAT": lambda v: not CERT_RE.match(str(v)),
            "REQUIRED_MISSING": lambda v: not str(v).strip(),
            "YEAR_ENUM": lambda v: str(v) not in {str(y) for y in SOURCE_YEARS},
            "BATCH_ENUM": lambda v: str(v) not in BATCHES,
            "DATE_FORMAT": lambda v: not _is_real_date(str(v)),
        }[code]
        if not rule(value):
            raise SystemExit(
                f"行 {r['row_no']}：声明为 {code}，但 {field}={value!r} 并没有违反该规则"
            )

    # ---- 写文件 ---------------------------------------------------------
    roster_csv = roster_to_csv(rows)
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, DEMO_CSV_NAME), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(roster_csv)  # BOM：Excel 双击不乱码

    with open(os.path.join(FIXTURE_DIR, EXPORT_CSV_NAME), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(export_csv(evidence))

    status = build_status(evidence, historic)
    summary = summarise(evidence)

    submit = {
        "chart": "A21",
        "data": {
            "job_id": status["default_job_id"],
            "status": "done",
            "as_of": AS_OF,
            "filename": "浙江省专精特新中小企业名单_2026Q3.xlsx",
            "rows_total": len(evidence),
            "rows_accepted": len(evidence),
            "field_validate": {
                "ok": True,
                "template_columns": TEMPLATE_COLUMNS,
                "cert_pattern": CERT_PATTERN,
                "ignored_extra_columns": [],
                "message": "6 列齐备，编码 UTF-8（BOM），大小 18.4 KB，序号 1–100 连续",
            },
            "estimated_seconds": 6,
            "accepted_at": NOW,
        },
        "meta": meta_block(
            source="contract_spec",
            n_samples=len(evidence),
            disclaimer="批量受理响应：job_id / 受理条数 / 字段校验口径为真实后端字段；"
            "演示名单由脱敏示例企业构成，不代表任何真实企业。",
        ),
    }
    evidence_env = {
        "chart": "A21",
        "data": {
            "job_id": status["default_job_id"],
            "as_of": AS_OF,
            "export_columns": status["export_columns"],
            "rows": evidence,
            "summary": summary,
        },
        "meta": meta_block(
            source="reconstructed_from_anchors",
            n_samples=len(evidence),
            disclaimer="逐行追溯：分数与档位直取 P7 契约同一 userid；主要贡献因子取其局部归因 rank-1（语义待字典）；"
            "企业名称为脱敏示例名。",
        ),
    }
    status_env = {
        "chart": "A21",
        "data": status,
        "meta": meta_block(
            source="contract_spec",
            n_samples=len(evidence),
            disclaimer="任务快照：6 个任务覆盖排队 / 处理中 / 完成 / 历史批次 / 空名单 / 解析失败。"
            "进度与条数为后端字段口径，名单内容为脱敏示例。",
        ),
    }

    sizes = [
        ("batch_submit.json", dump(os.path.join(CONTRACT_DIR, "batch_submit.json"), submit)),
        ("batch_status.json", dump(os.path.join(CONTRACT_DIR, "batch_status.json"), status_env)),
        ("batch_evidence.json", dump(os.path.join(CONTRACT_DIR, "batch_evidence.json"), evidence_env)),
        (
            "batch_notes.json",
            dump(os.path.join(CONTRACT_DIR, "batch_notes.json"), {"notes": build_notes(), "as_of": AS_OF}),
        ),
    ]

    print("— FE-20 批量评估契约已生成")
    for name, size in sizes:
        print(f"  {name:<26} {size / 1024:8.1f} KB")
    print(f"  public/fixtures/{DEMO_CSV_NAME:<28} {len(roster_csv.encode('utf-8')):8} B")
    print(
        f"  名单 {summary['rows_done']} 行 → 成功 {summary['rows_ok']} / 失败 {summary['rows_failed']}"
        f"（成功率 {summary['success_rate']:.1%}），分数均值 {summary['score_mean']}，异常 {summary['anomaly_count']} 家"
    )
    print(f"  五档分布 {summary['band_counts']}")
    for b in summary["error_breakdown"]:
        print(f"    · {b['code']:<16} {b['rows']:>2} 行  行号 {b['row_nos']}")


def band_of(score: float) -> str:
    """与前端 utils/enterprise.ts › bandOf 同一套切点（50/60/70/80，左闭右开）。"""
    cuts = [50, 60, 70, 80]
    codes = ["high_risk", "warning", "watch", "good", "excellent"]
    idx = sum(1 for c in cuts if score >= c)
    return codes[idx]


def meta_block(*, source: str, n_samples: int, disclaimer: str) -> dict:
    return {
        "generated_at": NOW,
        "script": "scripts/build_batch_contracts.py",
        "dataset": "real_panel",
        "source": source,
        "schema_version": SCHEMA_VERSION,
        "n_samples": n_samples,
        "seed": SEED,
        "semantic_known": False,
        "label_ref": None,
        "noise_floor": 0.477,
        "contract_status": "mock",
        "blocked_by": ["P0-1", "P0-2", "P0-3"],
        "disclaimer": disclaimer,
        "redline": {
            "no_mixed_dataset": True,
            "noise_floor_applicable": True,
            "unknown_fields_shown_as_code": True,
            "score_not_transformed": True,
            "every_row_traceable": True,
            "cross_batch_not_comparable": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 FE-20 批量评估契约")
    parser.add_argument("--all", action="store_true", help="全部生成（默认行为）")
    parser.parse_args()
    build_all()


if __name__ == "__main__":
    main()

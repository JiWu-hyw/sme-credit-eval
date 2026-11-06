# -*- coding: utf-8 -*-
"""生成可由现有数据真实派生的契约文件（后端契约驱动闭环的本地落地）。

产出：
  1. configs/enums.json          —— 实体枚举（资质类型 / 数据源 / IPC 部类），取自标准库字典表
  2. configs/data_gaps.mirror.json —— data_gaps.yaml 的类型化镜像 + gap_summary 聚合

这两个文件是 API「就绪接口」的直接数据源；其余契约文件（shap_global.json /
baseline_results.json / dashboard_data.json 等）须由模型/图谱/前端管道真实产出，不在此伪造。
"""
import json
import os
import sys

# 本脚本在 scripts/ 下，ROOT 需上跳一级才能 import src.*
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.data.db import connect, ROOT  # noqa: E402

# ---------------------------------------------------------------------------
# 1. 实体枚举：从标准库字典表真实读取
# ---------------------------------------------------------------------------
conn = connect()
qual = [
    dict(r) for r in conn.execute(
        "SELECT qual_type, qual_name, level_rank, valid_years, issuer "
        "FROM dim_qual_type ORDER BY level_rank"
    )
]
srcs = [
    dict(r) for r in conn.execute(
        "SELECT source_id, source_name, source_type, license, owner, coverage "
        "FROM meta_data_source ORDER BY source_id"
    )
]
ipc = [
    dict(r) for r in conn.execute(
        "SELECT ipc_code, ipc_level, title FROM dim_ipc ORDER BY ipc_code"
    )
]
conn.close()

enums = {
    "version": "v1.0",
    "entities": {
        "qual_type": qual,         # 资质层级（科技型→高新→专精特新→小巨人）
        "data_source": srcs,       # 10 个免费数据源登记
        "ipc_section": ipc,        # IPC 八大部（技术广度上位分类）
    },
}
cfg_dir = os.path.join(ROOT, "configs")
os.makedirs(cfg_dir, exist_ok=True)
with open(os.path.join(cfg_dir, "enums.json"), "w", encoding="utf-8") as f:
    json.dump(enums, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# 2. data_gaps 镜像：忠实复制 data_gaps.yaml 的 gaps，并聚合 gap_summary
#    （保留 P0-1/2/3 的 missing 口径，与 tdrive 源文件一致；其「替代为新数据」决策见采购清单文档）
# ---------------------------------------------------------------------------
gaps = [
    {"id": "P0-1", "name": "userid → 统一社会信用代码/企业名映射键", "status": "missing",
     "why": "无映射键则面板观测无法挂接企业实体，图谱与外源特征无法对齐",
     "source": "信贷数据提供方", "owner": "袁雪茹", "blocks": ["图谱对齐", "特征融合", "P7单企业诊断"]},
    {"id": "P0-2", "name": "x21–x200 字段字典", "status": "missing",
     "why": "SHAP 99% 贡献落在语义未知字段，业务解读与指标体系设计均被阻塞",
     "source": "数据提供方字段说明文档", "owner": "袁雪茹", "blocks": ["指标体系", "业务解读", "可解释性叙事"]},
    {"id": "P0-3", "name": "label 标签口径定义", "status": "missing",
     "why": "违约/经营异常判定口径未确认，评估结论无法对外表述",
     "source": "数据提供方 / 指导教师确认", "owner": "袁雪茹", "blocks": ["模型评估口径", "前瞻预测"]},
    {"id": "P0-4", "name": "企业专利数据（技术要素）", "status": "in_progress",
     "why": "技术要素维度完全缺失，是真实面板区分度弱（行级 AUC≈0.59）的主因之一",
     "source": "CNIPA / 图书馆 Incopat（免费渠道已确认）", "owner": "吕键",
     "blocks": ["技术要素特征", "技术相似网络"]},
    {"id": "P0-5", "name": "融资与投资机构数据（成长轨迹）", "status": "in_progress",
     "why": "成长轨迹维度完全缺失；融资力预测与估值区间依赖外部事件数据",
     "source": "企查查/天眼查 API（预算 ¥3,000 内）", "owner": "吕键",
     "blocks": ["成长轨迹特征", "融资力预测"]},
]
summary = {
    "total": len(gaps),
    "missing": sum(1 for g in gaps if g["status"] == "missing"),
    "in_progress": sum(1 for g in gaps if g["status"] == "in_progress"),
    "ready": sum(1 for g in gaps if g["status"] == "ready"),
}
mirror = {
    "version": "1.0",
    "updated_at": "2026-10-17",
    "maintainer": "袁雪茹（负责人/金融统筹）",
    "gaps": gaps,
    "gap_summary": summary,
}
with open(os.path.join(cfg_dir, "data_gaps.mirror.json"), "w", encoding="utf-8") as f:
    json.dump(mirror, f, ensure_ascii=False, indent=2)

print("✔ configs/enums.json       : %d 资质 / %d 数据源 / %d IPC部类"
      % (len(qual), len(srcs), len(ipc)))
print("✔ configs/data_gaps.mirror.json : %d 缺口  summary=%s" % (len(gaps), summary))

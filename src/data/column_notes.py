"""字段中文说明补充表（人工维护）。

作用：DDL 里没写 -- 注释的新增字段（QCC 接入后、生存分析表、建模宽表等），
在此集中登记，供 export_data_dictionary.py 合并进数据字典。

维护约定：新增字段时优先写在 schema.sql 的 -- 注释里；只有补历史欠账才写这里。
键为字段名，值为中文说明；COMMON 适用于所有同名通用字段。
"""

# 跨表通用字段
COMMON = {
    "enterprise_id": "企业代理键 ENT+md5(规范化名)，FK → dim_enterprise",
    "stat_year": "统计年度（按自然年切片）",
    "cohort_year": "队列年（资质首次认定年份）",
    "computed_at": "特征计算时间戳",
    "created_at": "行创建时间",
    "updated_at": "最后一次更新时间",
    "built_at": "该表构建时间",
    "source_id": "FK → meta_data_source，数据源登记",
    "raw_id": "FK → ODS 原始行，血缘回溯用",
    "notice_id": "FK → ods_qual_notice，公示批次",
    "row_id": "ODS 原始行自增 ID",
    "region_code": "行政区划代码（GB/T 2260）",
    "region_name": "行政区划名称",
    "label_task": "标签任务：qual_survival=资质存续（主任务）/ msme_default=MSME 违约（对照）",
    "file_path": "原始文件落盘路径",
    "file_sha256": "原始文件 SHA256，复现校验用",
    "name_display": "企业显示名（可读性用，不入模）",
    "dataset_ver": "数据集版本号（口径变更必须升版，保证实验可复现）",
}

# 表专属字段
TABLE_NOTES = {
    "ads_feature_asof": {
        "as_of_policy": "特征取值时点口径（期初无后视，如 t0 / t_minus_1）",
        "feature_name": "特征名（对应宽表列名）",
        "is_available": "该时点该特征是否有值（1/0），显式标记缺失而非留 NULL",
    },
    "ads_graph_node": {
        "node_type": "节点类型 5 类：enterprise（企业）/ patent（专利）/ ipc（分类号）/ inventor（发明人）/ investor（投资机构）",
        "node_id": (
            "节点 ID，各类型口径不同："
            "enterprise = `ENT`+12 位大写 hex（同 dim_enterprise.enterprise_id）；"
            "patent = fact_patent.patent_id（中国专利为 CN 开头公开号，另有 4 件 JP/TW 公开号，"
            "为真实国外同族专利，非脏值）；"
            "ipc = IPC 分类号（部+大类+小类+大组/小组，已剥 WIPO 版本号后缀如 (2006.01)；"
            "另有 5 个节点仅到小类 A61B/A61K/A61P/C07D/C07K，源串截断无法恢复，保留原样）；"
            "inventor = `INV`+10 位 hex（同 dim_inventor.inventor_id）；"
            "investor = `IVS`+10 位 hex（源数据无机构主键，由规范化机构名 md5 派生）"
        ),
        "display_name": "展示名（企业名 / 专利标题 / IPC 小类标题 / 发明人姓名 / 投资机构名）",
    },
    "ads_graph_edge": {
        "src_type": "起点节点类型（枚举同 ads_graph_node.node_type）",
        "src_id": "起点节点 ID（格式见 ads_graph_node.node_id）",
        "dst_type": "终点节点类型（枚举同 ads_graph_node.node_type）",
        "dst_id": "终点节点 ID（格式见 ads_graph_node.node_id）",
        "edge_type": (
            "边语义。CHECK 保留 8 个枚举位，本轮实际产出 5 类："
            "files_patent（企业→专利，weight=匹配置信度）/ "
            "belongs_ipc（专利→IPC，weight=1.0 主分类、0.5 副分类）/ "
            "invented_by（专利→发明人，weight=1.0 第一发明人、0.5 其他）/ "
            "funded_by（企业→投资机构，weight=1.0）/ "
            "tech_similar（企业↔企业，weight=IPC 小类 TF-IDF 余弦，每企业 top-20、≥0.05）。"
            "未产出：co_inventor / cites / qualifies（缺源数据或由 invented_by 覆盖）"
        ),
        "time_slice": "时间切片。当前全表为 0 = 全期累计；年度切片不物化，按 app_year<=T 自建",
        "weight": "边权（0~1，语义随 edge_type 变化，见 edge_type 说明）",
    },
    "ads_label": {
        "label_value": "标签值：1=观察窗内未通过复核（正类）/ 0=通过或续存",
        "observation_start": "观察窗起点（首次认定年）",
        "observation_end": "观察窗终点（到期年）",
        "n_events": "观察窗内相关事件数",
        "note": "备注：口径说明与人工审计结论",
    },
    "ads_model_dataset": {
        "name_display": "企业显示名（训练集可读性用，不入模）",
        "label": "目标标签（ads_label.label_value 快照）",
        "split": "冻结切分 train/valid/test，按企业冻结防泄漏",
    },
    "ads_survival_period": {
        "event": "结局事件：1=本期发生「未通过复核」；0=续存或右删失",
    },
    "ext_msme_sample": {
        "sample_id": "对照样本行号（1–1707）",
    },
    "bridge_patent_inventor": {
        "patent_id": "FK → fact_patent",
        "inventor_id": "FK → dim_inventor",
        "seq": "发明人署名顺序（1 开始）",
        "is_first": "是否第一发明人（1/0）",
    },
    "bridge_patent_ipc": {
        "patent_id": "FK → fact_patent",
        "ipc_code": "完整 IPC 分类号",
        "ipc_subclass": "IPC 小类（前 4 位，如 H01M）",
        "is_main": "是否主分类号（1/0）",
        "seq": "分类号在专利上的顺序",
    },
    "dim_enterprise": {
        "enterprise_type": "企业类型（工商登记类型）",
        "last_qual_year": "最近一次资质认定年份",
        "is_listed": "是否上市（1/0，本库仅 2 家为 1）",
        "has_website": "是否有官网（1/0）",
        "has_wiki": "是否有百科词条（1/0）",
        "has_thirdparty_page": "是否有第三方工商页（1/0）",
    },
    "dim_enterprise_alias": {
        "alias_id": "别名自增主键",
        "alias_name": "别名原文（曾用名 / 专利申请人变体）",
        "alias_norm": "规范化别名（去括号空格与组织形式后缀，匹配用）",
        "confidence": "匹配置信度：人工确认=1.0，规则推导 0.6–0.9",
    },
    "dim_inventor": {
        "name_display": "发明人姓名原文",
        "name_norm": "规范化姓名（同名消歧用）",
        "first_year": "首次出现年份",
        "last_year": "最后出现年份",
        "patent_cnt": "关联专利数",
    },
    "dim_ipc": {
        "parent_code": "上级 IPC 编码（层级归属）",
        "title": "IPC 分类标题",
    },
    "dim_qual_type": {
        "issuer": "认定 / 颁发机构",
        "note": "备注（层级说明等）",
    },
    "dws_tech_feature": {
        "rd_intensity_flag": "研发强度口径标注（实测 / 代理 / 缺失）",
        "n_patent_app": "当年专利申请数",
        "n_patent_grant": "当年专利授权数",
        "n_patent_invention": "当年发明专利数",
        "n_patent_utility": "当年实用新型数",
        "n_patent_design": "当年外观设计数",
        "n_patent_cited": "被引专利数（原始计数，稀疏需对数化）｜引文库未接入，设计性 NULL",
        "n_inventor": "当年去重发明人数",
        "n_ipc_subclass": "当年覆盖的 IPC 小类数",
        "first_app_year": "首个专利申请年份（企业技术起点）",
    },
    "dws_growth_feature": {
        "m_funding": "里程碑：融资事件（0/1）",
        "m_team": "里程碑：核心团队变动（0/1）",
        "m_cert": "里程碑：资质晋级（0/1，名单差分直接产出）",
        "m_cite": "里程碑：高影响力引用（0/1）",
        "rev_cagr_flag": "营收 CAGR 口径标注（实测 / 代理 / 缺失）",
        "ever_fail_review": "是否曾复核未通过（0/1）",
        "funding_occurred": "观察期内是否发生融资（0/1）",
    },
    "fact_funding_event": {
        "source_id": "行级数据源标识：`zj_500family`（506/506 有值，A7 回填）；与 meta_data_source / meta_ingest_log 同源，属血缘列不是业务特征，勿入模",
        "event_id": "事件自增主键",
        "event_date": "事件发生日期",
        "event_year": "事件发生年份",
        "event_type": "事件类型（定增 / 融资轮次 / 股权变更）",
        "is_public_co": "事件主体是否上市公司（1/0）；非上市企业融资事件同样在表内，0 不代表数据缺失",
    },
    "fact_patent": {
        "app_no": "专利申请号",
        "title": "专利名称（100% 覆盖，可替代摘要做文本特征）",
        "app_date": "申请日",
        "pub_date": "公开日（覆盖率 94.5%）",
        "grant_date": "授权公告日（外部源缺失，用 is_granted + grant_year 替代）",
        "grant_year": "授权年份",
        "patent_type": "专利类型：发明 / 实用新型 / 外观设计",
        "abstract": "摘要原文（覆盖 2.82%，不足以做文本建模，仅作留档）",
        "n_claims": "权利要求数｜数据源未采集（0%），设计性 NULL，禁止回填",
        "family_size": "同族规模｜数据源未采集（0%），设计性 NULL，禁止回填",
        "n_cite_fwd": "前向被引数｜依赖引文库，设计性 NULL",
        "n_cite_bwd": "后向引用数｜依赖引文库，设计性 NULL",
        "is_granted": "是否已授权（1/0，覆盖率 100%）",
        "country_code": "受理国别",
        "enterprise_id": "匹配到的企业（未匹配为 NULL，血缘见 log_match）",
        "assignee_raw": "原始申请人字符串（未规范化）",
        "assignee_addr_raw": "申请人地址原文",
        "match_confidence": "名称匹配置信度",
        "match_method": "匹配方法：exact / alias / normalized / fuzzy",
    },
    "fact_patent_citation": {
        "citing_ent_id": "施引专利所属企业",
        "cited_ent_id": "被引专利所属企业",
        "cite_year": "引用发生年份",
    },
    "fact_qualification": {
        "qual_id": "资质记录自增主键",
        "qual_type": "资质类型，FK → dim_qual_type",
        "batch_no": "公示批次号",
        "publish_date": "名单公示日期",
        "status": "状态：有效 / 失效",
    },
    "fact_qualification_event": {
        "event_id": "事件自增主键",
        "event_date": "事件发生日期",
        "event_year": "事件发生年份",
        "event_type": "事件类型：upgrade / downgrade / expire / renew",
        "from_qual": "变更前资质层级",
        "to_qual": "变更后资质层级",
        "evidence_qual_id": "证据记录，FK → fact_qualification",
        "note": "事件备注",
    },
    "log_match": {
        "match_id": "匹配审计自增主键",
        "stage": "所处阶段：fetch / parse / match / build",
        "patent_id": "匹配到的专利",
        "raw_name": "待匹配的原始名称",
        "candidate_ent": "候选企业 ID",
        "method": "匹配方法",
        "score": "匹配得分（0–1）",
        "decision": "决策：auto / manual_accept / manual_reject",
        "reviewed_by": "人工复核人",
        "reviewed_at": "人工复核时间",
    },
    "meta_data_source": {
        "source_name": "数据源名称",
        "source_type": "源类型：official_list / patent_db / open_dataset / market_api / internal / manual",
        "url": "源地址",
        "fetch_mode": "获取方式：api / bulk_download / web_parse / manual / none",
        "first_fetch": "首次获取时间",
        "last_fetch": "最近获取时间",
        "is_active": "是否启用（1/0）",
    },
    "meta_ingest_log": {
        "log_id": "日志自增主键",
        "stage": "阶段：fetch / parse / match / build",
        "target_table": "写入的目标表",
        "rows_in": "读入行数",
        "rows_ok": "成功行数",
        "rows_bad": "失败行数",
        "started_at": "开始时间",
        "finished_at": "结束时间",
        "status": "状态：ok / partial / failed",
        "message": "说明与错误信息",
    },
    "ods_patent_raw": {
        "title": "专利名称原文",
        "abstract": "摘要原文（上游仅少量提供）",
        "app_date": "申请日原文",
        "pub_date": "公开日原文",
        "grant_date": "授权日原文",
        "inventor_raw": "发明人原始字符串（可能多人拼接）",
        "ipc_raw": "IPC 原始字符串（可能多个分类号）",
        "legal_status_raw": "法律状态原文（如：授权 / 未缴年费专利权终止）",
        "country_code": "受理国别",
        "row_no": "在原始文件中的行号",
        "ingested_at": "入库时间",
    },
    "ods_qual_notice": {
        "notice_id": "公示批次自增主键",
        "qual_type": "本批次资质类型",
        "publish_date": "公示发布日期",
        "source_url": "名单原文 URL",
        "fetched_at": "抓取时间",
    },
    "ods_qual_company_raw": {
        "ingested_at": "入库时间",
    },
    "dws_fusion_feature": {},
    "ads_feature_asof2": {},
}

# 建模宽表复用的特征说明（与 dws 同名同义）
FEATURE_NOTES = {
    "inv_patent_ratio": "发明专利申请量 / 专利申请总量",
    "grant_rate": "按申请年对齐的授权率（剔除未决项）",
    "patent_citation": "近 5 年被引总数｜引文库未接入，设计性 NULL（非漏填）",
    "tech_breadth_ipc_class": "IPC 小类覆盖数（技术广度·计数口径）",
    "tech_breadth_entropy": "IPC 分布 Shannon 熵（技术广度·熵口径）",
    "patent_triple_score": "专利三值评分（SciBERT+被引/同族/权利要求）｜四源全缺，设计性 NULL，见 outputs/field_gap_registry.md",
    "core_staff_stability": "核心人员稳定性 = 1 − 年流失率",
    "rd_intensity": "研发强度（替代口径=研发人员占比）",
    "rev_cagr": "3 年营收 CAGR",
    "emp_cagr": "3 年员工数 CAGR",
    "patent_cagr": "3 年专利申请 CAGR",
    "funding_to_last_val": "本轮融资额 / 上轮投后估值（非上市用资质晋级代理）",
    "investor_prestige": "投资机构知名度等级",
    "qual_rank": "资质层级（科技型→高新→专精特新→小巨人）",
    "qual_rank_delta": "资质层级年度变化",
    "qual_upgrade_cum": "累计晋级次数",
    "tech_conversion": "技术转化效率 = 营收 CAGR / 专利 CAGR",
    "cap_tech_synergy": "资本-技术协同（融资后专利产出提速，P2 暂不做）",
    "tech_growth_inter": "技术 × 成长交互项",
    "n_patent_collab": "合作申请专利数（申请人为多家）",
    **{k: v for k, v in TABLE_NOTES.get("dws_tech_feature", {}).items()},
    **{k: v for k, v in TABLE_NOTES.get("dws_growth_feature", {}).items()},
}


def note_for(table: str, column: str) -> str:
    """按 表专属 → 特征复用 → 通用 的顺序取说明。"""
    return (TABLE_NOTES.get(table, {}).get(column)
            or FEATURE_NOTES.get(column)
            or COMMON.get(column)
            or "")


# DDL 表注释缺失时的补充（DDL 里只有分隔线的情况）
TABLE_COMMENTS = {
    "ads_feature_asof": "时点特征长表：企业 × 时点 × 特征（严格无后视口径，建模主数据源）",
    "ads_survival_period": "生存分析 person-period 长表：企业 × 期次（含结局事件，替代静态口径）",
    "ods_qcc_annual": "企查查工商年报原始落地（企业 × 年报年度）。财务快照的来源表，"
                      "实测只有 2025 单年，故 rev_cagr / emp_cagr 无 ≥2 年序列可算",
    "ods_qcc_cert": "企查查资质证书原始落地（一行一张证书）。"
                    "与 CNIPA 名单口径不同：这里是企业自持证书，资质层级特征取自名单而非本表",
    "ods_qcc_shareholder": "企查查股东原始落地（一行一个股东）。"
                           "用于股权结构与企业关系归集，不参与资质存续标签构造",
}


def table_comment(table: str, ddl_comment: str) -> str:
    """人工补充优先（DDL 里注释位置不规范时，自动提取会抓到块内说明行）。"""
    return TABLE_COMMENTS.get(table) or ddl_comment


# ---------------- 视图 ----------------
# 视图没有 DDL 行内注释可解析，全部人工登记；同名特征列走 FEATURE_NOTES / COMMON 兜底。
VIEW_COMMENTS = {
    "v_asof_feature_coverage": "时点特征覆盖度（企业 × 口径 × 特征），判断某特征能不能进时点模型",
    "v_data_coverage": "数据覆盖度体检（13 个对象的记录数一览）",
    "v_enterprise_profile": "企业画像速览：主表 + 资质履历 + 专利数一行看全（不做时点切分）",
    "v_model_dataset_asof_flat": "建模宽表 + 标签，时点口径（**无后视泄漏的主入口**）；"
                                 "614 行 = 307 家 × 2 种 as_of_policy，不是 614 家企业",
    "v_model_dataset_flat": "建模宽表 + 标签，静态口径（含后视信息，仅用于对照与复现旧结论）",
    "v_patent_canonical": "专利唯一化口径（一件专利 × 一个归属企业一行），专利级聚合的唯一入口",
    "v_patent_yearly": "年度专利产出（企业 × 年），canonical 去重后按申请年切分",
    "v_qual_history": "资质履历（企业 × 资质 × 批次时间线），成长轨迹 G1 的原料",
}

VIEW_NOTES = {
    "v_asof_feature_coverage": {
        "as_of_policy": "时点口径：cohort=认定当年 / due_minus_1=到期前一年",
        "feature_name": "特征名（对应 ads_model_dataset 列名）",
        "n_sample": "该口径下参与统计的企业-时点数（分母）",
        "n_available": "其中有值的条数（is_available=1）",
        "coverage": "覆盖率 = n_available / n_sample；低于 50% 不建议进模型",
    },
    "v_data_coverage": {
        "obj": "统计对象名（表名或指标名）",
        "n": "该对象的记录数",
    },
    "v_enterprise_profile": {
        "name_display": "企业显示名",
        "enterprise_type": "企业类型（源标注原文，不做推断）",
        "first_qual_year": "首次获得科技类资质的年份",
        "last_qual_year": "最近一次获得资质的年份",
        "max_qual_rank": "历史最高资质层级（1 科技型 → 4 小巨人）",
        "n_patent": "专利总数（走 v_patent_canonical 唯一化，非 fact_patent 行数）",
        "n_invention": "发明专利数（同上，唯一化口径）",
    },
    "v_patent_canonical": {
        "app_no": "申请号，视图唯一键（一件专利一行 × 一个归属企业）",
        "patent_id": "代表公告号：组内优先取授权公告文本",
        "n_records": "该组并入了多少条原始行（1=无重复，2=申请公布+授权公告两份文本）",
        "patent_ids": "组内全部公告号（逗号分隔，供审计追溯）",
        "title": "专利名称",
        "n_distinct_titles": "组内不同标题数（>1 说明存在更名或匹配噪声）",
        "abstract": "摘要（源仅约 2.8% 提供，缺失属源上限）",
        "app_date": "申请日",
        "app_year": "申请年，as-of 切分的时间基准",
        "pub_date_first": "组内最早公开日（通常是申请公布）",
        "pub_date_last": "组内最晚公开日（通常是授权公告）",
        "patent_type": "专利类型：invention / utility / design，决定审结期长短",
        "legal_status": "法律状态（取组内最新一条）；仅授权公告文本才有值",
        "is_granted": "**此刻是否仍在权**（不等于'是否授权过'，见字典 §6）",
        "is_granted_ever": "**是否曾经获得授权**（kind code ∈ B/U/S/C 或 is_granted=1）；授权率必须用这个",
        "grant_kind": "授权公告 kind code（B/U/S/C），未授权为 NULL",
        "ipc_main": "主分类号（源覆盖 49.3%，缺的是源上限不是漏填）",
        "match_method": "企业归属匹配方法：exact / alias / fuzzy",
        "match_confidence": "匹配置信度，fuzzy 匹配低于 0.9 建议人工抽查",
    },
    "v_patent_yearly": {
        "n_app": "当年申请量（canonical 唯一化后）",
        "n_grant": "当年授权公告量（按 grant_kind 非空计，唯一化后）",
        "n_inv": "当年发明专利申请量",
        "n_uty": "当年实用新型申请量",
        "n_des": "当年外观设计申请量",
        "n_ipc": "当年涉及的 IPC 小类数（技术广度的时间切片）",
    },
    "v_qual_history": {
        "year": "获得/公示年份",
        "qual_type": "资质类型代码",
        "qual_name": "资质名称原文",
        "level_rank": "资质层级（1 科技型 → 2 高新 → 3 专精特新 → 4 小巨人）",
        "status": "状态：有效 / 失效 / 公示中",
        "is_renewal": "是否复核换证（1=换证，0=首次认定）",
        "valid_until": "有效期截止日（资质存续标签的到期基准）",
    },
    "v_model_dataset_flat": {
        "label": "建模用标签（= ads_label.label_value；split=excluded 的行填 0 占位，不可当真）",
        "split": "数据集划分：train / val / test / excluded（excluded = 观察窗未结束的假负例）",
        "split_protocol": "划分协议说明（按企业分组，避免同企业跨集泄漏）",
        "label_name": "标签中文名（论文用）",
        "label_value": "标签原值：1=观察窗内未通过复核，0=通过或续存",
    },
}

VIEW_NOTES["v_model_dataset_asof_flat"] = {
    **VIEW_NOTES["v_model_dataset_flat"],
    "label": "建模用标签（= ads_label.label_value）：1=观察窗内未通过复核，0=通过或续存",
    "split": "数据集划分：train / val / test；**不含 excluded**（观察窗未结束的企业不进本视图）",
    "as_of_policy": "特征时点口径：cohort=认定当年 / due_minus_1=到期前一年；"
                    "同一企业两行，建模须按 enterprise_id 分组切分",
    "as_of_year": "特征观测截止年（含），该行所有特征只用到这一年及以前的数据",
    "m_funding": "⚠️ 本列恒 NULL（不是漏补）：v1.6 起的融资补算落在旁路表 "
                 "ads_feature_asof_funding / 视图 v_feature_asof_funding，"
                 "主宽表 29 列结构与 ASOF_VER 绑定，禁止原地 UPDATE。"
                 "要用融资特征必须 LEFT JOIN v_feature_asof_funding，"
                 "且必须按 (enterprise_id, as_of_policy) 双键连接；"
                 "单按 enterprise_id 会把 614 行撑成 1228 行伪重复",
    "investor_prestige": "⚠️ 本列恒 NULL（不是漏补）：补算值是 "
                         "investor_prestige_best / investor_prestige_latest 两式，"
                         "在 v_feature_asof_funding；接入口径同本视图 m_funding 列，"
                         "必须按 (enterprise_id, as_of_policy) 双键 LEFT JOIN",
}

# ---------------------------------------------------------------------------
# 算法端唯一训练入口：307 行 × 17 列，一行 = 一家企业
# ---------------------------------------------------------------------------
VIEW_COMMENTS["v_model_train_ready"] = (
    "**算法端唯一训练入口**：307 行 = 307 家企业 × 1 行（固定 as_of_policy='cohort'），"
    "已物理隔离 193 行 excluded 假负例、剔除 4 个 cohort 内常量列。"
    "**不含 v1.6 融资补算特征**：需要融资特征时按 (enterprise_id, as_of_policy) "
    "双键 LEFT JOIN v_feature_asof_funding，见 v_model_dataset_asof_flat 的列说明"
)

VIEW_NOTES["v_model_train_ready"] = {
    **VIEW_NOTES["v_model_dataset_asof_flat"],
    "enterprise_id": "企业主键（ENT + 12 位 hex），全库唯一；建模切分依据",
    "name_display": "企业显示名，仅供人工核对，**不入模**",
    "region_code": "行政区划码（330100 杭州 等）；**同时是队列分层变量，勿当普通特征**",
    "cohort_year": "队列年（= 首次高企认定年）：2021 有 160 家 / 2022 有 147 家；"
                   "2021 队列正例率 5.0%，2022 为 8.8%，跨队列混训须带队列权重",
    "split": "已固定：train 214 / val 54 / test 39（企业级 md5 切分）；"
             "**禁止再按行随机切分**，否则同一企业会同时进训练与验证",
    "label": "标签（代理标签，非官方违约口径）：1=观察窗内未通过复核，0=通过或续存；"
             "全表 21 正 / 286 负（6.84%）",
    # 下列特征均为「观测截止 as_of_year 及以前」口径，无后视
    "n_patent_app": "专利申请数（canonical 去重后按申请号计）；单变量 AUC 0.3905（负相关）",
    "n_patent_invention": "发明专利申请数；单变量 AUC 0.5102（无判别力）",
    "n_patent_grant": "专利授权数；单变量 AUC 0.2788（强负相关，最有用之一）",
    "inv_patent_ratio": "发明专利占比；单变量 AUC 0.6185（正相关，最强正信号）",
    "tech_conversion": "技术转化率（曾授权 / 申请）；单变量 AUC 0.2956（强负相关）",
    "n_ipc_subclass": "IPC 小类数（技术领域广度）；单变量 AUC 0.4868（无判别力）",
    "tech_breadth_entropy": "IPC 分布香农熵；单变量 AUC 0.4817（无判别力）",
    "tech_breadth_ipc_class": "IPC 大类数；单变量 AUC 0.4868（无判别力）",
    "n_inventor": "发明人数；单变量 AUC 0.3825（负相关）",
    "grant_rate": "as-of 授权率（曾授权 + 审结期过滤）；单变量 AUC 0.5185（无判别力）。"
                  "⚠️ 2021 队列 160 家**全为 1.0**，跨队列混训会退化成队列哑变量，建议优先剔除",
    "core_staff_stability": "核心人员稳定性；覆盖率仅 78.2%，单变量 AUC 0.5227（无判别力）",
}


# ---------------------------------------------------------------------------
# v1.4 口径诊断视图（依据数据交付包评审报告 XM-2026-RPT-001）
# 它们不替换任何既有视图，只用来回答「这个数到底该信哪个」。
# ---------------------------------------------------------------------------
VIEW_COMMENTS["v_patent_master"] = (
    "**专利唯一对外口径**：一行 = 一个申请号（30,103 行）。"
    "对外报「专利件数」一律 COUNT(*) 本视图；fact_patent 是 32,969 行（A/B 两份文本各一行），"
    "v_patent_canonical 是 30,346 行（一件专利 × 一个归属企业）"
)
VIEW_NOTES["v_patent_master"] = {
    "app_no": "申请号，本视图唯一键（一件专利一行，不再按企业拆行）",
    "patent_id_repr": "代表公告号：组内最小公告号，仅作追溯用",
    "enterprise_id": "归属企业（多个归属时取任一家，具体见 join_status=multi_assignee）",
    "n_enterprise": "该申请号归属几家企业（1=唯一，>1=同族跨企业申报）",
    "join_status": "matched=已匹配单一企业 / multi_assignee=同一申请号归属多家企业 / "
                   "unmatched=未匹配进 500 户清单",
    "title": "专利名称",
    "abstract": "摘要（源仅约 851 个申请号提供，缺失属源上限，不可回填）",
    "has_abstract": "1=该申请号有摘要（851 个）/ 0=无",
    "app_date": "申请日",
    "app_year": "申请年，年度切片与 as-of 切分的时间基准",
    "is_granted_ever": "**曾授权**（≠ 当前在权；算授权率必须用这一列）",
    "grant_kind": "授权公告的 kind code（B/U/S/C），无授权则为 NULL",
    "patent_type": "专利类型：invention / utility / design",
    "ipc_main": "主 IPC 分类号",
    "n_records": "该申请号在 fact_patent 里并入的原始行数（1 或 2）",
}

VIEW_COMMENTS["v_label_mechanism"] = (
    "标签机制台账：n_events × cohort 的正例分布。"
    "**用来钉住一个事实：n_events=1 的 21 家 100% 正例、n_events>=2 的 286 家 0% 正例，"
    "标签≈「是否只被认定过一次」** —— n_events 绝不可进特征"
)
VIEW_NOTES["v_label_mechanism"] = {
    "n_events": "资质认定次数（1 次 → 全为正例；>=2 次 → 全为负例）",
    "cohort_year": "队列年（首次认定年）",
    "n_enterprise": "该组企业数",
    "n_positive": "该组正例数",
    "n_negative": "该组负例数",
    "positive_rate": "正例率；本表的设计用途就是让「完全分离」一眼可见",
}

VIEW_COMMENTS["v_model_train_ready_cv"] = (
    "**交叉验证推荐口径**：在 v_model_train_ready 基础上加 fold_id（5 折、按 label 分层、"
    "企业不跨折，每折正例 4~5 个）。原 split 的 val 只有 2 个正例，早停/阈值不可靠"
)
VIEW_NOTES["v_model_train_ready_cv"] = {
    **VIEW_NOTES["v_model_train_ready"],
    "fold_id": "1..5，按 label 分层后轮转分配；每折正例 >= 4，企业不跨折。"
               "只做 CV 不做独立测试集时用它，不要再用 split",
}

VIEW_COMMENTS["v_feature_missing_reason"] = (
    "特征缺失原因（仅列 is_available=0 的行）：把「缺多少」升级为「为什么缺」，"
    "避免把「源没接入」和「该企业确实没有」混为一谈"
)
VIEW_NOTES["v_feature_missing_reason"] = {
    "n_patent_observable": "按申请年且 as-of 时点已公开可观测的专利数（pub_date_last<=as_of 或为空）",
    "feature_name": "特征名（对应 ads_feature_asof.feature_name）",
    "enterprise_id": "企业主键",
    "as_of_policy": "时点口径：cohort=认定当年 / due_minus_1=到期前一年",
    "as_of_year": "特征观测截止年（含）",
    "missing_reason": "source_not_integrated=源数据未接入或经决策保持 NULL（见缺口登记册）/ "
                      "no_patent_in_window=观察窗 2019..as_of 内无专利 / "
                      "insufficient_sample=其他原料不足（如授权未过审结期）",
    "n_patent_by_appyear": "申请年 <= as_of 的专利数（不看公开日）",
    "n_patent_in_window": "观察窗 2019..as_of 内的专利数（ETL 实际使用的口径）",
}

VIEW_COMMENTS["v_graph_coverage"] = (
    "图谱覆盖体检：每家企业各类边数一行看全，替代人工 COUNT（评审 §5.1 的复算入口）"
)
VIEW_NOTES["v_graph_coverage"] = {
    "enterprise_id": "企业主键",
    "name_display": "企业显示名",
    "n_patent_edge": "files_patent 出边数（= 匹配到该企业的专利件数）",
    "n_ipc_subclass": "该企业专利覆盖的 IPC 小类数",
    "n_inventor": "该企业专利涉及的发明人数（v1.4 起已入图）",
    "n_tech_similar": "tech_similar 边数（只算 top-20 邻居，无向边计一次）",
    "n_investor": "funded_by 出边数（投资机构数，v1.4 起已入图）",
}

VIEW_COMMENTS["v_graph_patent_timeline"] = (
    "年度切片规模台账：给算法端按 T 自建时序图用（口径 app_year <= T）。"
    "预置图仍是全期单切片（time_slice=0），本视图只回答「每个 T 有多少料」"
)
VIEW_NOTES["v_graph_patent_timeline"] = {
    "time_slice": "切片年 T（= 专利申请年上限）",
    "n_patent": "该年新增申请号数",
    "n_patent_matched": "该年新增且已匹配企业的申请号数",
    "n_enterprise": "该年有专利申请的企业数（当年口径，非累计）",
    "n_patent_cum": "截至 T 的累计申请号数（= T 切片图的节点规模）",
    "n_patent_matched_cum": "截至 T 的累计已匹配申请号数（= T 切片 files_patent 边规模）",
}

VIEW_COMMENTS["v_train_ready_window_fixed"] = (
    "等长观察窗复算表：把两个队列统一压到 as_of 前 3 年再比。"
    "**队列专利数差异约有一半来自窗口长度（2021 只有 3 年、2022 有 4 年），不是技术能力**"
)
VIEW_NOTES["v_train_ready_window_fixed"] = {
    "enterprise_id": "企业主键",
    "cohort_year": "队列年",
    "label": "标签（同 v_model_train_ready）",
    "n_patent_3y": "cohort_year-2 .. cohort_year 三年内的申请号数（等长口径）",
    "n_invention_3y": "同三年内的发明申请数",
    "n_patent_window": "2019 .. cohort_year 的申请号数（= ETL 原口径，队列间不等长）",
    "inv_patent_ratio_3y": "等长口径的发明专利占比",
}

VIEW_COMMENTS["v_feature_discrimination"] = (
    "特征判别力分队列复算（评审 §3.3 / §4.2 的落地）：一行 = 一个特征 × 一个样本范围。"
    "AUC 用 Mann-Whitney U 平均秩等价式算，与 sklearn roc_auc_score 数值一致。"
    "**全样本 AUC 会混入队列效应，引用单一特征效应前必须先看本视图的分队列值**"
)
VIEW_NOTES["v_feature_discrimination"] = {
    "cohort_scope": "样本范围：'all' = 全样本 307 家；'2021' / '2022' = 该队列单独算",
    "feature_name": "特征名（对应 v_model_train_ready 列名）",
    "n_all": "该范围内的样本企业数（含特征缺失者）",
    "n_missing": "该范围内特征为 NULL 的企业数",
    "n_valid": "实际参与 AUC 计算的企业数（= n_all − n_missing）",
    "n_pos": "参与计算的正例数",
    "n_neg": "参与计算的负例数",
    "auc": "单变量 AUC（0.5 = 无判别力；<0.5 表示数值越小越可能是正例）",
    "mean_pos": "正例均值（仅有效样本）",
    "mean_neg": "负例均值（仅有效样本）",
    "v_min": "该范围该特征最小值",
    "v_max": "该范围该特征最大值",
    "stability_note": "稳定性警示：正例 < 10 时标注「AUC 不稳，只作方向参考」",
}

TABLE_NOTES["ads_feature_asof_funding"] = {
    "enterprise_id": "企业主键；与 as_of_policy 共同构成 JOIN 双键（单独用它连接会产生伪重复）",
    "m_funding": "窗口内是否有融资事件（0/1；0=有观测无事件，与 NULL 语义不同）",
    "n_funding_events": "窗口内融资事件数（评审报告 §2.3(3)「融了几轮」的落地）",
    "investor_prestige_best": "窗口内最好机构等级（1=头部 VC … 4=个人天使；NULL=无带等级事件）",
    "investor_prestige_latest": "最近一次带等级事件的机构等级（NULL=无带等级事件）",
    "as_of_policy": "口径同 ads_feature_asof：cohort=认定前 / due_minus_1=到期前一年；"
                    "**与主宽表连接时必须同时匹配 enterprise_id 与 as_of_policy**",
    "as_of_year": "观测截止年；窗口=event_year <= as_of_year",
}

TABLE_COMMENTS["ads_feature_asof_funding"] = (
    "融资特征 as-of 补算表（v1.6）：m_funding / n_funding_events / 机构等级两式，"
    "614 行 = 307 家 × 2 口径。**这是旁路表，不回写主宽表**："
    "v_model_dataset_asof_flat 的 m_funding / investor_prestige 仍恒 NULL（设计，不是漏补），"
    "必须用 (enterprise_id, as_of_policy) 双键 LEFT JOIN v_feature_asof_funding 才能取到值；"
    "禁止原地 UPDATE 主宽表 29 列结构（ASOF_VER 升版规则）。"
    "覆盖 MNAR：cohort 60/307、due_minus_1 95/307，入模须带是否披露的指示意识"
)

VIEW_COMMENTS["v_feature_asof_funding"] = (
    "融资特征 as-of 查询视图（v1.6）：m_funding / n_funding_events / 机构等级两式。"
    "覆盖有限（cohort 口径 60/307 家），MNAR；不进 v_model_train_ready，"
    "**接入口径：SELECT d.*, f.m_funding AS m_funding_asof FROM "
    "v_model_dataset_asof_flat d LEFT JOIN v_feature_asof_funding f "
    "ON f.enterprise_id=d.enterprise_id AND f.as_of_policy=d.as_of_policy**"
    "（双键缺一不可，单键会把 614 行撑成 1228 行）"
)
VIEW_NOTES["v_feature_asof_funding"] = TABLE_NOTES["ads_feature_asof_funding"]

VIEW_COMMENTS["v_feature_cohort_gap"] = (
    "队列反差台账：把「全样本看起来有用、拆开就散架」的特征单独拎出来。"
    "|AUC₂₀₂₁ − AUC₂₀₂₂| ≥ 0.20 判为队列间不一致，此类特征的全样本 AUC 不得单独引用"
)
VIEW_NOTES["v_feature_cohort_gap"] = {
    "feature_name": "特征名",
    "n_pos_all": "全样本正例数（有值者）",
    "auc_all": "全样本 AUC",
    "n_pos_2021": "2021 队列正例数",
    "auc_2021": "2021 队列 AUC",
    "n_pos_2022": "2022 队列正例数",
    "auc_2022": "2022 队列 AUC",
    "auc_gap_21_22": "两队列 AUC 之差（取 2021 − 2022，正负号有意义）",
    "gap_vs_full_2021": "2021 队列偏离 0.5 的程度 − 全样本偏离 0.5 的程度",
    "gap_vs_full_2022": "2022 队列偏离 0.5 的程度 − 全样本偏离 0.5 的程度",
    "verdict": "判定：队列差距 ≥0.20 记「全样本 AUC 不可单独引用」，否则「基本一致」",
}


VIEW_COMMENTS["v_enterprise_grade_l0"] = (
    "企业「技术与资质梯队分档」L0 规则基线（v1.7，方案 §4 / §6 P0）：字典序分层 "
    "E 预警 → D 观察 → A 引领 → B 优质 → C 稳健，实测 44/95/275/83/3 家合计 500。"
    "这是可审计对照基线，不是信用好坏结论；不是 as-of 训练口径，不得入模验证；"
    "A/B 档判据用了第三方等级，禁止再用同字段反向验证（循环定义）。"
    "admin_penalty_cnt 语义与字段名不符，已排除在判据外。"
)
VIEW_NOTES["v_enterprise_grade_l0"] = {
    "enterprise_id": "企业代理键 ENT+12 位 hex；一家一行，覆盖 dim_enterprise 全部 500 家",
    "grade": "五档中文名：A 引领 / B 优质 / C 稳健 / D 观察 / E 预警（档次是输出，不是标签）",
    "grade_code": "档位单字母码 A–E，便于排序与分组聚合",
    "hit_rule": "实际命中的判据（如 E:严重失信 / D:风险负面 / A:小巨人 / C:兜底），可审计优先级",
    "serious_dishonesty_cnt": "判据输入：严重失信计数，>0 一票定 E 档（原字段 COALESCE 为 0）",
    "biz_anomaly_cnt": "判据输入：经营异常计数，>0 一票定 E 档",
    "biz_status": "判据输入：工商经营状态；非 NULL 且不等于「开业」一票定 E 档",
    "risk_negative_cnt": "判据输入：风险负面计数，>0 进 D 档（优先级低于 E）",
    "is_little_giant": "判据输入：是否小巨人；=1 且未命中 E/D 时进 A 档",
    "qcc_tech_level_ord": "判据输入：第三方技术等级序数（1–5）；>=5 进 A 档，=4 且信用等级>=11 进 B 档",
    "qcc_credit_level_ord": "判据输入：第三方信用等级序数（1–12）；仅与技术等级=4 组合判定 B 档",
    "label_value_qual_survival": "判据输入：qual_survival 弱标签；1=资质断档信号进 D 档；-1=未打标的 193 家，不参与 D 档判定，不是负例",
    "grade_ver": "分档规则版本 l0_v1.0；判据或口径变更必须升版，禁止跨版本比较实验结论",
    "scope_note": "使用边界声明：静态快照规则基线，非 as-of 训练口径，禁止入模验证，禁止用第三方等级反向验证",
}


# ---------------------------------------------------------------------------
# v1.8 口径一致性三视图
# ---------------------------------------------------------------------------
VIEW_COMMENTS["v_feature_coverage_effective"] = (
    "有效特征覆盖视图（v1.8）：修 v_asof_feature_coverage 只扫 ads_feature_asof "
    "而把 m_funding / investor_prestige 报成 0 的口径分叉。按 "
    "(as_of_policy, feature_name) 用旁路表实测值替换这两列，其余 12 个零覆盖特征"
    "保持 0（真无数据源，不造值）。原视图保持不动，已发布契约与 ASOF_VER 不受影响。"
    "取数仍须按双键 LEFT JOIN v_feature_asof_funding；本视图只用于回答「覆盖多少」，"
    "不直接产出建模特征。"
)
VIEW_NOTES["v_feature_coverage_effective"] = {
    "as_of_policy": "特征切面口径：cohort=认定前 / due_minus_1=到期前一年；一次实验固定单一口径",
    "feature_name": "特征名；与 ads_feature_asof 的 29 个特征一一对应，一行一个 (口径, 特征)",
    "n_sample": "该口径下的样本行数（307 家企业 × 1 行）",
    "n_available": "实际非空的特征行数；旁路两列取 ads_feature_asof_funding 实测值，其余取主宽表",
    "coverage": "n_available / n_sample，四位小数；0 表示该特征在该口径下完全不可用",
    "coverage_source": "覆盖值来源：side_table=旁路表补算 / main_wide_table=as-of 主宽表",
    "source_object": "具体取数对象（表名.列名），便于反向对账",
    "requires_left_join": "1=该特征的真实值在旁路表，必须双键 LEFT JOIN 才能取到；0=主宽表原生列",
}

VIEW_COMMENTS["v_graph_slice_annual"] = (
    "图谱年度切片规模台账（v1.8，评审报告 §5.2(1) / §5.3）：按 "
    "fact_patent.app_year <= T 累计，29 个年份 × 4 个指标共 116 行。"
    "关联键用 fact_patent.patent_id（全量 32,969 行），不用已去重的 "
    "v_patent_master.patent_id_repr（会漏 19% 的 IPC / 发明人边）。"
    "物理 ads_graph_node/edge.time_slice 仍全为 0 = 全期累计（既有契约口径，"
    "build_graph_v2 幂等重建会写回 0），年度切片一律走本组视图，禁止 UPDATE 物理表。"
    "末年 belongs_ipc 累计 51,795 与预置图 51,776 差 19 条，来自 IPC 版本号归一后并边。"
)
VIEW_NOTES["v_graph_slice_annual"] = {
    "time_slice": "切片年份 T（1992–2026，共 29 个有专利申请的年份）",
    "metric": "指标名：n_node_patent / n_edge_files_patent / n_edge_belongs_ipc / n_edge_invented_by",
    "n_in_year": "该年份新增的节点或边数（按申请年分组）",
    "n_cumulative": "截至该年（含）的累计节点或边数，单调不减；末年可与 ads_graph_edge 逐类型对账",
}

VIEW_COMMENTS["v_graph_slice_annual_edge"] = (
    "图谱年度切片边级视图（v1.8）：README「自建年度切片」三条 SQL 的库内落地版，"
    "把 files_patent / belongs_ipc / invented_by 三类边按 T 展开。"
    "IPC 节点 ID 按 _norm_ipc 口径剥掉 WIPO 版本号后缀，与预置图节点对齐。"
    "行数随 T 累计增长（末级约 16 万行，全视图为各 T 叠加），查询务必带 "
    "WHERE time_slice = :T；视图是惰性的，不物化、不占库文件空间。"
)
VIEW_NOTES["v_graph_slice_annual_edge"] = {
    "time_slice": "切片年份 T；查询必须带此条件，否则会把 29 个 T 的边全部展开",
    "src_type": "源节点类型：enterprise / patent",
    "src_id": "源节点 ID（企业 ENT+12hex 或专利公开号）",
    "dst_type": "目标节点类型：patent / ipc / inventor",
    "dst_id": "目标节点 ID；IPC 已剥 WIPO 版本号后缀，与 ads_graph_node 对齐",
    "edge_type": "边类型：files_patent / belongs_ipc / invented_by（三类均可按年份切片）",
    "weight": "边权：files_patent=1.0；belongs_ipc / invented_by 主项 1.0、次要项 0.5",
}


def view_note_for(view: str, column: str) -> str:
    """视图列说明：视图专属 → 特征复用 → 通用。"""
    return (VIEW_NOTES.get(view, {}).get(column)
            or FEATURE_NOTES.get(column)
            or COMMON.get(column)
            or "")


def view_comment(view: str) -> str:
    return VIEW_COMMENTS.get(view, "")

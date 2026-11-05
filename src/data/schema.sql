-- ============================================================================
-- 浙江中小科技企业信用智能评估 · 标准数据库 Schema
-- ----------------------------------------------------------------------------
-- 引擎   : SQLite 3.37+（STRICT tables，类型强校验）
-- 文件   : data/db/zjtech_credit.db
-- 版本   : v1.0（2026-10-14）
-- 分层   : meta 元数据治理 / ods 原始落库 / dim 维度主数据
--          fact 业务事实 / dws 特征汇总 / ads 应用层（标签·建模宽表·图）
-- 口径契约: docs/特征工程字典.md（15 个指标 → 见 docs/数据字典-标准数据库.md §5）
-- ----------------------------------------------------------------------------
-- 设计约定
--   1. 代理键统一前缀：企业 ENT+12hex、专利用公开号、发明人 INV+10hex、IPC 用原始码
--   2. 日期一律 TEXT 'YYYY-MM-DD'；年月一律 TEXT 'YYYY-MM'；年份 INT
--   3. 主键冲突一律不静默忽略：批量写入前先落 ods，再由 DWD 清洗去重
--   4. 枚举值用 CHECK 约束固化，禁止自由文本（口径漂移是数据质量问题第一来源）
--   5. 连接后必须 `PRAGMA foreign_keys = ON`（SQLite 默认关闭）
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ============================================================================
-- 一、meta：元数据与数据治理（溯源·合规·可复现）
-- ============================================================================

-- 架构版本：每次改 DDL 必须升版本并留痕
CREATE TABLE IF NOT EXISTS meta_schema_version (
    version     TEXT NOT NULL,              -- 语义化版本，如 'v1.0'
    applied_at  TEXT NOT NULL,              -- ISO8601
    author      TEXT,                       -- 变更人
    note        TEXT,                       -- 变更说明
    PRIMARY KEY (version)
) STRICT;

-- 数据源登记：每个数据源一条，记录授权与成本（预算红线核查依据）
CREATE TABLE IF NOT EXISTS meta_data_source (
    source_id    TEXT NOT NULL,             -- 'zj_tech_sme' / 'google_patents' / 'kaggle_msme' ...
    source_name  TEXT NOT NULL,
    source_type  TEXT NOT NULL CHECK (source_type IN
                     ('official_list','patent_db','open_dataset','market_api','internal','manual')),
    url          TEXT,
    license      TEXT,                      -- 'CC BY 4.0' / 'CC BY-NC 4.0' / 'public' / 'internal'
    fetch_mode   TEXT CHECK (fetch_mode IN ('api','bulk_download','web_parse','manual','none')),
    cost_cny     REAL NOT NULL DEFAULT 0,   -- 采购成本，0=免费源
    quota_note   TEXT,                      -- 配额/频率限制说明
    owner        TEXT,                      -- 主对接人
    coverage     TEXT,                      -- 覆盖的指标范围
    first_fetch  TEXT,
    last_fetch   TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    PRIMARY KEY (source_id)
) STRICT;

-- 采集日志：每次抓取/下载留一条，含文件哈希（保证实验可复现）
CREATE TABLE IF NOT EXISTS meta_ingest_log (
    log_id        INTEGER PRIMARY KEY,
    source_id     TEXT NOT NULL REFERENCES meta_data_source(source_id),
    stage         TEXT NOT NULL CHECK (stage IN ('fetch','parse','match','build')),
    target_table  TEXT,
    file_path     TEXT,
    file_sha256   TEXT,                     -- 原始文件哈希，复现校验用
    rows_in       INTEGER,
    rows_ok       INTEGER,
    rows_bad      INTEGER,
    started_at    TEXT,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','warn','fail')),
    message       TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_ingest_source ON meta_ingest_log(source_id, started_at);


-- ============================================================================
-- 二、ods：原始落库层（保持与来源同构，不做业务判断）
-- ============================================================================

-- 公示公告登记：每批次一条
CREATE TABLE IF NOT EXISTS ods_qual_notice (
    notice_id      INTEGER PRIMARY KEY,
    qual_type      TEXT NOT NULL CHECK (qual_type IN
                      ('tech_sme','high_tech','specialized','little_giant')),
    batch_no       TEXT NOT NULL,           -- '2025-2' / '2021'
    publish_date   TEXT,
    year           INTEGER,                 -- 归属年度（=认定/备案年度）
    region_code    TEXT,                    -- '330000' 浙江省
    region_name    TEXT,
    issuer         TEXT,                    -- 发文单位
    source_url     TEXT,
    source_id      TEXT REFERENCES meta_data_source(source_id),
    file_path      TEXT,
    raw_payload    TEXT,                    -- JSON：公告原文关键字段
    fetched_at     TEXT
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_qual_notice ON ods_qual_notice(qual_type, batch_no, region_code);

-- 名单企业原始行：一行一家企业，保持来源原貌（不做名称规范化）
CREATE TABLE IF NOT EXISTS ods_qual_company_raw (
    raw_id          INTEGER PRIMARY KEY,
    notice_id       INTEGER NOT NULL REFERENCES ods_qual_notice(notice_id),
    seq_no          INTEGER,                -- 名单序号
    company_name    TEXT NOT NULL,          -- 原文企业名（未规范化）
    region_raw      TEXT,                   -- 原文辖区
    raw_payload     TEXT,                   -- JSON：多出的原始列
    ingested_at     TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_qual_raw_notice ON ods_qual_company_raw(notice_id);
CREATE INDEX IF NOT EXISTS idx_qual_raw_name   ON ods_qual_company_raw(company_name);

-- 专利原始行：BigQuery 导出 / CNIPA 批量包的直接落库（含原始申请人字符串）
CREATE TABLE IF NOT EXISTS ods_patent_raw (
    raw_id             INTEGER PRIMARY KEY,
    pub_no             TEXT,                -- 公开号
    app_no             TEXT,                -- 申请号
    title              TEXT,
    abstract           TEXT,
    app_date           TEXT,
    pub_date           TEXT,
    grant_date         TEXT,
    assignee_raw       TEXT,                -- 原始申请人（可能多个，原文）
    assignee_addr_raw  TEXT,                -- 原始申请人地址（用于区域筛选）
    inventor_raw       TEXT,
    ipc_raw            TEXT,
    legal_status_raw   TEXT,
    country_code       TEXT,
    source_id          TEXT REFERENCES meta_data_source(source_id),
    file_path          TEXT,
    row_no             INTEGER,
    ingested_at        TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_patent_raw_pubno   ON ods_patent_raw(pub_no);
CREATE INDEX IF NOT EXISTS idx_patent_raw_assignee ON ods_patent_raw(assignee_raw);


-- ============================================================================
-- 三、dim：维度主数据（去重后的标准实体）
-- ============================================================================

-- 资质类型字典
CREATE TABLE IF NOT EXISTS dim_qual_type (
    qual_type      TEXT NOT NULL,           -- 'tech_sme'
    qual_name      TEXT NOT NULL,           -- '科技型中小企业'
    level_rank     INTEGER NOT NULL,        -- 层级序号：1 科技型 → 4 小巨人（梯度晋级序列依据）
    valid_years    INTEGER,                 -- 有效期年数（科技型 1、高新 3、专精特新 3）
    issuer         TEXT,
    is_gradable    INTEGER NOT NULL DEFAULT 1 CHECK (is_gradable IN (0,1)),  -- 是否参与晋级序列
    note           TEXT,
    PRIMARY KEY (qual_type)
) STRICT;

-- 企业主表：全库唯一的实体锚点（surrogate key 不依赖统一社会信用代码）
CREATE TABLE IF NOT EXISTS dim_enterprise (
    enterprise_id     TEXT NOT NULL,        -- 'ENT' + md5(name_norm)[:12] 大写，确定性生成
    name_display      TEXT NOT NULL,        -- 展示名（取最近一次公示原文）
    name_norm         TEXT NOT NULL,        -- 规范化名（去省市前缀/括号备注/全半角统一）
    credit_code       TEXT,                 -- 统一社会信用代码（公开名单未提供，待补，可空）
    region_code       TEXT,
    region_name       TEXT,
    enterprise_type   TEXT CHECK (enterprise_type IN
                         ('tech_sme','high_tech','specialized','little_giant','multi','unknown')),
    first_qual_year   INTEGER,              -- 首次进入任一名单的年份
    last_qual_year    INTEGER,
    max_qual_rank     INTEGER,              -- 历史最高资质层级（= 晋级序列终点）
    is_listed         INTEGER NOT NULL DEFAULT 0 CHECK (is_listed IN (0,1)),
    stock_code        TEXT,                 -- 若上市，AKShare 代码
    patent_cnt        INTEGER,              -- 缓存：已匹配专利数（由 DWS 回填）
    -- 企业画像补充列（来自 500 家主表：工商/信用/在线存在/资讯；注册资本·参保人数多为空，可空）
    found_date              TEXT,            -- 成立日期
    found_years             REAL,            -- 成立年限(年)
    legal_rep               TEXT,            -- 法定代表人
    biz_status              TEXT,            -- 经营状态
    reg_capital             REAL,            -- 注册资本（万元，公开名单多缺）
    insured_num             INTEGER,         -- 参保人数（公开名单多缺）
    admin_penalty_cnt       INTEGER,         -- 行政管理条数
    serious_dishonesty_cnt  INTEGER,         -- 严重失信条数
    biz_anomaly_cnt         INTEGER,         -- 经营异常条数
    credit_promise_cnt      INTEGER,         -- 信用承诺条数
    has_website             INTEGER CHECK (has_website IS NULL OR has_website IN (0,1)),
    has_wiki                INTEGER CHECK (has_wiki IS NULL OR has_wiki IN (0,1)),
    has_thirdparty_page     INTEGER CHECK (has_thirdparty_page IS NULL OR has_thirdparty_page IN (0,1)),
    news_cnt                INTEGER,         -- 资讯条数
    order_award_cnt         INTEGER,         -- 订单中标
    honor_cnt               INTEGER,         -- 荣誉资质
    risk_negative_cnt       INTEGER,         -- 风险负面
    created_at        TEXT,
    updated_at        TEXT,
    PRIMARY KEY (enterprise_id)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ent_name_norm ON dim_enterprise(name_norm);
CREATE INDEX IF NOT EXISTS idx_ent_region      ON dim_enterprise(region_code);
CREATE INDEX IF NOT EXISTS idx_ent_type        ON dim_enterprise(enterprise_type);
CREATE INDEX IF NOT EXISTS idx_ent_credit_code ON dim_enterprise(credit_code);

-- 企业别名表：名称消歧的关键支撑（更名、简称、分支机构）
CREATE TABLE IF NOT EXISTS dim_enterprise_alias (
    alias_id       INTEGER PRIMARY KEY,
    enterprise_id  TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,
    alias_name     TEXT NOT NULL,
    alias_norm     TEXT NOT NULL,
    source         TEXT,                    -- 'qual_notice' / 'patent_assignee' / 'manual'
    confidence     REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    evidence       TEXT,                    -- 判定依据（如同一地址/同一发明人/人工确认）
    created_at     TEXT
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_alias ON dim_enterprise_alias(alias_norm, enterprise_id);
CREATE INDEX IF NOT EXISTS idx_alias_norm ON dim_enterprise_alias(alias_norm);

-- IPC 分类字典
CREATE TABLE IF NOT EXISTS dim_ipc (
    ipc_code    TEXT NOT NULL,              -- 完整码如 'H04L12/00'
    ipc_level   INTEGER NOT NULL,           -- 1 部 / 3 大类 / 4 小类 / 5 小组
    ipc_section TEXT,                       -- 'H'（A~H 八部）
    ipc_class   TEXT,                       -- 'H04'
    ipc_subclass TEXT,                      -- 'H04L'（技术广度按此粒度计算）
    parent_code TEXT,
    title       TEXT,
    PRIMARY KEY (ipc_code)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_ipc_subclass ON dim_ipc(ipc_subclass);
CREATE INDEX IF NOT EXISTS idx_ipc_section  ON dim_ipc(ipc_section);

-- 发明人维表：人才流动网络的节点
CREATE TABLE IF NOT EXISTS dim_inventor (
    inventor_id   TEXT NOT NULL,            -- 'INV' + md5(name_norm|addr_hash)[:10]
    name_display  TEXT NOT NULL,
    name_norm     TEXT NOT NULL,
    addr_hash     TEXT,                     -- 地址归属哈希（用于降低同名不同人误配）
    first_year    INTEGER,
    last_year     INTEGER,
    patent_cnt    INTEGER,
    PRIMARY KEY (inventor_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_inv_norm ON dim_inventor(name_norm);


-- ============================================================================
-- 四、fact：业务事实表（清洗后、已关联主数据）
-- ============================================================================

-- 资质公示事实：名单 = 事实（企业 × 资质类型 × 批次）
CREATE TABLE IF NOT EXISTS fact_qualification (
    qual_id        INTEGER PRIMARY KEY,
    enterprise_id  TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id),
    qual_type      TEXT NOT NULL REFERENCES dim_qual_type(qual_type),
    batch_no       TEXT NOT NULL,
    year           INTEGER NOT NULL,        -- 认定/备案年度（时间维主键）
    publish_date   TEXT,
    region_code    TEXT,
    status         TEXT NOT NULL DEFAULT 'announced' CHECK (status IN
                     ('announced','approved','removed','failed_review')),
    is_renewal     INTEGER NOT NULL DEFAULT 0 CHECK (is_renewal IN (0,1)),  -- 是否复评/重新认定
    valid_until    TEXT,                    -- 有效期截止（= year + valid_years）
    notice_id      INTEGER REFERENCES ods_qual_notice(notice_id),
    raw_id         INTEGER REFERENCES ods_qual_company_raw(raw_id),
    match_score    REAL                     -- 名称匹配分（1.0=精确）
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_qual ON fact_qualification(enterprise_id, qual_type, batch_no);
CREATE INDEX IF NOT EXISTS idx_qual_ent  ON fact_qualification(enterprise_id, year);
CREATE INDEX IF NOT EXISTS idx_qual_year ON fact_qualification(year, qual_type);

-- 资质差分事件（派生表，成长轨迹 G1「资质晋级序列」的直接来源）
CREATE TABLE IF NOT EXISTS fact_qualification_event (
    event_id       INTEGER PRIMARY KEY,
    enterprise_id  TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id),
    event_date     TEXT NOT NULL,
    event_year     INTEGER NOT NULL,
    event_type     TEXT NOT NULL CHECK (event_type IN
                     ('first_grant','renew','expire','upgrade','downgrade','fail_review')),
    from_qual      TEXT REFERENCES dim_qual_type(qual_type),
    to_qual        TEXT REFERENCES dim_qual_type(qual_type),
    rank_delta     INTEGER,                 -- 层级变化（upgrade=+n, expire=-n）
    evidence_qual_id INTEGER REFERENCES fact_qualification(qual_id),
    note           TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_qual_evt_ent ON fact_qualification_event(enterprise_id, event_year);
CREATE INDEX IF NOT EXISTS idx_qual_evt_typ ON fact_qualification_event(event_type, event_year);

-- 专利事实表
CREATE TABLE IF NOT EXISTS fact_patent (
    patent_id        TEXT NOT NULL,          -- = pub_no
    app_no           TEXT,
    title            TEXT,
    abstract         TEXT,                   -- 供 SciBERT 向量化
    app_date         TEXT,
    app_year         INTEGER,                -- 时间维主键（与申请年对齐算授权率）
    pub_date         TEXT,
    grant_date       TEXT,
    grant_year       INTEGER,
    patent_type      TEXT CHECK (patent_type IN ('invention','utility','design')),
    legal_status     TEXT,                   -- 'granted' / 'pending' / 'expired' / 'withdrawn'
    is_granted       INTEGER NOT NULL DEFAULT 0 CHECK (is_granted IN (0,1)),
    country_code     TEXT NOT NULL DEFAULT 'CN',
    -- 申请人关联
    enterprise_id    TEXT REFERENCES dim_enterprise(enterprise_id),
    assignee_raw     TEXT,
    assignee_addr_raw TEXT,
    match_confidence REAL CHECK (match_confidence IS NULL OR
                                 (match_confidence >= 0 AND match_confidence <= 1)),
    match_method     TEXT CHECK (match_method IS NULL OR match_method IN
                        ('exact','alias','fuzzy','inventor_anchor','manual')),
    -- 质量与影响力
    n_claims         INTEGER,                -- 权利要求数
    n_cite_fwd       INTEGER,                -- 前向被引数（被后案引用）
    n_cite_bwd       INTEGER,                -- 后向引用数（引用前案）
    family_size      INTEGER,                -- 同族规模
    ipc_main         TEXT,                   -- 主 IPC
    source_id        TEXT REFERENCES meta_data_source(source_id),
    raw_id           INTEGER REFERENCES ods_patent_raw(raw_id),
    PRIMARY KEY (patent_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_pat_ent      ON fact_patent(enterprise_id, app_year);
CREATE INDEX IF NOT EXISTS idx_pat_appyear  ON fact_patent(app_year, patent_type);
CREATE INDEX IF NOT EXISTS idx_pat_type     ON fact_patent(patent_type, is_granted);
CREATE INDEX IF NOT EXISTS idx_pat_ipc      ON fact_patent(ipc_main);
CREATE INDEX IF NOT EXISTS idx_pat_match    ON fact_patent(match_confidence, match_method);

-- 专利-IPC 多值桥表
CREATE TABLE IF NOT EXISTS bridge_patent_ipc (
    patent_id  TEXT NOT NULL REFERENCES fact_patent(patent_id) ON DELETE CASCADE,
    ipc_code   TEXT NOT NULL,
    ipc_subclass TEXT,
    is_main    INTEGER NOT NULL DEFAULT 0 CHECK (is_main IN (0,1)),
    seq        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (patent_id, ipc_code)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_bpi_subclass ON bridge_patent_ipc(ipc_subclass);

-- 专利-发明人多值桥表
CREATE TABLE IF NOT EXISTS bridge_patent_inventor (
    patent_id    TEXT NOT NULL REFERENCES fact_patent(patent_id) ON DELETE CASCADE,
    inventor_id  TEXT NOT NULL REFERENCES dim_inventor(inventor_id),
    seq          INTEGER NOT NULL DEFAULT 1,
    is_first     INTEGER NOT NULL DEFAULT 0 CHECK (is_first IN (0,1)),
    PRIMARY KEY (patent_id, inventor_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_bpinv_inv ON bridge_patent_inventor(inventor_id);

-- 专利引用边（知识扩散网络）
CREATE TABLE IF NOT EXISTS fact_patent_citation (
    citing_patent_id  TEXT NOT NULL,        -- 施引专利
    cited_patent_id   TEXT NOT NULL,        -- 被引专利（可能不在样本内，故不加 FK）
    citing_ent_id     TEXT REFERENCES dim_enterprise(enterprise_id),
    cited_ent_id      TEXT REFERENCES dim_enterprise(enterprise_id),
    cite_year         INTEGER,
    PRIMARY KEY (citing_patent_id, cited_patent_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_cite_citing ON fact_patent_citation(citing_ent_id, cite_year);
CREATE INDEX IF NOT EXISTS idx_cite_cited  ON fact_patent_citation(cited_ent_id, cite_year);

-- 融资事件表（仅上市公司子集可得，AKShare；非上市留空并诚实声明）
CREATE TABLE IF NOT EXISTS fact_funding_event (
    event_id       INTEGER PRIMARY KEY,
    enterprise_id  TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id),
    event_date     TEXT NOT NULL,
    event_year     INTEGER NOT NULL,
    event_type     TEXT NOT NULL CHECK (event_type IN
                     ('private_placement','ipo','convertible_bond','seasoned_equity','other')),
    amount_cny     REAL,                    -- 融资额（元）
    round_label    TEXT,                    -- 轮次标签（非上市通常为空）
    post_valuation REAL,                    -- 投后估值
    investor_names TEXT,                    -- JSON 数组，投资机构名单
    prestige_rank  INTEGER,                 -- 机构知名度等级（1 头部 VC … 4 个人天使）
    source_id      TEXT REFERENCES meta_data_source(source_id),
    is_public_co   INTEGER NOT NULL DEFAULT 1 CHECK (is_public_co IN (0,1))
) STRICT;

CREATE INDEX IF NOT EXISTS idx_fund_ent ON fact_funding_event(enterprise_id, event_year);

-- 匹配日志：名称消歧的审计轨迹（论文可写匹配准确率）
CREATE TABLE IF NOT EXISTS log_match (
    match_id      INTEGER PRIMARY KEY,
    stage         TEXT NOT NULL CHECK (stage IN ('patent_assignee','qual_company')),
    patent_id     TEXT,
    raw_id        INTEGER REFERENCES ods_qual_company_raw(raw_id),
    raw_name      TEXT NOT NULL,
    candidate_ent TEXT REFERENCES dim_enterprise(enterprise_id),
    method        TEXT NOT NULL,
    score         REAL,
    decision      TEXT NOT NULL DEFAULT 'pending' CHECK (decision IN
                    ('accepted','rejected','pending')),
    reviewed_by   TEXT,
    reviewed_at   TEXT,
    created_at    TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_match_decision ON log_match(decision, stage);
CREATE INDEX IF NOT EXISTS idx_match_raw      ON log_match(raw_name);


-- ============================================================================
-- 五、dws：特征汇总层（供图谱与模型直接消费）
-- ============================================================================

-- 技术要素特征（粒度：企业 × 年度）
CREATE TABLE IF NOT EXISTS dws_tech_feature (
    enterprise_id          TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id),
    stat_year              INTEGER NOT NULL,
    -- 结构（对应 特征工程字典 3.1–3.6）
    inv_patent_ratio       REAL,            -- 3.1 发明专利占比
    grant_rate             REAL,            -- 3.2 授权率（按申请年对齐，剔除未决）
    patent_citation        REAL,            -- 3.3 被引（近 5 年，含年代衰减可选）
    tech_breadth_ipc_class INTEGER,         -- 3.4 技术广度·IPC 小类覆盖数
    tech_breadth_entropy   REAL,            -- 3.4 技术广度·Shannon 熵
    patent_triple_score    REAL,            -- 3.5 专利三值评分（SciBERT 打分均值）
    core_staff_stability   REAL,            -- 3.6 核心人员稳定性
    rd_intensity           REAL,            -- 3.7 研发投入（替代口径=研发人员占比）
    rd_intensity_flag      TEXT CHECK (rd_intensity_flag IS NULL OR rd_intensity_flag IN
                             ('reported','proxy','missing')),  -- 口径标注，敏感性分析用
    -- 原始计数（供复算与审计，不直接入模）
    n_patent_app           INTEGER,
    n_patent_grant         INTEGER,
    n_patent_invention     INTEGER,
    n_patent_utility       INTEGER,
    n_patent_design        INTEGER,
    n_patent_cited         INTEGER,
    n_inventor             INTEGER,
    n_ipc_subclass         INTEGER,
    n_patent_collab        INTEGER,         -- 合作申请专利数（≥2 申请人）
    first_app_year         INTEGER,
    computed_at            TEXT,
    PRIMARY KEY (enterprise_id, stat_year)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_dtst_year ON dws_tech_feature(stat_year);

-- 成长轨迹特征（粒度：企业 × 年度）
CREATE TABLE IF NOT EXISTS dws_growth_feature (
    enterprise_id        TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id),
    stat_year            INTEGER NOT NULL,
    -- 对应 特征工程字典 4.1–4.6
    rev_cagr             REAL,              -- 4.1 营收 CAGR（替代口径，标注）
    emp_cagr             REAL,              -- 4.2 员工 CAGR
    patent_cagr          REAL,              -- 4.3 专利 CAGR
    funding_to_last_val  REAL,              -- 4.4 新获融资额/上轮估值
    m_funding            INTEGER CHECK (m_funding IS NULL OR m_funding IN (0,1)),
    m_team               INTEGER CHECK (m_team IS NULL OR m_team IN (0,1)),
    m_cert               INTEGER CHECK (m_cert IS NULL OR m_cert IN (0,1)),
    m_cite               INTEGER CHECK (m_cite IS NULL OR m_cite IN (0,1)),
    investor_prestige    INTEGER,           -- 4.6 投资机构知名度（1 最高）
    rev_cagr_flag        TEXT CHECK (rev_cagr_flag IS NULL OR rev_cagr_flag IN
                           ('reported','proxy','missing')),
    -- 本方案新增：资质晋级序列代理（原计划 funding 的替代变量）
    qual_rank            INTEGER,           -- 当年资质层级
    qual_rank_delta      INTEGER,           -- 当年层级变化
    qual_upgrade_cum     INTEGER,           -- 累计晋级次数
    ever_fail_review     INTEGER CHECK (ever_fail_review IS NULL OR ever_fail_review IN (0,1)),
    -- 融资汇总补充列（来自 500 家主表「融资_*」聚合字段；缺则 NULL）
    funding_occurred          INTEGER CHECK (funding_occurred IS NULL OR funding_occurred IN (0,1)),
    funding_rounds            INTEGER,        -- 融资_轮次数
    funding_first_date        TEXT,          -- 融资_首次事件日
    funding_last_date         TEXT,          -- 融资_末次事件日
    funding_span_months       INTEGER,       -- 融资_事件跨度月
    funding_net_increase_wan  REAL,          -- 融资_累计净增万元
    funding_max_round_wan     REAL,          -- 融资_单轮最大万元
    funding_orgs_cnt          INTEGER,       -- 融资_参与机构数
    funding_prestige_score    REAL,          -- 融资_级别分值
    funding_top_org_level     INTEGER,       -- 融资_最高机构级别（A=1…D=4）
    funding_top_org_name      TEXT,          -- 融资_最高级别机构
    funding_orgs_list         TEXT,          -- 融资_参与机构名单（JSON 数组）
    computed_at          TEXT,
    PRIMARY KEY (enterprise_id, stat_year)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_dgst_year ON dws_growth_feature(stat_year);

-- 融合特征（粒度：企业 × 年度）
CREATE TABLE IF NOT EXISTS dws_fusion_feature (
    enterprise_id     TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id),
    stat_year         INTEGER NOT NULL,
    tech_conversion   REAL,                 -- 5.1 技术转化效率
    cap_tech_synergy  REAL,                 -- 5.2 资本技术协同度（P2）
    tech_growth_inter REAL,                 -- 本方案代理：资质晋级 × 专利增长 交互项
    computed_at       TEXT,
    PRIMARY KEY (enterprise_id, stat_year)
) STRICT;


-- ============================================================================
-- 六、ads：应用层（标签 · 建模宽表 · 图结构）
-- ============================================================================

-- 标签表：支持多任务多队列（主任务=资质存续，对照=MSME 违约）
CREATE TABLE IF NOT EXISTS ads_label (
    enterprise_id     TEXT NOT NULL,        -- 对 MSME 对照集，此处放外部样本 ID
    label_task        TEXT NOT NULL CHECK (label_task IN
                        ('qual_survival','msme_default','sba_default','survival_risk')),
    cohort_year       INTEGER NOT NULL,     -- 观察队列起始年（feature window 终点）
    label_value       INTEGER NOT NULL CHECK (label_value IN (0,1)),
    label_name        TEXT,                 -- '科技资质存续失败' 等中文名，论文用
    observation_start TEXT,
    observation_end   TEXT,
    definition_ver    TEXT,                 -- 标签定义版本（口径变更必须升版）
    n_events          INTEGER,
    note              TEXT,
    PRIMARY KEY (enterprise_id, label_task, cohort_year)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_label_task ON ads_label(label_task, cohort_year, label_value);

-- 建模宽表：模型与图谱的唯一数据入口
CREATE TABLE IF NOT EXISTS ads_model_dataset (
    enterprise_id   TEXT NOT NULL,
    dataset_ver     TEXT NOT NULL,          -- 'ds_v1.0'
    label_task      TEXT NOT NULL,
    cohort_year     INTEGER NOT NULL,
    name_display    TEXT,
    region_code     TEXT,
    label           INTEGER NOT NULL CHECK (label IN (0,1)),
    -- 特征列（与 DWS 同名列，便于横向比对；宽表由 build 脚本生成）
    inv_patent_ratio REAL, grant_rate REAL, patent_citation REAL,
    tech_breadth_ipc_class INTEGER, tech_breadth_entropy REAL,
    patent_triple_score REAL, core_staff_stability REAL, rd_intensity REAL,
    n_patent_app INTEGER, n_patent_grant INTEGER, n_patent_invention INTEGER,
    n_inventor INTEGER, n_ipc_subclass INTEGER, n_patent_collab INTEGER,
    rev_cagr REAL, emp_cagr REAL, patent_cagr REAL, funding_to_last_val REAL,
    m_funding INTEGER, m_team INTEGER, m_cert INTEGER, m_cite INTEGER,
    investor_prestige INTEGER,
    qual_rank INTEGER, qual_rank_delta INTEGER, qual_upgrade_cum INTEGER,
    tech_conversion REAL, cap_tech_synergy REAL, tech_growth_inter REAL,
    -- 切分：按 enterprise_id 分组切分（防跨集泄漏），由 build 脚本一次性写入并冻结
    split           TEXT NOT NULL CHECK (split IN ('train','val','test','excluded')),
    split_protocol  TEXT,                   -- 切分协议描述，保证可比
    built_at        TEXT,
    PRIMARY KEY (enterprise_id, dataset_ver, label_task, cohort_year)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_ds_split ON ads_model_dataset(dataset_ver, split);
CREATE INDEX IF NOT EXISTS idx_ds_label ON ads_model_dataset(label_task, label);

-- 图节点表（PyG 消费）
CREATE TABLE IF NOT EXISTS ads_graph_node (
    node_type    TEXT NOT NULL CHECK (node_type IN
                   ('enterprise','patent','ipc','inventor')),
    node_id      TEXT NOT NULL,             -- 对应各维表主键
    time_slice   INTEGER NOT NULL,           -- 年度切片
    display_name TEXT,
    PRIMARY KEY (node_type, node_id, time_slice)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_gnode_slice ON ads_graph_node(node_type, time_slice);

-- 图边表（四类语义边；人才流动为外部独立信息，是图增益的关键来源）
CREATE TABLE IF NOT EXISTS ads_graph_edge (
    src_type   TEXT NOT NULL CHECK (src_type IN ('enterprise','patent','ipc','inventor')),
    src_id     TEXT NOT NULL,
    dst_type   TEXT NOT NULL CHECK (dst_type IN ('enterprise','patent','ipc','inventor')),
    dst_id     TEXT NOT NULL,
    edge_type  TEXT NOT NULL CHECK (edge_type IN
                 ('files_patent','belongs_ipc','co_inventor','cites','qualifies','tech_similar')),
    time_slice INTEGER NOT NULL,
    weight     REAL,
    PRIMARY KEY (src_type, src_id, dst_type, dst_id, edge_type, time_slice)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_gedge_src ON ads_graph_edge(src_type, src_id, time_slice);
CREATE INDEX IF NOT EXISTS idx_gedge_dst ON ads_graph_edge(dst_type, dst_id, time_slice);
CREATE INDEX IF NOT EXISTS idx_gedge_typ ON ads_graph_edge(edge_type, time_slice);

-- 外部对照样本（Kaggle MSME：1707 家中国中小微企业，87 特征）
CREATE TABLE IF NOT EXISTS ext_msme_sample (
    sample_id   INTEGER PRIMARY KEY,
    label       INTEGER NOT NULL CHECK (label IN (0,1)),  -- 0 非违约 / 1 违约
    label_task  TEXT NOT NULL DEFAULT 'msme_default',
    f01 REAL, f02 REAL, f03 REAL, f04 REAL, f05 REAL, f06 REAL, f07 REAL, f08 REAL,
    f09 REAL, f10 REAL, f11 REAL, f12 REAL, f13 REAL, f14 REAL, f15 REAL, f16 REAL,
    f17 REAL, f18 REAL, f19 REAL, f20 REAL, f21 REAL, f22 REAL, f23 REAL, f24 REAL,
    f25 REAL, f26 REAL, f27 REAL, f28 REAL, f29 REAL, f30 REAL, f31 REAL, f32 REAL,
    f33 REAL, f34 REAL, f35 REAL, f36 REAL, f37 REAL, f38 REAL, f39 REAL, f40 REAL,
    f41 REAL, f42 REAL, f43 REAL, f44 REAL, f45 REAL, f46 REAL, f47 REAL, f48 REAL,
    f49 REAL, f50 REAL, f51 REAL, f52 REAL, f53 REAL, f54 REAL, f55 REAL, f56 REAL,
    f57 REAL, f58 REAL, f59 REAL, f60 REAL, f61 REAL, f62 REAL, f63 REAL, f64 REAL,
    f65 REAL, f66 REAL, f67 REAL, f68 REAL, f69 REAL, f70 REAL, f71 REAL, f72 REAL,
    f73 REAL, f74 REAL, f75 REAL, f76 REAL, f77 REAL, f78 REAL, f79 REAL, f80 REAL,
    f81 REAL, f82 REAL, f83 REAL, f84 REAL, f85 REAL, f86 REAL, f87 REAL,
    name_map    TEXT,                        -- JSON：f01..f87 → 原始字段名（下载后回填）
    source_id   TEXT REFERENCES meta_data_source(source_id)
) STRICT;


-- ============================================================================
-- 七、视图：常用派生口径固化（避免各处 SQL 口径漂移）
-- ============================================================================

-- 企业画像速览
CREATE VIEW IF NOT EXISTS v_enterprise_profile AS
SELECT e.enterprise_id, e.name_display, e.region_name, e.enterprise_type,
       e.first_qual_year, e.last_qual_year, e.max_qual_rank,
       (SELECT COUNT(*) FROM fact_patent p WHERE p.enterprise_id = e.enterprise_id) AS n_patent,
       (SELECT COUNT(*) FROM fact_patent p WHERE p.enterprise_id = e.enterprise_id
          AND p.patent_type = 'invention') AS n_invention
FROM dim_enterprise e;

-- 企业年度专利产出（技术要素特征的原始来源）
CREATE VIEW IF NOT EXISTS v_patent_yearly AS
SELECT enterprise_id, app_year AS stat_year,
       COUNT(*) AS n_app,
       SUM(is_granted) AS n_grant,
       SUM(CASE WHEN patent_type = 'invention' THEN 1 ELSE 0 END) AS n_inv,
       SUM(CASE WHEN patent_type = 'utility'   THEN 1 ELSE 0 END) AS n_uty,
       SUM(CASE WHEN patent_type = 'design'    THEN 1 ELSE 0 END) AS n_des,
       COUNT(DISTINCT ipc_main) AS n_ipc
FROM fact_patent
WHERE enterprise_id IS NOT NULL AND app_year IS NOT NULL
GROUP BY enterprise_id, app_year;

-- 企业资质履历（含晋级/失效事件）
CREATE VIEW IF NOT EXISTS v_qual_history AS
SELECT q.enterprise_id, e.name_display, q.year, q.qual_type, t.qual_name,
       t.level_rank, q.status, q.is_renewal, q.valid_until
FROM fact_qualification q
JOIN dim_enterprise e ON e.enterprise_id = q.enterprise_id
JOIN dim_qual_type  t ON t.qual_type    = q.qual_type
ORDER BY q.enterprise_id, q.year;

-- 建模宽表 + 标签联合视图（训练脚本直接 SELECT）
CREATE VIEW IF NOT EXISTS v_model_dataset_flat AS
SELECT d.*, l.label_name, l.label_value
FROM ads_model_dataset d
LEFT JOIN ads_label l
       ON l.enterprise_id = d.enterprise_id
      AND l.label_task    = d.label_task
      AND l.cohort_year   = d.cohort_year;

-- 数据覆盖度体检（每次入库后自查）
CREATE VIEW IF NOT EXISTS v_data_coverage AS
SELECT 'enterprise'   AS obj, COUNT(*) AS n FROM dim_enterprise
UNION ALL SELECT 'qualification', COUNT(*) FROM fact_qualification
UNION ALL SELECT 'qual_event',    COUNT(*) FROM fact_qualification_event
UNION ALL SELECT 'patent',        COUNT(*) FROM fact_patent
UNION ALL SELECT 'patent_matched',COUNT(*) FROM fact_patent WHERE enterprise_id IS NOT NULL
UNION ALL SELECT 'inventor',      COUNT(*) FROM dim_inventor
UNION ALL SELECT 'citation',      COUNT(*) FROM fact_patent_citation
UNION ALL SELECT 'funding',       COUNT(*) FROM fact_funding_event
UNION ALL SELECT 'graph_node',    COUNT(*) FROM ads_graph_node
UNION ALL SELECT 'graph_edge',    COUNT(*) FROM ads_graph_edge
UNION ALL SELECT 'model_row',     COUNT(*) FROM ads_model_dataset
UNION ALL SELECT 'patent_ipc_bridge', COUNT(*) FROM bridge_patent_ipc
UNION ALL SELECT 'ipc_dict',          COUNT(*) FROM dim_ipc;

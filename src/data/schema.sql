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
    -- ===== 企查查维度扩展列（QCC 维度 v0.1；由 load_qcc_dimensions 回填） =====
    is_little_giant         INTEGER,         -- D5 国家级小巨人(是=1)
    is_tech_sme             INTEGER,         -- D6 科技型中小企业(是=1)
    is_high_tech_union      INTEGER,         -- D2 高企认定(三源合并)(是=1)
    is_renamed              INTEGER,         -- 是否更名(是=1)
    qcc_is_micro            INTEGER,         -- QCC_是否小微(是=1)
    qcc_tech_level_ord      INTEGER,         -- QCC_科创等级有序编码(入门1..卓越5)
    qcc_scale_ord           INTEGER,         -- QCC_企业规模有序编码(XS1/S2/M3/L4)
    qcc_credit_level_ord    INTEGER,         -- QCC_信用等级有序编码(数字)
    zjtx_honor_cnt          INTEGER,         -- D7 专精特新类荣誉数
    cert_cnt                INTEGER,         -- 资质证书_条数
    cert_valid_cnt          INTEGER,         -- 有效资质证书数(来自 ods_qcc_cert)
    shareholder_cnt         INTEGER,         -- 股东_人数
    patent_union_cnt        INTEGER,         -- 专利数_并集(来自主表)
    invention_union_cnt     INTEGER,         -- 发明专利数_并集(来自主表)
    annual_social_sec       INTEGER,         -- QCC_年报社保人数(「46人」→46)
    annual_revenue_wan      REAL,            -- QCC_年报营收(万元)
    annual_net_profit_wan   REAL,            -- 净利润(万元；多为 NULL，来自 ods_qcc_annual)
    qcc_reg_capital_wan     REAL,            -- QCC_注册资本(万元)
    qcc_score               REAL,            -- QCC_企查分
    qcc_tech_score          REAL,            -- QCC_科创分
    qcc_tech_level          TEXT,            -- QCC_科创等级(原始文本)
    qcc_enterprise_scale    TEXT,            -- QCC_企业规模(原始文本)
    qcc_credit_level        TEXT,            -- QCC_信用等级(原始文本)
    qcc_industry_section    TEXT,            -- QCC_国标行业门类
    qcc_industry_class      TEXT,            -- QCC_国标行业大类
    qcc_industry_mid        TEXT,            -- QCC_国标行业中类
    qcc_industry_sub        TEXT,            -- QCC_国标行业小类
    qcc_biz_scope           TEXT,            -- QCC_经营范围
    qcc_intro               TEXT,            -- QCC_企业简介
    qcc_match_method        TEXT,            -- QCC_匹配方式
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
    n_claims         INTEGER,                -- 权利要求数（设计性 NULL：源未采集，禁止估算回填）
    n_cite_fwd       INTEGER,                -- 前向被引数（设计性 NULL：依赖引文库）
    n_cite_bwd       INTEGER,                -- 后向引用数（设计性 NULL：依赖引文库）
    family_size      INTEGER,                -- 同族规模（设计性 NULL：源未采集，禁止估算回填）
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
    patent_citation        REAL,            -- 3.3 被引（设计性 NULL：引文库未接入，见 field_gap_registry）
    tech_breadth_ipc_class INTEGER,         -- 3.4 技术广度·IPC 小类覆盖数
    tech_breadth_entropy   REAL,            -- 3.4 技术广度·Shannon 熵
    patent_triple_score    REAL,            -- 3.5 专利三值评分（设计性 NULL：SciBERT/同族/权利要求/被引 四源覆盖 0%）
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
    n_patent_cited         INTEGER,         -- 设计性 NULL：依赖 fact_patent_citation
    n_inventor             INTEGER,
    n_ipc_subclass         INTEGER,
    n_patent_collab        INTEGER,         -- 合作申请专利数（≥2 申请人）
    first_app_year         INTEGER,
    computed_at            TEXT,
    -- 法律状态代理（2026-11-05 新增）：被引次数拿不到，用「专利结局」替代
    n_patent_lapsed        INTEGER,         -- 授权后失效的专利数（未缴年费/期限届满/失效/避重放弃）
    n_patent_rejected      INTEGER,         -- 驳回或撤回的专利数
    patent_lapse_ratio     REAL,            -- 失效数 / 有法律状态的专利数
    patent_reject_ratio    REAL,            -- 驳回数 / 有法律状态的专利数
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
    -- 年报财务快照（2026-11-05 新增）：有的填、没有留空，不拿社保人数折算
    latest_report_year        INTEGER,       -- 最新一期年报年份
    latest_rev_wan            REAL,          -- 营业收入（万元）
    latest_net_profit_wan     REAL,          -- 净利润（万元）
    latest_total_assets_wan   REAL,          -- 资产总计（万元）
    latest_tax_wan            REAL,          -- 纳税总额（万元）
    latest_emp_num            INTEGER,       -- 年报披露员工数
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

-- ---------------------------------------------------------------------------
-- 专利唯一化视图（v1.2，2026-11-05）
--   问题：同一件专利在源数据里落两条 —— 申请公布文本（公告号 …A，带 IPC 分类号、
--         无法律状态）与授权公告文本（…B，带授权状态、无 IPC 分类号）。
--         实测 2,866 组 / 5,732 行，其中 2,776 组是「一条未授权 + 一条已授权」。
--   后果：任何按 fact_patent 行数做的聚合都会重复计数 —— 专利数虚增约 8%，
--         手算授权率被硬压向 0.5（实测 406 家企业平均低估 5.7pp，最大 50pp）。
--   口径：一行 = 一件专利（app_no）× 一个归属企业；未归属专利按 app_no 单独成行。
--         共同申请人（同一 app_no 挂两个 enterprise_id，实测 4 组）各自成行，不合并。
--         字段级合并：布尔取 MAX、文本取非空、日期取最早/最晚；
--         title 冲突（实测 58 组）用 n_distinct_titles 标记，不强行统一。
--   用法：**所有专利级聚合一律走本视图，不要直接 COUNT(*) fact_patent**。
--
--   ⚠️ 授权字段有两个，含义不同，别用错（2026-11-05 补，v1.3）：
--       fact_patent.is_granted  = 1 仅当「此刻仍在权」。它会随年费断缴 / 期限届满
--                                 翻回 0，所以 "is_granted=0" 里混着两类完全不同的专利：
--                                 申请未获授权的，和授权后失效的。
--       is_granted_ever         = 1 表示「曾经获得过授权」，依据公告号 kind code
--                                 （B/U/S/C 均为授权公告，存在即授权）+ is_granted。
--       实测：全部 29,582 件已归属专利里，is_granted=1 只有 15,753 件，
--       而曾授权 23,603 件 —— 用 is_granted 算授权率会少算 7,850 件（33.3%），
--       企业级授权率中位 0.567 被压到真值 0.831。
--       凡「授权率 / 授权数 / 技术转化率」一律用 is_granted_ever；
--       凡「当前有效专利数 / 维持率」才用 is_granted。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_patent_canonical;
CREATE VIEW v_patent_canonical AS
WITH base AS (
    SELECT p.app_no,
           p.enterprise_id,
           p.patent_id,
           p.title, p.abstract, p.app_date, p.app_year, p.pub_date,
           p.patent_type, p.legal_status, p.is_granted, p.ipc_main,
           p.match_method, p.match_confidence,
           ROW_NUMBER() OVER (
               PARTITION BY p.app_no, COALESCE(p.enterprise_id, '~NA')
               ORDER BY p.is_granted DESC,
                        CASE WHEN p.ipc_main IS NOT NULL THEN 0 ELSE 1 END,
                        p.match_confidence DESC,
                        p.patent_id
           ) AS rn
    FROM fact_patent p
    WHERE p.app_no IS NOT NULL
)
SELECT b.app_no,
       b.enterprise_id,
       MAX(CASE WHEN b.rn = 1 THEN b.patent_id END)         AS patent_id,
       COUNT(*)                                             AS n_records,
       GROUP_CONCAT(b.patent_id, ',')                       AS patent_ids,
       MAX(b.title)                                         AS title,
       COUNT(DISTINCT b.title)                              AS n_distinct_titles,
       MAX(b.abstract)                                      AS abstract,
       MIN(b.app_date)                                      AS app_date,
       MAX(b.app_year)                                      AS app_year,
       MIN(b.pub_date)                                      AS pub_date_first,
       MAX(b.pub_date)                                      AS pub_date_last,
       MAX(b.patent_type)                                   AS patent_type,
       MAX(b.legal_status)                                  AS legal_status,
       MAX(b.is_granted)                                    AS is_granted,
       -- 「曾授权」：任一记录是授权公告（B/U/S/C）或 is_granted=1 即成立
       MAX(CASE WHEN b.is_granted = 1
                 OR substr(b.patent_id, -1, 1) IN ('B','U','S','C')
                THEN 1 ELSE 0 END)                          AS is_granted_ever,
       -- 授权凭据：优先给出授权公告的 kind code，无则 NULL
       MAX(CASE WHEN substr(b.patent_id, -1, 1) IN ('B','U','S','C')
                THEN substr(b.patent_id, -1, 1) END)        AS grant_kind,
       MAX(b.ipc_main)                                      AS ipc_main,
       MAX(CASE WHEN b.rn = 1 THEN b.match_method END)      AS match_method,
       MAX(CASE WHEN b.rn = 1 THEN b.match_confidence END)  AS match_confidence
FROM base b
GROUP BY b.app_no, COALESCE(b.enterprise_id, '~NA');

-- 企业画像速览
CREATE VIEW IF NOT EXISTS v_enterprise_profile AS
SELECT e.enterprise_id, e.name_display, e.region_name, e.enterprise_type,
       e.first_qual_year, e.last_qual_year, e.max_qual_rank,
       (SELECT COUNT(*) FROM v_patent_canonical p WHERE p.enterprise_id = e.enterprise_id) AS n_patent,
       (SELECT COUNT(*) FROM v_patent_canonical p WHERE p.enterprise_id = e.enterprise_id
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
FROM v_patent_canonical
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

-- ===========================================================================
-- [ASOF:BEGIN]
-- as-of 切面特征（消除后视泄漏）
--   DDL 单一事实源：build_asof_features.py 只截取本区块执行，勿把建表语句复制到脚本。
--   语义：ads_label.cohort_year = 特征窗口终点（见《数据字典-标准数据库》§4.6），
--         as_of_year 之后发生的事实（专利 / 资质 / 发明人）一律不得进入特征。
--   'cohort'      = 主口径，as_of_year = cohort_year（认定之前，字典规定）
--   'due_minus_1' = 稳健性对照，as_of_year = cohort_year + VALID_YEARS - 1（到期前一年）
--   原 ads_model_dataset / v_model_dataset_flat 保持不动，作为泄漏基线留存。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS ads_feature_asof (
    enterprise_id   TEXT NOT NULL,
    as_of_policy    TEXT NOT NULL CHECK (as_of_policy IN ('cohort','due_minus_1')),
    cohort_year     INTEGER NOT NULL,
    as_of_year      INTEGER NOT NULL,       -- 特征观测截止年（含）
    feature_name    TEXT NOT NULL,
    feature_value   REAL,                   -- 无原料 / 不可切分时为 NULL
    is_available    INTEGER NOT NULL CHECK (is_available IN (0,1)),
    computed_at     TEXT,
    PRIMARY KEY (enterprise_id, as_of_policy, feature_name)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_asof_policy   ON ads_feature_asof(as_of_policy, as_of_year);
CREATE INDEX IF NOT EXISTS idx_asof_feature  ON ads_feature_asof(feature_name, is_available);

-- as-of 特征覆盖率体检（不改动既有 v_data_coverage）
DROP VIEW IF EXISTS v_asof_feature_coverage;
CREATE VIEW v_asof_feature_coverage AS
SELECT as_of_policy, feature_name,
       COUNT(*)                                   AS n_sample,
       SUM(is_available)                          AS n_available,
       ROUND(1.0 * SUM(is_available) / COUNT(*), 4) AS coverage
FROM ads_feature_asof
GROUP BY as_of_policy, feature_name;

-- 无泄漏训练入口：列名与 v_model_dataset_flat 对齐，下游只需换视图名。
-- ⚠️ 训练时必须加 WHERE as_of_policy='cohort' 取主口径；否则同一企业会出现两行。
DROP VIEW IF EXISTS v_model_dataset_asof_flat;
CREATE VIEW v_model_dataset_asof_flat AS
SELECT d.enterprise_id, d.dataset_ver, d.label_task, d.cohort_year,
       d.name_display, d.region_code, d.label,
       MAX(CASE WHEN f.feature_name='inv_patent_ratio'       THEN f.feature_value END) AS inv_patent_ratio,
       MAX(CASE WHEN f.feature_name='grant_rate'             THEN f.feature_value END) AS grant_rate,
       MAX(CASE WHEN f.feature_name='patent_citation'        THEN f.feature_value END) AS patent_citation,
       MAX(CASE WHEN f.feature_name='tech_breadth_ipc_class' THEN f.feature_value END) AS tech_breadth_ipc_class,
       MAX(CASE WHEN f.feature_name='tech_breadth_entropy'   THEN f.feature_value END) AS tech_breadth_entropy,
       MAX(CASE WHEN f.feature_name='patent_triple_score'    THEN f.feature_value END) AS patent_triple_score,
       MAX(CASE WHEN f.feature_name='core_staff_stability'   THEN f.feature_value END) AS core_staff_stability,
       MAX(CASE WHEN f.feature_name='rd_intensity'           THEN f.feature_value END) AS rd_intensity,
       MAX(CASE WHEN f.feature_name='n_patent_app'           THEN f.feature_value END) AS n_patent_app,
       MAX(CASE WHEN f.feature_name='n_patent_grant'         THEN f.feature_value END) AS n_patent_grant,
       MAX(CASE WHEN f.feature_name='n_patent_invention'     THEN f.feature_value END) AS n_patent_invention,
       MAX(CASE WHEN f.feature_name='n_inventor'             THEN f.feature_value END) AS n_inventor,
       MAX(CASE WHEN f.feature_name='n_ipc_subclass'         THEN f.feature_value END) AS n_ipc_subclass,
       MAX(CASE WHEN f.feature_name='n_patent_collab'        THEN f.feature_value END) AS n_patent_collab,
       MAX(CASE WHEN f.feature_name='rev_cagr'               THEN f.feature_value END) AS rev_cagr,
       MAX(CASE WHEN f.feature_name='emp_cagr'               THEN f.feature_value END) AS emp_cagr,
       MAX(CASE WHEN f.feature_name='patent_cagr'            THEN f.feature_value END) AS patent_cagr,
       MAX(CASE WHEN f.feature_name='funding_to_last_val'    THEN f.feature_value END) AS funding_to_last_val,
       MAX(CASE WHEN f.feature_name='m_funding'              THEN f.feature_value END) AS m_funding,
       MAX(CASE WHEN f.feature_name='m_team'                 THEN f.feature_value END) AS m_team,
       MAX(CASE WHEN f.feature_name='m_cert'                 THEN f.feature_value END) AS m_cert,
       MAX(CASE WHEN f.feature_name='m_cite'                 THEN f.feature_value END) AS m_cite,
       MAX(CASE WHEN f.feature_name='investor_prestige'      THEN f.feature_value END) AS investor_prestige,
       MAX(CASE WHEN f.feature_name='qual_rank'              THEN f.feature_value END) AS qual_rank,
       MAX(CASE WHEN f.feature_name='qual_rank_delta'        THEN f.feature_value END) AS qual_rank_delta,
       MAX(CASE WHEN f.feature_name='qual_upgrade_cum'       THEN f.feature_value END) AS qual_upgrade_cum,
       MAX(CASE WHEN f.feature_name='tech_conversion'        THEN f.feature_value END) AS tech_conversion,
       MAX(CASE WHEN f.feature_name='cap_tech_synergy'       THEN f.feature_value END) AS cap_tech_synergy,
       MAX(CASE WHEN f.feature_name='tech_growth_inter'      THEN f.feature_value END) AS tech_growth_inter,
       d.split, d.split_protocol, d.built_at,
       l.label_name, l.label_value,
       f.as_of_policy, MAX(f.as_of_year) AS as_of_year
FROM ads_model_dataset d
JOIN ads_label l
  ON l.enterprise_id = d.enterprise_id
 AND l.label_task    = d.label_task
 AND l.cohort_year   = d.cohort_year
JOIN ads_feature_asof f
  ON f.enterprise_id = d.enterprise_id
 AND f.cohort_year   = d.cohort_year
WHERE d.split <> 'excluded'
GROUP BY d.enterprise_id, d.dataset_ver, d.label_task, d.cohort_year, f.as_of_policy;
-- [ASOF:END]

-- ============================================================================
-- 【算法端唯一训练入口】v_model_train_ready
-- ============================================================================
-- 这层视图存在的唯一目的：把三个已知的结构性问题在**物理上**挡掉，
-- 让下游拿到就能 fit，不需要再读任何口径文档。
--
-- 1) 假负例隔离（P1-4）
--    ads_model_dataset 有 193 行 split='excluded'，它们是 cohort 2023+、
--    观察窗尚未闭合的企业（到期年 2026–2028），label 填 0 只是占位。
--    直接 `SELECT * FROM ads_model_dataset` 训练，正例率会被从 6.8% 稀释到
--    4.2%，AUC 看起来更"稳"，但那是假的。本视图只放 train/val/test。
--
-- 2) 伪重复（一家两行）
--    v_model_dataset_asof_flat 是 614 行 = 307 家 × 2 种 as_of_policy
--    （cohort / due_minus_1）。按行训练会把同一家企业算两次权重，正例也从
--    21 虚增成 30。本视图固定 as_of_policy='cohort'，一行 = 一家企业。
--
-- 3) 常量列（浪费自由度且误导重要性排序）
--    cohort 切面下这 4 列去重值恒为 1，单变量 AUC 全部 = 0.5000：
--       m_cert=1.0   qual_rank=2.0
--       qual_rank_delta=0.0   qual_upgrade_cum=0.0
--    成因：队列由「首次高企认定年」定义，认定当年资质等级必然一致，
--    故 m_cert / qual_rank 恒定；当年即首次认定，故 delta / cum 恒为 0。
--    已从本视图剔除。要看它们请回 v_model_dataset_asof_flat。
--
-- 仍保留的 11 列均为 cohort 内非常量（去重值 20–237 个）。
-- 缺口字段（引文库 / CAGR / 资金 / 三重评分等 15 个）不在本视图内，
-- 原因见 configs/field_gap_registry.json。
--
-- ⚠️ grant_rate 跨队列不可混：2021 队列 160 家**全部为 1.0**（发明专利需 3 年
--    审结期，2021 时点窗口内无成熟发明），2022 队列才有 0/1 变化。混队列训练
--    会让它退化成「队列哑变量」。单变量 AUC 0.5185（|Δ|=0.0185，无判别力），
--    建树下树时建议优先剔除。
--
-- 用法：
--   SELECT * FROM v_model_train_ready;        -- 307 行 × 17 列，直接 fit
--   split 已按企业级固定（train 214 / val 54 / test 39），
--   禁止再按行随机切分，否则同一企业会同时进训练与验证。
-- [TRAINREADY:BEGIN]
DROP VIEW IF EXISTS v_model_train_ready;
CREATE VIEW v_model_train_ready AS
SELECT enterprise_id, name_display, region_code, cohort_year, split, label,
       n_patent_app, n_patent_invention, n_patent_grant,
       inv_patent_ratio, tech_conversion,
       n_ipc_subclass, tech_breadth_entropy, tech_breadth_ipc_class,
       grant_rate, core_staff_stability, n_inventor
FROM v_model_dataset_asof_flat
WHERE as_of_policy = 'cohort'
  AND split IN ('train', 'val', 'test');
-- [TRAINREADY:END]

-- ============================================================================
-- 企查查维度 ODS 表（QCC 维度 v0.1）
-- 单一事实源：本区块由 load_qcc_dimensions 通过正则截取执行；主库存量库上
-- 通过 CREATE TABLE IF NOT EXISTS 保证幂等。外键指向 dim_enterprise(enterprise_id)，
-- enterprise_id 为 NULL 表示未匹配进 500 户清单（保留 company_name 兜底）。
-- ============================================================================
-- [QCC:BEGIN]
CREATE TABLE IF NOT EXISTS ods_qcc_honor (
    row_id          INTEGER PRIMARY KEY,
    enterprise_id   TEXT REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,
    company_name    TEXT NOT NULL,        -- 兜底名（企查查原文）
    honor_name      TEXT,                 -- 荣誉名称
    honor_type      TEXT,                 -- 荣誉类型
    level           TEXT,                 -- 级别
    publisher       TEXT,                 -- 发布单位
    cert_year       INTEGER,              -- 认证年份
    publish_date    TEXT,                 -- 发布日期
    valid_from      TEXT,                 -- 有效期自
    valid_to        TEXT,                 -- 有效期至
    source          TEXT                  -- 来源
) STRICT;
CREATE INDEX IF NOT EXISTS idx_qcc_honor_eid ON ods_qcc_honor(enterprise_id);

CREATE TABLE IF NOT EXISTS ods_qcc_cert (
    row_id          INTEGER PRIMARY KEY,
    enterprise_id   TEXT REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,
    company_name    TEXT NOT NULL,        -- 兜底名
    cert_name       TEXT,                 -- 资质名称
    cert_category   TEXT,                 -- 资质类别及等级/产品名称/认证项目
    cert_no         TEXT,                 -- 证书编号
    issuer          TEXT,                 -- 发证机构
    issue_date      TEXT,                 -- 发证日期
    valid_to        TEXT,                 -- 有效期至
    cert_status     TEXT                  -- 证书状态(有效/过期/…)
) STRICT;
CREATE INDEX IF NOT EXISTS idx_qcc_cert_eid ON ods_qcc_cert(enterprise_id);
CREATE INDEX IF NOT EXISTS idx_qcc_cert_status ON ods_qcc_cert(enterprise_id, cert_status);

CREATE TABLE IF NOT EXISTS ods_qcc_shareholder (
    row_id              INTEGER PRIMARY KEY,
    enterprise_id       TEXT REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,
    company_name        TEXT NOT NULL,    -- 兜底名
    shareholder_name    TEXT,             -- 股东名称
    shareholder_type    TEXT,             -- 股东类型
    share_kind          TEXT,             -- 股份类型
    shares              INTEGER,          -- 持股数(股)
    subscription_wan    REAL,             -- 认缴出资额(万元)
    subscription_date   TEXT,             -- 认缴出资日期
    indirect_ratio      REAL,             -- 间接持股比例
    first_hold_date     TEXT,             -- 首次持股日期
    related_product     TEXT              -- 关联产品/机构
) STRICT;
CREATE INDEX IF NOT EXISTS idx_qcc_share_eid ON ods_qcc_shareholder(enterprise_id);

CREATE TABLE IF NOT EXISTS ods_qcc_annual (
    row_id              INTEGER PRIMARY KEY,
    enterprise_id       TEXT REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,
    company_name        TEXT NOT NULL,    -- 兜底名
    report_year         INTEGER,          -- 年报年份
    publish_date        TEXT,             -- 公布日期
    total_revenue_wan   REAL,             -- 营业总收入(万元；不公示→NULL)
    employee_num        INTEGER,          -- 从业人数
    social_sec_num      INTEGER,          -- 社保人数
    total_assets_wan    REAL,             -- 资产总额(万元)
    tax_total_wan       REAL,             -- 纳税总额(万元)
    liability_wan       REAL,             -- 负债总额(万元)
    net_profit_wan      REAL,             -- 净利润(万元)
    profit_total_wan    REAL,             -- 利润总额(万元)
    owner_equity_wan    REAL,             -- 所有者权益合计(万元)
    main_biz_rev_wan    REAL,             -- 营业总收入中主营业务收入(万元)
    biz_status          TEXT              -- 企业经营状态
) STRICT;
CREATE INDEX IF NOT EXISTS idx_qcc_annual_eid ON ods_qcc_annual(enterprise_id);
-- [QCC:END]

-- ===========================================================================
-- 生存分析口径（方案C）：高企资格存续 person-period 表
-- 每企业最多 3 期（高企资格 3 年有效，t=3 为续期屏障）。
-- 特征锚点 = fy + t - 1（每期期初），严格无后视泄漏。
-- event: 1=该期发生「到期未续」（仅 t=3 且完整观察窗）；0=存活至期末；
--        NULL=前瞻行（2023+ 队列，观察窗未满，特征可用、结局未知）。
-- prospective 标记 2023 队列 t=3 行（2026 年到期），供风险排序输出。
-- ===========================================================================
-- [SURV:BEGIN]
CREATE TABLE IF NOT EXISTS ads_survival_period (
    enterprise_id TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,
    first_year    INTEGER NOT NULL,          -- 首次高企认定年 fy
    period_t      INTEGER NOT NULL CHECK (period_t IN (1,2,3)),  -- 期次 1..3
    anchor_year   INTEGER NOT NULL,          -- 特征锚点年 = fy + t - 1
    event         INTEGER CHECK (event IN (0,1) OR event IS NULL),
    prospective   INTEGER NOT NULL DEFAULT 0, -- 1=前瞻行（结局未知，只算特征）
    split         TEXT,                      -- 沿用 md5 企业级 70/15/15 切分
    -- as-of 14 特征（与 v_model_dataset_asof_flat 同名同义）
    core_staff_stability REAL, inv_patent_ratio REAL, m_cert REAL, n_inventor REAL,
    n_ipc_subclass REAL, n_patent_app REAL, n_patent_grant REAL,
    n_patent_invention REAL, qual_rank REAL, qual_rank_delta REAL,
    qual_upgrade_cum REAL, tech_breadth_entropy REAL,
    tech_breadth_ipc_class REAL, tech_conversion REAL,
    built_at      TEXT,
    PRIMARY KEY (enterprise_id, period_t)
) STRICT;
CREATE INDEX IF NOT EXISTS idx_surv_anchor ON ads_survival_period(anchor_year, period_t);
CREATE INDEX IF NOT EXISTS idx_surv_prosp ON ads_survival_period(prospective, first_year);
-- [SURV:END]

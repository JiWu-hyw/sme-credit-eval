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

-- 融资事件表（506 条全部来自 data/raw/合并_融资事件流水.csv，行级源标识 zj_500family；
--   非上市企业融资事件同样在表内，AKShare 只是 meta_data_source 里登记的潜在补充渠道，不是本表来源；
--   金额/估值缺失如实留 NULL，禁止用占位值回填）
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
-- v1.4：节点类型扩到 5 类 —— 补 investor（投资机构），人员 inventor 已于 v1.3 起定义
CREATE TABLE IF NOT EXISTS ads_graph_node (
    node_type    TEXT NOT NULL CHECK (node_type IN
                   ('enterprise','patent','ipc','inventor','investor')),
    node_id      TEXT NOT NULL,             -- 各类型 ID 口径不同。enterprise = ENT+12 位 hex；patent = fact_patent.patent_id（CN 开头公开号，另有 4 件 JP/TW 同族，非脏值）；ipc = IPC 分类号（已剥 WIPO 版本号后缀如 (2006.01)；5 个节点仅到小类 A61B/A61K/A61P/C07D/C07K，源串截断）；inventor = INV+10 位 hex；investor = IVS+10 位 hex（源数据无机构主键，由规范化机构名 md5 派生）
    time_slice   INTEGER NOT NULL,           -- 年度切片（全期累计=0；按 app_year<=T 自建时不入本表）
    display_name TEXT,
    PRIMARY KEY (node_type, node_id, time_slice)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_gnode_slice ON ads_graph_node(node_type, time_slice);

-- 图边表
-- v1.4：边类型枚举扩到 8 个，实际产出 5 类 —— files_patent / belongs_ipc /
--       invented_by（专利→发明人）/ funded_by（企业→投资机构）/ tech_similar。
--       未产出：co_inventor（语义由 invented_by 二跳覆盖）、cites / qualifies（无源数据）。
--       保留枚举位是为了将来补数据时不必改 DDL，不代表表里有这三类边。
-- v1.5：weight 语义随 edge_type 变化 —— files_patent=匹配置信度、
--       belongs_ipc/invented_by=1.0 主 0.5 副、funded_by=1.0、tech_similar=TF-IDF 余弦。
CREATE TABLE IF NOT EXISTS ads_graph_edge (
    src_type   TEXT NOT NULL CHECK (src_type IN
                 ('enterprise','patent','ipc','inventor','investor')),
    src_id     TEXT NOT NULL,
    dst_type   TEXT NOT NULL CHECK (dst_type IN
                 ('enterprise','patent','ipc','inventor','investor')),
    dst_id     TEXT NOT NULL,
    edge_type  TEXT NOT NULL CHECK (edge_type IN
                 ('files_patent','belongs_ipc','invented_by','funded_by',
                  'co_inventor','cites','qualifies','tech_similar')),
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

-- ============================================================================
-- v1.4 诊断视图（单一事实源：src/data/migrations/v1_4_views.sql）
-- ============================================================================
-- [V14VIEWS:BEGIN]
-- ============================================================================
-- v1.4 口径诊断视图（2026-11-06）
-- 触发：数据交付包评审报告 XM-2026-RPT-001
--   · §2.3(1) 专利数三个口径并存（32,969 / 30,346 / 30,103）→ v_patent_master
--   · §3.2 标签与 n_events 完全分离（1 次认定 = 正例）→ v_label_mechanism
--   · §3.4 val 仅 2 个正例，早停/阈值不可靠      → v_model_train_ready_cv
--   · §4.1 特征缺失只报数量不报原因              → v_feature_missing_reason
--   · §5.1 图覆盖靠人工统计                      → v_graph_coverage
--   · §5.2(1) 图只有 1 个时间切片，算法端不知各 T 的规模 → v_graph_patent_timeline
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1) 专利权威口径：一行 = 一个申请号（30,103 行）
--    对外报数一律用本视图的 COUNT(*)，不要用 fact_patent（32,969，
--    同一申请号在「申请公布 A」与「授权公告 B」各落一行）。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_patent_master;
CREATE VIEW v_patent_master AS
SELECT c.app_no,
       MIN(c.patent_id)                                   AS patent_id_repr,
       MAX(CASE WHEN c.enterprise_id IS NOT NULL
                THEN c.enterprise_id END)                 AS enterprise_id,
       COUNT(DISTINCT c.enterprise_id)                    AS n_enterprise,
       CASE WHEN COUNT(DISTINCT c.enterprise_id) > 1 THEN 'multi_assignee'
            WHEN MAX(c.enterprise_id) IS NULL          THEN 'unmatched'
            ELSE 'matched' END                            AS join_status,
       MAX(c.title)                                       AS title,
       MAX(c.abstract)                                    AS abstract,
       CASE WHEN MAX(c.abstract) IS NOT NULL
             AND TRIM(MAX(c.abstract)) <> '' THEN 1 ELSE 0 END AS has_abstract,
       MIN(c.app_date)                                    AS app_date,
       MAX(c.app_year)                                    AS app_year,
       MAX(c.is_granted_ever)                             AS is_granted_ever,
       MAX(c.grant_kind)                                  AS grant_kind,
       MAX(c.patent_type)                                 AS patent_type,
       MAX(c.ipc_main)                                    AS ipc_main,
       MAX(c.n_records)                                   AS n_records
FROM v_patent_canonical c
WHERE c.app_no IS NOT NULL
GROUP BY c.app_no;

-- ---------------------------------------------------------------------------
-- 2) 标签机制台账：n_events × cohort 的标签分布
--    事实：n_events=1 的 21 家 100% 为正例，n_events>=2 的 286 家 0% 正例。
--    标签 ≈「是否只被认定过一次」。任何与「持续申报」相关的特征都会天然有
--    判别力，论文讨论必须正面处理（评审 §3.2）。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_label_mechanism;
CREATE VIEW v_label_mechanism AS
SELECT l.n_events,
       l.cohort_year,
       COUNT(*)                                        AS n_enterprise,
       SUM(l.label_value)                              AS n_positive,
       COUNT(*) - SUM(l.label_value)                    AS n_negative,
       ROUND(1.0 * SUM(l.label_value) / COUNT(*), 4)    AS positive_rate
FROM ads_label l
GROUP BY l.n_events, l.cohort_year;

-- ---------------------------------------------------------------------------
-- 3) 5 折分组分层交叉验证口径（新增，不替换 v_model_train_ready.split）
--    原 split 正例 15/2/4，验证集只有 2 个正例，早停与阈值选择不成立。
--    本视图按 label 分层、按 enterprise_id 排序轮转分配 fold_id，
--    每折正例 4~5 个，可直接用于 RepeatedStratifiedGroupKFold 的组标签。
--    ⚠️ fold_id 只保证「分层 + 企业不跨折」，不保证各折特征分布一致；
--       论文里仍须同时报分队列结果（评审 §4.2）。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_model_train_ready_cv;
CREATE VIEW v_model_train_ready_cv AS
WITH ranked AS (
    SELECT t.*,
           ROW_NUMBER() OVER (PARTITION BY t.label
                              ORDER BY t.enterprise_id) - 1 AS rn
    FROM v_model_train_ready t
)
SELECT enterprise_id, name_display, region_code, cohort_year, split, label,
       (rn % 5) + 1                                     AS fold_id,
       n_patent_app, n_patent_invention, n_patent_grant,
       inv_patent_ratio, tech_conversion,
       n_ipc_subclass, tech_breadth_entropy, tech_breadth_ipc_class,
       grant_rate, core_staff_stability, n_inventor
FROM ranked;

-- ---------------------------------------------------------------------------
-- 4) 特征缺失原因（只列 is_available=0 的行）
--    source_not_integrated       源数据未接入或经决策保持 NULL（登记册已记）
--    no_patent_in_window          观察窗（2019..as_of）内无专利
--    insufficient_sample         其他原料不足（如授权未过审结期）
--    ⚠️ 观察窗起点固定为 2019（build_asof_features.WINDOW_START），
--       因此 2021 队列只有 2019–2021 三年、2022 队列有 2019–2022 四年。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_feature_missing_reason;
CREATE VIEW v_feature_missing_reason AS
SELECT f.feature_name,
       f.enterprise_id,
       f.as_of_policy,
       f.as_of_year,
       CASE
         WHEN f.feature_name IN (
                'rev_cagr','emp_cagr','patent_cagr','rd_intensity',
                'investor_prestige','funding_to_last_val','m_funding','m_team',
                'm_cite','cap_tech_synergy','patent_citation','patent_triple_score',
                'tech_growth_inter','n_patent_collab')
           THEN 'source_not_integrated'
         WHEN NOT EXISTS (
                SELECT 1 FROM v_patent_canonical p
                WHERE p.enterprise_id = f.enterprise_id
                  AND p.app_year BETWEEN 2019 AND f.as_of_year)
           THEN 'no_patent_in_window'
         ELSE 'insufficient_sample'
       END                                              AS missing_reason,
       (SELECT COUNT(*) FROM v_patent_canonical p
         WHERE p.enterprise_id = f.enterprise_id
           AND p.app_year <= f.as_of_year)              AS n_patent_by_appyear,
       (SELECT COUNT(*) FROM v_patent_canonical p
         WHERE p.enterprise_id = f.enterprise_id
           AND p.app_year BETWEEN 2019 AND f.as_of_year) AS n_patent_in_window
FROM ads_feature_asof f
WHERE f.is_available = 0;

-- ---------------------------------------------------------------------------
-- 5) 图谱覆盖：每家企业各类边数（替代人工 COUNT，评审 §5.1 的复算入口）
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_graph_coverage;
CREATE VIEW v_graph_coverage AS
SELECT e.enterprise_id,
       e.name_display,
       (SELECT COUNT(*) FROM ads_graph_edge g
         WHERE g.edge_type = 'files_patent'
           AND g.src_id = e.enterprise_id)              AS n_patent_edge,
       (SELECT COUNT(DISTINCT b.ipc_subclass)
          FROM fact_patent p
          JOIN bridge_patent_ipc b ON b.patent_id = p.patent_id
         WHERE p.enterprise_id = e.enterprise_id)        AS n_ipc_subclass,
       (SELECT COUNT(DISTINCT bi.inventor_id)
          FROM fact_patent p
          JOIN bridge_patent_inventor bi ON bi.patent_id = p.patent_id
         WHERE p.enterprise_id = e.enterprise_id)        AS n_inventor,
       (SELECT COUNT(*) FROM ads_graph_edge g
         WHERE g.edge_type = 'tech_similar'
           AND (g.src_id = e.enterprise_id OR g.dst_id = e.enterprise_id))
                                                        AS n_tech_similar,
       (SELECT COUNT(*) FROM ads_graph_edge g
         WHERE g.edge_type = 'funded_by'
           AND g.src_id = e.enterprise_id)              AS n_investor
FROM dim_enterprise e;

-- ---------------------------------------------------------------------------
-- 6) 年度切片规模台账：给算法端按 T 自建时序图用
--    用法：SELECT * FROM v_graph_patent_timeline WHERE time_slice <= 2022;
--    切片口径：app_year <= T（专利申请年），edge 取 files_patent。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_graph_patent_timeline;
CREATE VIEW v_graph_patent_timeline AS
WITH y AS (
    SELECT m.app_year                                     AS time_slice,
           COUNT(*)                                       AS n_patent,
           SUM(CASE WHEN m.enterprise_id IS NOT NULL
                    THEN 1 ELSE 0 END)                    AS n_patent_matched,
           COUNT(DISTINCT CASE WHEN m.enterprise_id IS NOT NULL
                               THEN m.enterprise_id END)  AS n_enterprise
    FROM v_patent_master m
    WHERE m.app_year IS NOT NULL
    GROUP BY m.app_year
)
SELECT time_slice,
       n_patent,
       n_patent_matched,
       n_enterprise,
       SUM(n_patent)         OVER (ORDER BY time_slice)  AS n_patent_cum,
       SUM(n_patent_matched) OVER (ORDER BY time_slice)  AS n_patent_matched_cum
FROM y;

-- ---------------------------------------------------------------------------
-- 7) 等长观察窗复算表（新增，评审 §4.2「队列效应」的解法）
--    现象：2022 队列平均专利数 23.4 件 > 2021 队列 15.8 件，被误读为
--          「2022 队列企业技术能力更强」。
--    真因：观察窗起点固定 2019，2021 队列只有 2019–2021（3 年），
--          2022 队列有 2019–2022（4 年）——队列间窗口长度不同。
--    本视图把两个队列统一压到「as_of 前 3 年」（cohort_year-2 .. cohort_year），
--    差距从 7.6 件收窄到 4.2 件（−45%）。剩下的一半才是真实差异+噪声。
--    ⚠️ 这是诊断口径，不替换 v_model_train_ready；论文里两套结果都要报。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_train_ready_window_fixed;
CREATE VIEW v_train_ready_window_fixed AS
WITH yrs AS (
    SELECT m.enterprise_id, m.app_year, m.patent_type, COUNT(*) AS n_patent
    FROM v_patent_master m
    WHERE m.enterprise_id IS NOT NULL AND m.app_year IS NOT NULL
    GROUP BY m.enterprise_id, m.app_year, m.patent_type
)
SELECT t.enterprise_id,
       t.cohort_year,
       t.label,
       COALESCE(SUM(CASE WHEN y.app_year BETWEEN t.cohort_year - 2 AND t.cohort_year
                         THEN y.n_patent END), 0)          AS n_patent_3y,
       COALESCE(SUM(CASE WHEN y.app_year BETWEEN t.cohort_year - 2 AND t.cohort_year
                          AND y.patent_type = 'invention'
                         THEN y.n_patent END), 0)          AS n_invention_3y,
       COALESCE(SUM(y.n_patent), 0)                        AS n_patent_window,
       ROUND(1.0 * COALESCE(SUM(CASE WHEN y.app_year BETWEEN t.cohort_year - 2
                                                          AND t.cohort_year
                                                          AND y.patent_type = 'invention'
                                     THEN y.n_patent END), 0)
             / NULLIF(COALESCE(SUM(CASE WHEN y.app_year BETWEEN t.cohort_year - 2
                                                             AND t.cohort_year
                                        THEN y.n_patent END), 0), 0), 4)
                                                           AS inv_patent_ratio_3y
FROM v_model_train_ready t
LEFT JOIN yrs y
       ON y.enterprise_id = t.enterprise_id
      AND y.app_year BETWEEN 2019 AND t.cohort_year
GROUP BY t.enterprise_id, t.cohort_year, t.label;

-- [V14VIEWS:END]

-- ============================================================================
-- v1.5 诊断视图（单一事实源：src/data/migrations/v1_5_views.sql）
-- ============================================================================
-- [V15VIEWS:BEGIN]
-- ============================================================================
-- v1.5 诊断视图
-- 依据：XM-2026-RPT-001《数据交付包评审报告 · 算法端可用性》
--   §3.3「建议在报告中强制带 cohort_year 分层或加权，并给出分队列结果」
--   §4.2「建议论文中所有特征效应都给分队列版本，并明确 8 个正例支撑不了稳定估计」
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1) 特征判别力分队列复算（v_feature_discrimination）
--    一行 = 一个特征 × 一个样本范围（all / 2021 / 2022）。
--
--    为什么要把这件事做成视图，而不是让算法端自己算：
--    报告 §4.2 实测 inv_patent_ratio 全样本 AUC 0.618，但拆开是 2021 队列
--    0.784、2022 队列 0.503 —— 全样本值混入了队列效应，单看会误判
--    「技术质量决定存续」。这类陷阱必须在数据侧固化成可查的口径，
--    否则每篇论文、每份汇报都要重新踩一遍。
--
--    AUC 用 Mann-Whitney U 等价式算（平均秩），并列值给 0.5 权重：
--      AUC = (Σ rank_pos − n_pos(n_pos+1)/2) / (n_pos × n_neg)
--    与 sklearn roc_auc_score 数值一致（并列处理相同）。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_feature_discrimination;
CREATE VIEW v_feature_discrimination AS
WITH base AS (
    SELECT enterprise_id, cohort_year, label,
           'inv_patent_ratio'     AS feature_name, inv_patent_ratio     AS value
      FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'n_patent_app',        n_patent_app        FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'n_patent_invention',  n_patent_invention  FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'n_patent_grant',      n_patent_grant      FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'tech_conversion',     tech_conversion     FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'n_ipc_subclass',      n_ipc_subclass      FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'tech_breadth_entropy',tech_breadth_entropy FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'grant_rate',          grant_rate          FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'core_staff_stability',core_staff_stability FROM v_model_train_ready
    UNION ALL SELECT enterprise_id, cohort_year, label,
           'n_inventor',          n_inventor          FROM v_model_train_ready
),
-- 每个企业同时参与「全样本」与「本队列」两个范围
long AS (
    SELECT 'all' AS cohort_scope, feature_name, value, label FROM base
    UNION ALL
    SELECT CAST(cohort_year AS TEXT), feature_name, value, label FROM base
),
-- 缺失数按未经 rank 过滤的全体算（否则缺失会被静默吞掉）
missing AS (
    SELECT cohort_scope, feature_name, COUNT(*) AS n_all,
           SUM(CASE WHEN value IS NULL THEN 1 ELSE 0 END) AS n_missing
      FROM long GROUP BY cohort_scope, feature_name
),
-- 平均秩：RANK() 遇并列返回最小秩，+（并列个数-1)/2 即平均秩
ranked AS (
    SELECT cohort_scope, feature_name, label, value,
           RANK() OVER (PARTITION BY cohort_scope, feature_name ORDER BY value)
           + (COUNT(*) OVER (PARTITION BY cohort_scope, feature_name, value) - 1) / 2.0
             AS avg_rank
      FROM long
     WHERE value IS NOT NULL
),
agg AS (
    SELECT cohort_scope, feature_name,
           COUNT(*)                                        AS n_valid,
           SUM(label)                                      AS n_pos,
           COUNT(*) - SUM(label)                           AS n_neg,
           SUM(CASE WHEN label = 1 THEN avg_rank END)      AS rank_sum_pos,
           AVG(CASE WHEN label = 1 THEN value END)         AS mean_pos,
           AVG(CASE WHEN label = 0 THEN value END)         AS mean_neg,
           MIN(value)                                      AS v_min,
           MAX(value)                                      AS v_max
      FROM ranked
     GROUP BY cohort_scope, feature_name
)
SELECT a.cohort_scope,
       a.feature_name,
       m.n_all,
       m.n_missing,
       a.n_valid,
       a.n_pos,
       a.n_neg,
       ROUND((a.rank_sum_pos - a.n_pos * (a.n_pos + 1) / 2.0)
             / NULLIF(a.n_pos * a.n_neg, 0), 4)           AS auc,
       ROUND(a.mean_pos, 4)                               AS mean_pos,
       ROUND(a.mean_neg, 4)                               AS mean_neg,
       ROUND(a.v_min, 4)                                  AS v_min,
       ROUND(a.v_max, 4)                                  AS v_max,
       CASE
         WHEN a.n_pos < 10 THEN '正例<10，AUC 不稳，只作方向参考'
         WHEN a.n_pos * a.n_neg = 0 THEN '单一类别，AUC 无定义'
         ELSE NULL
       END                                                AS stability_note
  FROM agg a
  JOIN missing m
    ON m.cohort_scope = a.cohort_scope
   AND m.feature_name = a.feature_name
 ORDER BY a.feature_name, a.cohort_scope;


-- ---------------------------------------------------------------------------
-- 2) 特征判别力的队列反差（v_feature_cohort_gap）
--    把「全样本看起来有用、分队列就散架」的特征单独拎出来。
--    口径：全样本 AUC 距 0.5 的偏离，与两个队列各自偏离的均值之差。
--    inv_patent_ratio：全样本 |0.618-0.5|=0.118，队列均 |0.784-0.5|、|0.503-0.5|
--    的均值 0.142 —— 队列差异比全样本更极端，说明效应集中在 2021 队列。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_feature_cohort_gap;
CREATE VIEW v_feature_cohort_gap AS
WITH p AS (
    SELECT feature_name,
           MAX(CASE WHEN cohort_scope = 'all'  THEN auc END) AS auc_all,
           MAX(CASE WHEN cohort_scope = 'all'  THEN n_pos END) AS n_pos_all,
           MAX(CASE WHEN cohort_scope = '2021' THEN auc END) AS auc_2021,
           MAX(CASE WHEN cohort_scope = '2021' THEN n_pos END) AS n_pos_2021,
           MAX(CASE WHEN cohort_scope = '2022' THEN auc END) AS auc_2022,
           MAX(CASE WHEN cohort_scope = '2022' THEN n_pos END) AS n_pos_2022
      FROM v_feature_discrimination
     GROUP BY feature_name
)
SELECT feature_name,
       n_pos_all,
       auc_all,
       n_pos_2021, auc_2021,
       n_pos_2022, auc_2022,
       ROUND(auc_2021 - auc_2022, 3)                      AS auc_gap_21_22,
       ROUND(ABS(auc_2021 - 0.5) - ABS(auc_all - 0.5), 3) AS gap_vs_full_2021,
       ROUND(ABS(auc_2022 - 0.5) - ABS(auc_all - 0.5), 3) AS gap_vs_full_2022,
       CASE
         WHEN auc_2021 IS NULL OR auc_2022 IS NULL THEN '队列不全'
         WHEN ABS(auc_2021 - auc_2022) >= 0.20
              THEN '队列间方向/强度不一致，全样本 AUC 不可单独引用'
         ELSE '队列间基本一致'
       END                                                AS verdict
  FROM p
 ORDER BY ABS(COALESCE(auc_2021, 0.5) - COALESCE(auc_2022, 0.5)) DESC;

-- [V15VIEWS:END]

-- ============================================================================
-- v1.6 融资特征 as-of 补算（单一事实源：src/data/migrations/v1_6_funding.sql）
-- ============================================================================
-- [V16FUND:BEGIN]
-- ============================================================================
-- v1.6 融资特征补算（依据 XM-2026-RPT-001 §2.3(3) 与 §4.1 的回落核查）
--
-- 背景：评审报告 §2.3(3) 明说「融资维度可做『有没有融过、融了几轮』」，
--       但 build_asof_features 把 m_funding / investor_prestige 整列置 NULL，
--       登记理由写「本地无时间维」「本地无数据」—— 与事实不符：
--       fact_funding_event.event_year 506/506 全填，prestige_rank 352/506 有值。
--       实测 cohort 口径 60 家 / due_minus_1 口径 95 家可算 m_funding；
--       investor_prestige 分别 59 / 75 家。这是「能算而没算」，不是口径决策。
--
-- 为什么另建表而不是改 ads_feature_asof：
--   ads_feature_asof 的 29 列结构与 v_model_dataset_asof_flat /
--   v_model_train_ready 的列绑定，且 ASOF_VER 有升版规则（不得原地改写）。
--   融资特征是新增口径，落独立表 + 独立视图，谁要谁 JOIN，不动旧口径。
--
-- 口径：
--   as-of 边界 = fact_funding_event.event_year <= 该企业该口径的 as_of_year。
--   m_funding        0/1：窗口内是否有融资事件（语义=指标体系 G5 里程碑）
--   n_funding_events 窗口内融资次数（报告原话「融了几轮」）
--   investor_prestige_best 窗口内最好机构等级（1 最好；NULL=窗口内无带等级事件）
--   investor_prestige_latest 最近一次带等级事件的机构等级
--
--   ⚠️ 只覆盖 500 家 B 套企业中的 182 家有融资记录者；打标 307 家里
--      cohort 口径 60 家有值。NULL = 「窗口内无事件」观测事实，不是缺失。
-- ============================================================================

DROP TABLE IF EXISTS ads_feature_asof_funding;
CREATE TABLE ads_feature_asof_funding (
    enterprise_id            TEXT NOT NULL,
    as_of_policy             TEXT NOT NULL CHECK (as_of_policy IN ('cohort','due_minus_1')),
    cohort_year              INTEGER NOT NULL,
    as_of_year               INTEGER NOT NULL,
    m_funding                INTEGER,          -- 0/1；无事件=0（有观测）
    n_funding_events         INTEGER,          -- 窗口内事件数；无事件=0
    investor_prestige_best    INTEGER,          -- 1=头部 VC … 4=个人天使；NULL=无带等级事件
    investor_prestige_latest  INTEGER,
    PRIMARY KEY (enterprise_id, as_of_policy)
) STRICT;

-- 生成语句见 src/data/build_asof_funding.py（幂等，重算重灌）。

-- ---------------------------------------------------------------------------
-- 查询视图：和 v_model_dataset_asof_flat 同形，直接 LEFT JOIN 即可
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_feature_asof_funding;
CREATE VIEW v_feature_asof_funding AS
SELECT enterprise_id, as_of_policy, cohort_year, as_of_year,
       m_funding, n_funding_events,
       investor_prestige_best, investor_prestige_latest
  FROM ads_feature_asof_funding;

-- ---------------------------------------------------------------------------
-- 修正 v_feature_missing_reason 对 patent_cagr 的错误归类
-- 原来归 'source_not_integrated'（源未接入），但真实原因是口径决策：
-- 原列是静态快照 CAGR，as-of 需逐年口径，两者不可比，宁缺毋滥。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_feature_missing_reason;
CREATE VIEW v_feature_missing_reason AS
SELECT f.feature_name,
       f.enterprise_id,
       f.as_of_policy,
       f.as_of_year,
       CASE
         WHEN f.feature_name IN (
                'rev_cagr','emp_cagr','rd_intensity',
                'funding_to_last_val','m_team',
                'm_cite','cap_tech_synergy','patent_citation','patent_triple_score',
                'n_patent_collab','tech_growth_inter')
           THEN 'source_not_integrated'
         WHEN f.feature_name IN ('patent_cagr')
           THEN 'definition_mismatch'   -- 原列静态快照，与逐年 as-of 口径不可比
         WHEN f.feature_name IN ('m_funding','investor_prestige')
           THEN 'computable_not_computed_v1.6'  -- v1.6 起已补算，见 ads_feature_asof_funding
         WHEN NOT EXISTS (
                SELECT 1 FROM v_patent_canonical p
                WHERE p.enterprise_id = f.enterprise_id
                  AND p.app_year <= f.as_of_year
                  AND (p.pub_date_last IS NULL
                       OR CAST(substr(p.pub_date_last, 1, 4) AS INTEGER) <= f.as_of_year))
           THEN 'no_patent_observable_at_asof'
         ELSE 'insufficient_sample'
       END                                              AS missing_reason,
       (SELECT COUNT(*) FROM v_patent_canonical p
         WHERE p.enterprise_id = f.enterprise_id
           AND p.app_year <= f.as_of_year)              AS n_patent_by_appyear,
       (SELECT COUNT(*) FROM v_patent_canonical p
         WHERE p.enterprise_id = f.enterprise_id
           AND p.app_year <= f.as_of_year
           AND (p.pub_date_last IS NULL
                OR CAST(substr(p.pub_date_last, 1, 4) AS INTEGER) <= f.as_of_year))
                                                        AS n_patent_observable
FROM ads_feature_asof f
WHERE f.is_available = 0;

-- [V16FUND:END]

-- ============================================================================
-- [V17GRADE:BEGIN]  v1.7 企业「技术与资质梯队分档」L0 规则基线
-- 完整口径说明见 src/data/migrations/v1_7_grade.sql（两处 DDL 必须保持一致）
-- 字典序分层：E 硬约束 → D 观察 → A 引领 → B 优质 → C 兜底
-- 定位：对照基线，不是信用好坏结论；非 as-of 训练口径，禁止入模验证。
-- ============================================================================
DROP VIEW IF EXISTS v_enterprise_grade_l0;
CREATE VIEW v_enterprise_grade_l0 AS
WITH b AS (
  SELECT d.enterprise_id,
         COALESCE(d.serious_dishonesty_cnt, 0) AS sd,
         COALESCE(d.biz_anomaly_cnt, 0)       AS ba,
         d.biz_status                         AS biz_status,
         COALESCE(d.risk_negative_cnt, 0)     AS rn,
         COALESCE(d.is_little_giant, 0)       AS lg,
         COALESCE(d.qcc_tech_level_ord, 0)    AS tech,
         COALESCE(d.qcc_credit_level_ord, 0)  AS cred,
         COALESCE(l.label_value, -1)          AS lab
    FROM dim_enterprise d
    LEFT JOIN ads_label l
      ON l.enterprise_id = d.enterprise_id
     AND l.label_task = 'qual_survival'
), g AS (
  SELECT b.*,
         CASE
           WHEN sd > 0 OR ba > 0
             OR (biz_status IS NOT NULL AND biz_status <> '开业') THEN 'E 预警'
           WHEN rn > 0 OR lab = 1                                 THEN 'D 观察'
           WHEN lg = 1 OR tech >= 5                               THEN 'A 引领'
           WHEN tech = 4 AND cred >= 11                           THEN 'B 优质'
           ELSE 'C 稳健'
         END AS grade,
         CASE
           WHEN sd > 0                                            THEN 'E:严重失信'
           WHEN ba > 0                                            THEN 'E:经营异常'
           WHEN biz_status IS NOT NULL AND biz_status <> '开业'   THEN 'E:非开业'
           WHEN rn > 0 AND lab = 1                                THEN 'D:风险负面+资质断档'
           WHEN rn > 0                                            THEN 'D:风险负面'
           WHEN lab = 1                                           THEN 'D:资质断档信号'
           WHEN lg = 1                                            THEN 'A:小巨人'
           WHEN tech >= 5                                         THEN 'A:技术等级卓越'
           WHEN tech = 4 AND cred >= 11                           THEN 'B:技术优秀且信用等级>=11'
           ELSE 'C:兜底'
         END AS hit_rule
    FROM b
)
SELECT enterprise_id,
       grade,
       substr(grade, 1, 1)                  AS grade_code,
       hit_rule,
       sd                                  AS serious_dishonesty_cnt,
       ba                                  AS biz_anomaly_cnt,
       biz_status,
       rn                                  AS risk_negative_cnt,
       lg                                  AS is_little_giant,
       tech                                AS qcc_tech_level_ord,
       cred                                AS qcc_credit_level_ord,
       lab                                 AS label_value_qual_survival,
       'l0_v1.0'                           AS grade_ver,
       '静态快照规则基线；非 as-of 训练口径，不得入模验证；禁止用第三方等级反向验证' AS scope_note
  FROM g;

-- [V17GRADE:END]

-- ============================================================================
-- v1.8 口径一致性：有效特征覆盖视图 + 图谱年度切片视图
-- 与 src/data/migrations/v1_8_consistency.sql 的视图定义逐字符一致。
-- 只增视图，不改事实表、不改主宽表 29 列结构、不改 ASOF_VER。
-- ============================================================================
DROP VIEW IF EXISTS v_feature_coverage_effective;
CREATE VIEW v_feature_coverage_effective AS
WITH base AS (
  SELECT as_of_policy, feature_name,
         COUNT(*)        AS n_sample,
         SUM(is_available) AS n_available
    FROM ads_feature_asof
   GROUP BY as_of_policy, feature_name
), side AS (
  SELECT as_of_policy,
         'm_funding' AS feature_name,
         COUNT(*) AS n_sample,
         SUM(CASE WHEN m_funding IS NOT NULL THEN 1 ELSE 0 END) AS n_available,
         'ads_feature_asof_funding.m_funding' AS source_object
    FROM ads_feature_asof_funding
   GROUP BY as_of_policy
  UNION ALL
  SELECT as_of_policy,
         'investor_prestige',
         COUNT(*),
         SUM(CASE WHEN investor_prestige_best IS NOT NULL THEN 1 ELSE 0 END),
         'ads_feature_asof_funding.investor_prestige_best'
    FROM ads_feature_asof_funding
   GROUP BY as_of_policy
)
SELECT b.as_of_policy,
       b.feature_name,
       b.n_sample,
       COALESCE(s.n_available, b.n_available) AS n_available,
       ROUND(1.0 * COALESCE(s.n_available, b.n_available) / b.n_sample, 4) AS coverage,
       CASE WHEN s.feature_name IS NOT NULL
            THEN 'side_table' ELSE 'main_wide_table' END AS coverage_source,
       COALESCE(s.source_object, 'ads_feature_asof') AS source_object,
       CASE WHEN s.feature_name IS NOT NULL THEN 1 ELSE 0 END AS requires_left_join
  FROM base b
  LEFT JOIN side s
    ON s.as_of_policy = b.as_of_policy
   AND s.feature_name = b.feature_name;

DROP VIEW IF EXISTS v_graph_slice_annual;
CREATE VIEW v_graph_slice_annual AS
WITH years AS (
  SELECT DISTINCT app_year AS time_slice
    FROM fact_patent
   WHERE app_year IS NOT NULL
), m AS (
  SELECT app_year AS t, 'n_node_patent' AS metric, COUNT(*) AS n
    FROM fact_patent WHERE app_year IS NOT NULL GROUP BY app_year
  UNION ALL
  SELECT app_year, 'n_edge_files_patent', COUNT(*)
    FROM fact_patent
   WHERE app_year IS NOT NULL AND enterprise_id IS NOT NULL
   GROUP BY app_year
  UNION ALL
  SELECT p.app_year, 'n_edge_belongs_ipc', COUNT(*)
    FROM fact_patent p
    JOIN bridge_patent_ipc b ON b.patent_id = p.patent_id
   WHERE p.app_year IS NOT NULL
   GROUP BY p.app_year
  UNION ALL
  SELECT p.app_year, 'n_edge_invented_by', COUNT(*)
    FROM fact_patent p
    JOIN bridge_patent_inventor i ON i.patent_id = p.patent_id
   WHERE p.app_year IS NOT NULL
   GROUP BY p.app_year
), grid AS (
  SELECT y.time_slice, k.metric
    FROM years y CROSS JOIN (SELECT DISTINCT metric FROM m) k
), filled AS (
  SELECT g.time_slice, g.metric, COALESCE(m.n, 0) AS n
    FROM grid g
    LEFT JOIN m ON m.t = g.time_slice AND m.metric = g.metric
)
SELECT time_slice,
       metric,
       n AS n_in_year,
       SUM(n) OVER (PARTITION BY metric ORDER BY time_slice
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n_cumulative
  FROM filled;

DROP VIEW IF EXISTS v_graph_slice_annual_edge;
CREATE VIEW v_graph_slice_annual_edge AS
WITH years AS (
  SELECT DISTINCT app_year AS time_slice
    FROM fact_patent
   WHERE app_year IS NOT NULL
)
SELECT y.time_slice,
       'enterprise' AS src_type, p.enterprise_id AS src_id,
       'patent'     AS dst_type, p.patent_id    AS dst_id,
       'files_patent' AS edge_type, 1.0 AS weight
  FROM years y
  JOIN fact_patent p ON p.app_year <= y.time_slice
 WHERE p.enterprise_id IS NOT NULL
UNION ALL
SELECT y.time_slice,
       'patent', p.patent_id,
       'ipc',
       CASE WHEN instr(b.ipc_code, '(') > 0
            THEN substr(b.ipc_code, 1, instr(b.ipc_code, '(') - 1)
            ELSE b.ipc_code END,
       'belongs_ipc',
       CASE WHEN b.is_main THEN 1.0 ELSE 0.5 END
  FROM years y
  JOIN fact_patent p ON p.app_year <= y.time_slice
  JOIN bridge_patent_ipc b ON b.patent_id = p.patent_id
UNION ALL
SELECT y.time_slice,
       'patent', p.patent_id,
       'inventor', i.inventor_id,
       'invented_by',
       CASE WHEN i.is_first THEN 1.0 ELSE 0.5 END
  FROM years y
  JOIN fact_patent p ON p.app_year <= y.time_slice
  JOIN bridge_patent_inventor i ON i.patent_id = p.patent_id;

-- [V18CONSISTENCY:END]

-- [V19MULTILABEL:BEGIN]
-- ---------------------------------------------------------------------------
-- ① 方向对照：把「风险方向」和「企业好坏方向」并列，杜绝再次叫反正负例
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_label_direction;
CREATE VIEW v_label_direction AS
SELECT l.enterprise_id,
       l.label_task,
       l.label_value                                   AS risk_label,
       1 - l.label_value                               AS credit_label,
       CASE WHEN l.label_value = 0 THEN 'positive' ELSE 'negative' END AS credit_class,
       '1=资质正常存续/续期成功（好企业）'              AS positive_definition,
       '0=资格到期后未再出现/断档（坏企业）'            AS negative_definition,
       l.definition_ver,
       ('ads_label.label_value 是风险预警方向(1=坏)，旧文档误把 21 家断档称为正例；' ||
        '本视图给出企业好坏方向的映射，禁止原地 UPDATE ads_label') AS direction_note
  FROM ads_label l
 WHERE l.label_task = 'qual_survival';

-- ---------------------------------------------------------------------------
-- ② 正确方向的训练入口：与 v_model_train_ready 同 17 列 + 2 列方向说明
--    label = 1 - 原 label → 286 正例（好企业）/ 21 负例（坏企业）
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_model_train_ready_credit;
CREATE VIEW v_model_train_ready_credit AS
SELECT enterprise_id, name_display, region_code, cohort_year, split,
       1 - label AS label,
       n_patent_app, n_patent_invention, n_patent_grant,
       inv_patent_ratio, tech_conversion,
       n_ipc_subclass, tech_breadth_entropy, tech_breadth_ipc_class,
       grant_rate, core_staff_stability, n_inventor,
       'credit_good_positive' AS label_direction,
       '1=正例/好企业(资质存续)，0=负例/坏企业(资质断档)' AS positive_class
  FROM v_model_train_ready;

-- ---------------------------------------------------------------------------
-- ③ 正确方向机制台账：n_positive 现在真的是「好企业」数
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_label_mechanism_credit;
CREATE VIEW v_label_mechanism_credit AS
SELECT l.n_events,
       l.cohort_year,
       COUNT(*)                                     AS n_enterprise,
       SUM(1 - l.label_value)                       AS n_positive,
       SUM(l.label_value)                           AS n_negative,
       ROUND(1.0 * SUM(1 - l.label_value) / COUNT(*), 4) AS positive_rate,
       '1=好企业(存续)/正例；0=坏企业(断档)/负例'     AS direction_note
  FROM ads_label l
 GROUP BY l.n_events, l.cohort_year;

-- ---------------------------------------------------------------------------
-- ④ 多标签矩阵：四轴 6 个二值锚点 + 3 个有序锚点，方向统一 1=好
--    阈值全部来自实测分布且写死在 DDL 里（可审计、无主观权重）：
--      y_qual_ladder_good  >= 3   三项资质齐全（45/500）
--      y_qcc_credit_good   >= 11  信用等级 11–12（382/500，与 L0 的 B 档同阈值）
--      y_qcc_tech_good     >= 4   技术等级良好/优秀/卓越（157/500）
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_enterprise_label_matrix;
CREATE VIEW v_enterprise_label_matrix AS
WITH base AS (
  SELECT d.enterprise_id,
         d.name_display,
         CASE
           WHEN d.serious_dishonesty_cnt IS NULL
             OR d.biz_anomaly_cnt IS NULL
             OR d.biz_status IS NULL THEN NULL
           WHEN COALESCE(d.serious_dishonesty_cnt, 0) > 0
             OR COALESCE(d.biz_anomaly_cnt, 0) > 0
             OR d.biz_status <> '开业' THEN 0
           ELSE 1
         END                                        AS y_hard_outcome,
         CASE WHEN COALESCE(d.risk_negative_cnt, 0) > 0 THEN 0 ELSE 1 END
                                                    AS y_risk_free,
         (CASE WHEN COALESCE(d.is_tech_sme, 0) = 1 THEN 1 ELSE 0 END
        + CASE WHEN COALESCE(d.is_high_tech_union, 0) = 1 THEN 1 ELSE 0 END
        + CASE WHEN COALESCE(d.is_little_giant, 0) = 1 THEN 1 ELSE 0 END)
                                                    AS y_qual_ladder,
         1 - l.label_value                          AS y_survival,
         d.qcc_credit_level_ord                    AS y_qcc_credit,
         d.qcc_tech_level_ord                      AS y_qcc_tech
    FROM dim_enterprise d
    LEFT JOIN ads_label l
      ON l.enterprise_id = d.enterprise_id
     AND l.label_task = 'qual_survival'
), bin AS (
  SELECT base.*,
         CASE WHEN y_qual_ladder >= 3 THEN 1 ELSE 0 END AS y_qual_ladder_good,
         CASE WHEN y_qcc_credit >= 11 THEN 1 ELSE 0 END AS y_qcc_credit_good,
         CASE WHEN y_qcc_tech >= 4 THEN 1 ELSE 0 END    AS y_qcc_tech_good
    FROM base
)
SELECT enterprise_id,
       name_display,
       -- 二值锚点（1=好/正例，0=坏/负例，NULL=不可判定）
       y_hard_outcome,                 -- A 轴 · 硬结果（严重失信/经营异常/非开业）
       y_risk_free,                   -- A 轴 · 风险负面
       y_survival,                    -- C 轴 · 资质存续（193 家未打标 = NULL）
       y_qual_ladder_good,            -- B 轴 · 资质阶梯达标
       y_qcc_credit_good,             -- D 轴 · 第三方信用等级达标
       y_qcc_tech_good,               -- D 轴 · 第三方技术等级达标
       -- 有序锚点（原始等级，不参与投票，用于同档内排序）
       y_qual_ladder,
       y_qcc_credit,
       y_qcc_tech,
       -- 票数与一致性
       6 - CASE WHEN y_hard_outcome IS NULL THEN 1 ELSE 0 END
         - CASE WHEN y_survival IS NULL THEN 1 ELSE 0 END AS n_labels_observed,
       COALESCE(y_hard_outcome, 0) + y_risk_free + COALESCE(y_survival, 0)
         + y_qual_ladder_good + y_qcc_credit_good + y_qcc_tech_good AS n_good,
       (6 - CASE WHEN y_hard_outcome IS NULL THEN 1 ELSE 0 END
          - CASE WHEN y_survival IS NULL THEN 1 ELSE 0 END)
       - (COALESCE(y_hard_outcome, 0) + y_risk_free + COALESCE(y_survival, 0)
          + y_qual_ladder_good + y_qcc_credit_good + y_qcc_tech_good) AS n_bad,
       ROUND(1.0 * (COALESCE(y_hard_outcome, 0) + y_risk_free
                    + COALESCE(y_survival, 0) + y_qual_ladder_good
                    + y_qcc_credit_good + y_qcc_tech_good)
             / (6 - CASE WHEN y_hard_outcome IS NULL THEN 1 ELSE 0 END
                  - CASE WHEN y_survival IS NULL THEN 1 ELSE 0 END), 4) AS label_agreement,
       'A/D 轴相对独立；B(资质阶梯)与 C(资质存续)同源于公示名单，不构成独立印证' AS axis_note
  FROM bin;

-- ---------------------------------------------------------------------------
-- ⑤ 多标签五档 + L0 对照：硬约束优先，其余按「好标签票数」定档
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_enterprise_multilabel_grade;
CREATE VIEW v_enterprise_multilabel_grade AS
SELECT m.enterprise_id,
       m.name_display,
       m.y_survival,
       m.y_hard_outcome,
       m.y_risk_free,
       CASE
         WHEN m.y_hard_outcome = 0                       THEN 'E 预警'
         WHEN m.y_survival = 0 OR m.y_risk_free = 0      THEN 'D 观察'
         WHEN m.n_good >= 5                              THEN 'A 引领'
         WHEN m.n_good >= 4                              THEN 'B 优质'
         ELSE 'C 稳健'
       END                                             AS grade_multilabel,
       substr(CASE
         WHEN m.y_hard_outcome = 0                       THEN 'E 预警'
         WHEN m.y_survival = 0 OR m.y_risk_free = 0      THEN 'D 观察'
         WHEN m.n_good >= 5                              THEN 'A 引领'
         WHEN m.n_good >= 4                              THEN 'B 优质'
         ELSE 'C 稳健'
       END, 1, 1)                                      AS grade_code,
       g.grade                                         AS grade_l0,
       CASE WHEN substr(g.grade, 1, 1) = substr(CASE
              WHEN m.y_hard_outcome = 0                       THEN 'E 预警'
              WHEN m.y_survival = 0 OR m.y_risk_free = 0      THEN 'D 观察'
              WHEN m.n_good >= 5                              THEN 'A 引领'
              WHEN m.n_good >= 4                              THEN 'B 优质'
              ELSE 'C 稳健' END, 1, 1) THEN 1 ELSE 0 END AS is_consistent_with_l0,
       m.n_labels_observed,
       m.n_good,
       m.n_bad,
       m.label_agreement,
       'l1_multilabel_v1.0'                            AS grade_ver,
       '静态多标签规则基线；B/C 轴同源不独立；不得报准确率，不得用 D 轴等级反向验证' AS scope_note
  FROM v_enterprise_label_matrix m
  LEFT JOIN v_enterprise_grade_l0 g
    ON g.enterprise_id = m.enterprise_id;

-- ---------------------------------------------------------------------------
-- ⑥ 交叉测评汇总（长表）：档次 → 各锚点正例率、标签间一致性、L0 漂移
--    ⚠️ 循环定义边界：分档判据用了 y_survival / y_hard_outcome，
--      再用这两列算「各档正例率」在数学上必然恒为 1.0（A–D 档硬结果、A–C 档存续），
--      这是规则使然，不是多标签验证了分档有效。该两列只用于暴露口径，
--      不得当作外部效度；真正的验证必须用未参与分档的独立 A 轴结果标签。
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_multilabel_eval_summary;
CREATE VIEW v_multilabel_eval_summary AS
WITH g AS (
  SELECT grade_multilabel AS group_value, COUNT(*) AS n,
         SUM(CASE WHEN y_survival = 1 THEN 1 ELSE 0 END) AS n_survival_good,
         SUM(CASE WHEN y_survival IS NOT NULL THEN 1 ELSE 0 END) AS n_survival_obs,
         SUM(CASE WHEN y_hard_outcome = 1 THEN 1 ELSE 0 END) AS n_hard_good,
         SUM(CASE WHEN y_hard_outcome IS NOT NULL THEN 1 ELSE 0 END) AS n_hard_obs,
         ROUND(AVG(label_agreement), 4) AS mean_agreement,
         SUM(is_consistent_with_l0) AS n_same_l0
    FROM v_enterprise_multilabel_grade
   GROUP BY grade_multilabel
)
SELECT '档次日数' AS metric, group_value, n AS n_enterprise,
       NULL AS n_positive, NULL AS n_observed, NULL AS positive_rate,
       mean_agreement, n_same_l0,
       '每档必须带家数与置信区间；E/D 档样本塌陷，点估计无意义' AS note
  FROM g
UNION ALL
SELECT '资质存续正例率', group_value, n,
       n_survival_good, n_survival_obs,
       CASE WHEN n_survival_obs > 0
            THEN ROUND(1.0 * n_survival_good / n_survival_obs, 4) END,
       NULL, NULL,
       'C 轴：1=好企业(存续)；未打标 193 家不计入分母'
  FROM g
UNION ALL
SELECT '硬结果正例率', group_value, n,
       n_hard_good, n_hard_obs,
       CASE WHEN n_hard_obs > 0
            THEN ROUND(1.0 * n_hard_good / n_hard_obs, 4) END,
       NULL, NULL,
       'A 轴：1=无严重失信/经营异常/非开业；字段缺失 37 家不计入分母'
  FROM g
UNION ALL
SELECT '多标签 vs L0 一致', 'ALL',
       (SELECT COUNT(*) FROM v_enterprise_multilabel_grade),
       (SELECT SUM(is_consistent_with_l0) FROM v_enterprise_multilabel_grade),
       (SELECT COUNT(*) FROM v_enterprise_multilabel_grade),
       ROUND(1.0 * (SELECT SUM(is_consistent_with_l0) FROM v_enterprise_multilabel_grade)
             / (SELECT COUNT(*) FROM v_enterprise_multilabel_grade), 4),
       NULL, NULL,
       '一致率低说明单标签 L0 与各轴多标签测的不是同一件事，属预期发现'
UNION ALL
SELECT '标签锚点覆盖率', 'y_survival',
       (SELECT COUNT(*) FROM v_enterprise_label_matrix),
       (SELECT COUNT(y_survival) FROM v_enterprise_label_matrix),
       (SELECT COUNT(*) FROM v_enterprise_label_matrix),
       ROUND(1.0 * (SELECT COUNT(y_survival) FROM v_enterprise_label_matrix)
             / (SELECT COUNT(*) FROM v_enterprise_label_matrix), 4),
       NULL, NULL,
       'C 轴仅 307/500 有标签，其余 193 家为 excluded，禁止当负例';
-- [V19MULTILABEL:END]

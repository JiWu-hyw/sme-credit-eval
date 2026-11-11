-- =============================================================================
-- v2.0  A 轴司法违约 + 主要人员（企查查批量查询回传，2026-11-11）
--
-- 纪律：本次只「新增并列层」，严格不改 ads_label / v_enterprise_label_matrix /
--       v_enterprise_multilabel_grade / v_model_train_ready 等任何已发布视图，
--       不改 dim_enterprise 任何列，不改 29 列主宽表，不改 ASOF_VER。
--       复合负面事件以 *并列候选* 视图表达，正式切换需另一次升版拍板。
--
-- 入库清洗（见 apply_v2_0_axis_a.py）：
--   ① 主要人员原始 2569 行含 490 行「每户表头」脏行（姓名='姓名'、职务='职务'），
--      入库只保留清洗后的 2079 行；用户主表「高管_人数」为未清洗口径，
--      故另存 ods_qcc_axis_a_snapshot 原始快照用于对照，禁止直接当人数用。
--   ② 三类金额语义不同（失信=涉案金额 / 被执行=执行标的 / 终本=未履行金额），
--      分列保存并带 amount_kind，禁止无条件相加当作「未履行金额」。
--   ③ 司法明细 1 条日期为 '-'，清洗为 NULL；1 户企业名
--      「浙江九州未来信息科技有限公司」经别名归一映射到库内
--      「浙江九州云信息科技有限公司」，company_name 保留原文。
-- =============================================================================

-- [V20AXISA:BEGIN]  v2.0 A 轴司法违约 + 主要人员（并列层）

-- 司法违约明细：失信被执行人 / 被执行人 / 终本案件，34 条 / 6 户
CREATE TABLE IF NOT EXISTS ods_qcc_judicial (
    row_id          INTEGER PRIMARY KEY,
    enterprise_id   TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,  -- 企业代理键，已按别名归一
    company_name    TEXT NOT NULL,        -- 企查查回传原文企业名（可能与库内登记名不同）
    event_type      TEXT NOT NULL CHECK(event_type IN ('失信被执行人','被执行人','终本案件')),  -- 司法事件类别
    case_no         TEXT,                 -- 案号，如 （2025）浙0105执11575号
    court           TEXT,                 -- 执行法院
    amount_yuan     REAL,                 -- 金额（元），语义由 amount_kind 决定
    amount_kind     TEXT CHECK(amount_kind IN ('涉案金额','执行标的','未履行金额')),  -- 金额口径，三类不可直接相加
    event_date      TEXT,                 -- 发布日期 / 立案日期 / 终本日期；原始 '-' 已清洗为 NULL
    applicant       TEXT,                 -- 疑似申请执行人
    source_batch    TEXT,                 -- 企查查回传批次（zip 文件名）
    built_at        TEXT                  -- 入库时间
) STRICT;

-- 主要人员明细：清洗后 2079 行 / 490 户
CREATE TABLE IF NOT EXISTS ods_qcc_person (
    row_id          INTEGER PRIMARY KEY,
    enterprise_id   TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,  -- 企业代理键，已按别名归一
    company_name    TEXT NOT NULL,        -- 企查查回传原文企业名
    person_name     TEXT NOT NULL,        -- 姓名；490 行表头脏行已剔除
    position        TEXT,                 -- 职务原文，逗号分隔，如「经理,董事」
    is_legal_rep    INTEGER CHECK(is_legal_rep IN (0,1)),  -- 是否法定代表人（对照 dim_enterprise.legal_rep）
    source_batch    TEXT,                 -- 企查查回传批次（zip 文件名）
    built_at        TEXT                  -- 入库时间
) STRICT;

-- 用户主表本轮新增的 17 列原始快照（500 行），保留未清洗口径供追溯
CREATE TABLE IF NOT EXISTS ods_qcc_axis_a_snapshot (
    row_id              INTEGER PRIMARY KEY,
    enterprise_id       TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,  -- 企业代理键
    company_name        TEXT NOT NULL,    -- 主表企业名称
    n_dishonest_raw     INTEGER,          -- A轴_失信被执行人_条数（原始口径）
    n_executed_raw      INTEGER,          -- A轴_被执行人_条数（原始口径）
    n_terminal_raw      INTEGER,          -- A轴_终本案件_条数（原始口径）
    amount_wan_raw      REAL,             -- A轴_未履行金额_万元（三类金额混加，仅作快照，禁止当单一口径）
    has_event_raw       TEXT CHECK(has_event_raw IN ('是','否')),  -- A轴_是否有违约事件
    event_types_raw     TEXT,             -- A轴_违约类型
    n_court_raw         INTEGER,          -- A轴_执行法院数
    case_sample_raw     TEXT,             -- A轴_案号样例（前 3 条，分号分隔）
    n_person_raw        INTEGER,          -- 高管_人数（含表头脏行，每户多计 1 人）
    person_names_raw    TEXT,             -- 高管_姓名（分号分隔）
    n_chairman_raw      INTEGER,          -- 高管_董事长数
    n_director_raw      INTEGER,          -- 高管_董事数
    n_supervisor_raw    INTEGER,          -- 高管_监事数
    n_manager_raw       INTEGER,          -- 高管_经理数
    n_gm_raw            INTEGER,          -- 高管_总经理数
    n_cfo_raw           INTEGER,          -- 高管_财务负责人数
    legal_in_list_raw   TEXT CHECK(legal_in_list_raw IN ('是','否','NAN')),  -- 高管_法人是否在列
    source_file         TEXT,             -- 来源文件（合并_500家_企业主表.csv）
    built_at            TEXT              -- 入库时间
) STRICT;

CREATE INDEX IF NOT EXISTS idx_qcc_jud_ent ON ods_qcc_judicial(enterprise_id, event_type);
CREATE INDEX IF NOT EXISTS idx_qcc_jud_type ON ods_qcc_judicial(event_type);
CREATE INDEX IF NOT EXISTS idx_qcc_person_ent ON ods_qcc_person(enterprise_id);
CREATE INDEX IF NOT EXISTS idx_qcc_person_name ON ods_qcc_person(person_name);

-- 企业层司法聚合：500 行，无事件企业补 0（LEFT JOIN），金额按三类分列
CREATE VIEW v_axis_a_judicial AS
SELECT d.enterprise_id,
       d.name_display,
       SUM(CASE WHEN j.event_type = '失信被执行人' THEN 1 ELSE 0 END) AS n_dishonest,
       SUM(CASE WHEN j.event_type = '被执行人'     THEN 1 ELSE 0 END) AS n_executed,
       SUM(CASE WHEN j.event_type = '终本案件'     THEN 1 ELSE 0 END) AS n_terminal,
       COUNT(j.row_id)                                              AS n_case_total,
       ROUND(SUM(CASE WHEN j.event_type = '失信被执行人'
                      THEN COALESCE(j.amount_yuan, 0) ELSE 0 END), 2) AS amount_dishonest_yuan,
       ROUND(SUM(CASE WHEN j.event_type = '被执行人'
                      THEN COALESCE(j.amount_yuan, 0) ELSE 0 END), 2) AS amount_executed_yuan,
       ROUND(SUM(CASE WHEN j.event_type = '终本案件'
                      THEN COALESCE(j.amount_yuan, 0) ELSE 0 END), 2) AS amount_unperformed_yuan,
       COUNT(DISTINCT j.court)                                      AS n_court,
       MIN(j.event_date)                                            AS first_event_date,
       MAX(j.event_date)                                            AS last_event_date,
       CASE WHEN COUNT(j.row_id) > 0 THEN 1 ELSE 0 END              AS has_judicial_event,
       CASE WHEN COUNT(j.row_id) > 0
            THEN 'judicial_qcc_v1' END                              AS event_ver,
       '三类金额语义不同：涉案金额 / 执行标的 / 未履行金额，禁止无条件相加' AS amount_note
  FROM dim_enterprise d
  LEFT JOIN ods_qcc_judicial j ON j.enterprise_id = d.enterprise_id
 GROUP BY d.enterprise_id, d.name_display;

-- 主要人员企业层汇总：清洗口径 vs 主表原始口径对照
CREATE VIEW v_enterprise_person_summary AS
SELECT d.enterprise_id,
       d.name_display,
       d.legal_rep,
       COUNT(p.row_id)                                              AS n_person_clean,
       SUM(CASE WHEN p.is_legal_rep = 1 THEN 1 ELSE 0 END)          AS n_legal_rep_in_list,
       SUM(CASE WHEN p.position LIKE '%董事长%' THEN 1 ELSE 0 END)   AS n_chairman,
       SUM(CASE WHEN p.position LIKE '%董事%'   THEN 1 ELSE 0 END)   AS n_director,
       SUM(CASE WHEN p.position LIKE '%监事%'   THEN 1 ELSE 0 END)   AS n_supervisor,
       MAX(s.n_person_raw)                                          AS n_person_raw,
       MAX(s.n_person_raw) - COUNT(p.row_id)                        AS n_header_rows_dropped,
       CASE WHEN COUNT(p.row_id) = 0 THEN 'source_missing'
            ELSE 'qcc_person_v1' END                                 AS coverage_state
  FROM dim_enterprise d
  LEFT JOIN ods_qcc_person p ON p.enterprise_id = d.enterprise_id
  LEFT JOIN ods_qcc_axis_a_snapshot s ON s.enterprise_id = d.enterprise_id
 GROUP BY d.enterprise_id, d.name_display, d.legal_rep;

-- A 轴复合负面事件（并列候选）：司法违约 ∪ 现有硬结果，1=无事件（好）/0=有事件（坏）/NULL=不可判定
CREATE VIEW v_axis_a_compound_event AS
SELECT m.enterprise_id,
       m.name_display,
       m.y_hard_outcome                                  AS y_hard_outcome_v1,
       j.n_case_total,
       j.has_judicial_event,
       -- 司法事件是独立于公示名单的行政/司法记录，可直接判坏；其余沿用 v1 口径
       CASE WHEN j.has_judicial_event = 1 OR m.y_hard_outcome = 0 THEN 0
            WHEN m.y_hard_outcome IS NULL THEN NULL
            ELSE 1 END                                    AS y_compound_v2,
       (CASE WHEN j.has_judicial_event = 1 THEN 1 ELSE 0 END
      + CASE WHEN m.y_hard_outcome = 0 THEN 1 ELSE 0 END) AS n_event_sources,
       TRIM(CASE WHEN j.has_judicial_event = 1 THEN '司法违约 ' ELSE '' END
         || CASE WHEN m.y_hard_outcome = 0 THEN '严重失信/经营异常/非开业' ELSE '' END)
                                                         AS event_sources,
       '并列候选，未替换 v_enterprise_label_matrix.y_hard_outcome；正式切换需升版拍板' AS scope_note
  FROM v_enterprise_label_matrix m
  LEFT JOIN v_axis_a_judicial j ON j.enterprise_id = m.enterprise_id;

-- 五档交叉：v1 正式档 vs 复合事件，检验「A→E 事件率单调递增」
CREATE VIEW v_axis_a_grade_cross AS
SELECT g.grade_multilabel                                  AS grade_v1,
       COUNT(*)                                            AS n_enterprise,
       SUM(CASE WHEN c.y_compound_v2 = 0 THEN 1 ELSE 0 END) AS n_compound_adverse,
       SUM(CASE WHEN c.y_compound_v2 IS NOT NULL THEN 1 ELSE 0 END) AS n_observable,
       ROUND(1.0 * SUM(CASE WHEN c.y_compound_v2 = 0 THEN 1 ELSE 0 END)
             / NULLIF(SUM(CASE WHEN c.y_compound_v2 IS NOT NULL THEN 1 ELSE 0 END), 0), 4)
                                                          AS adverse_rate,
       SUM(CASE WHEN c.has_judicial_event = 1 THEN 1 ELSE 0 END) AS n_judicial_only,
       SUM(CASE WHEN c.y_hard_outcome_v1 = 0 THEN 1 ELSE 0 END)  AS n_hard_v1,
       SUM(CASE WHEN c.y_compound_v2 = 0 AND g.grade_multilabel NOT LIKE 'E%'
                THEN 1 ELSE 0 END)                          AS n_new_e_candidate
  FROM v_enterprise_multilabel_grade g
  JOIN v_axis_a_compound_event c ON c.enterprise_id = g.enterprise_id
 GROUP BY g.grade_multilabel;

-- [V20AXISA:END]

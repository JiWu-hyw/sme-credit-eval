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

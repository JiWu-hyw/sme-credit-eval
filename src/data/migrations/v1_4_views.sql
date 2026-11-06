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

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

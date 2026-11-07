-- ============================================================================
-- v1.8 口径一致性：有效特征覆盖视图 + 图谱年度切片视图
-- 依据：评审报告 §4.1 / §5.2(1) / §5.3；交付说明「融资 LEFT JOIN 契约」
--
-- 定位：只增视图，不改任何事实表数据、不改主宽表 29 列结构、不改 ASOF_VER。
--
-- ① v_feature_coverage_effective：修「覆盖视图与旁路表口径分叉」。
--    v_asof_feature_coverage 只扫 ads_feature_asof，把 m_funding /
--    investor_prestige 报成 0 覆盖；但 v1.6 起的补算落在旁路表
--    ads_feature_asof_funding。本视图把这两列按 (as_of_policy, feature_name)
--    替换为旁路表实测值，并显式标 coverage_source / requires_left_join，
--    其余 12 个零覆盖特征保持 0 —— 11 个真空字段一个都不许被「补」出来。
--    原视图 v_asof_feature_coverage 保持不动，已发布契约与 ASOF_VER 不受影响。
--
-- ② v_graph_slice_annual：年度切片规模台账（长表，29 年 × 4 指标）。
--    口径 = 按 fact_patent.app_year <= T 累计（与 README §自建年度切片一致，
--    但修正了两处：关联键用 fact_patent.patent_id 而非已去重的
--    v_patent_master.patent_id_repr，后者会漏 19% 的 IPC / 发明人边）。
--
-- ③ v_graph_slice_annual_edge：边级年度切片，把 README 的 3 条 SQL 落成可查视图。
--    IPC 节点 ID 按 _norm_ipc 口径剥掉 WIPO 版本号后缀，与预置图节点对齐。
--
-- ⚠️ 物理表 ads_graph_node.time_slice / ads_graph_edge.time_slice 仍全为 0
--    （全期累计），这是与既有契约绑定的设计，且 build_graph_v2 幂等重建会
--    写回 0；年度切片一律通过本组视图按 T 查询，禁止 UPDATE 物理表。
-- ============================================================================

-- ---------------------------------------------------------------------------
-- ① 有效特征覆盖
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- ② 年度切片规模台账
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- ③ 边级年度切片（README 三条 SQL 的库内落地版）
-- ---------------------------------------------------------------------------
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

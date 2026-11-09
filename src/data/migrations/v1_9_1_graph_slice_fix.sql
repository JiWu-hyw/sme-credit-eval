-- [V191GRAPHFIX:BEGIN]
-- 修订日期：2026-11-09
-- 触发：算法端评审报告 §5.2(1)「只有 1 个时间切片 / STGNN 无时序输入」
--
-- 本次修订不改变物理表数据、不新增表或视图、不升 schema 版本号（仍为 v1.9），
-- 只重定义已有的两个年度切片视图 + 新建两个查询索引，解决一个实测出来的
-- 同源性 bug：
--   旧 v_graph_slice_annual / _edge 从 fact_patent JOIN bridge_* 现算边，
--   与物理表 ads_graph_edge 不同源：belongs_ipc 末年累计 51,795 条，
--   而物理图只有 51,776 条，差 19 条；端点 ID 虽一致，但边集不一致，
--   STGNN 按年度切片建图会与静态图对不上。
--
-- 新定义直接以 ads_graph_edge 为唯一边源，用 patent_id 反查 app_year 作为
-- 「发生年份 t」，按 t <= time_slice 展开成累计快照：
--   * 末年（2026）三类边计数与物理表严格相等（51,776 / 32,203 / 77,899）；
--   * 所有 src_id / dst_id 原样取自物理表，孤立端点 0；
--   * 每条边只出现一次，不重复计数。
--
-- ⚠️ 性能约束（不要「简化」掉）：必须用 CROSS JOIN + INDEXED BY 强制
-- 「years（29 行）→ fact_patent（年份范围索引）→ ads_graph_edge（边端点索引）」
-- 的连接顺序。若改成普通 JOIN / CTE，SQLite 会改用 ads_graph_edge 驱动并把
-- years 协程重复构造 16 万次，全表 COUNT(*) 实测从 0.9s 劣化到 >170s，
-- 会直接卡死数据字典导出与 API 查询。守卫 tests/test_graph_temporal_contract.py
-- 有 5 秒性能闸门。
--   实测：COUNT(*) 847,677 行 ≈0.88s；WHERE time_slice=2026 ≈0.20s。
--
-- 口径边界（写进报告，不要在代码里偷偷改）：
--   * ads_graph_node / ads_graph_edge.time_slice 全为 0 是「全量单快照」标记，
--     不是缺失值，本次不 UPDATE 底表任何行（v1.9 起「底表不原地改写」是守卫项）。
--     新建的两个索引只影响查询计划，不写业务数据、不增加表/列数。
--   * 本视图是「累计快照」口径（截至该年末可见的所有边），不是「当年新增」；
--     要当年新增请用 n_in_year 或按 t = time_slice 过滤。
--   * funded_by（583 条）与 tech_similar（8,749 条）不进时序切面：
--     前者缺「边—融资事件年份」的可靠映射（investor_names 为 JSON 名称串，
--     与 investor 节点 ID 需二次匹配），后者是全期静态相似度、切年会产生
--     伪时序。二者作为静态边在物理图里仍可用，禁止据此声称「全边时序化」。
--   * 立项六类实体缺「产品」节点：库内无任何产品/商标/经营范围源表，
--     用专利标题或 IPC 聚类造「产品节点」会形成伪实体，禁止；登记册定稿为
--     需外部数据源（P2）。当前技术域语义由 ipc 节点承担。
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_gedge_dst_typ ON ads_graph_edge(dst_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_gedge_src_typ ON ads_graph_edge(src_id, edge_type);

DROP VIEW IF EXISTS v_graph_slice_annual_edge;
CREATE VIEW v_graph_slice_annual_edge AS
SELECT y.time_slice,
       e.src_type, e.src_id,
       e.dst_type, e.dst_id,
       e.edge_type, e.weight
  FROM (SELECT DISTINCT app_year AS time_slice
          FROM fact_patent WHERE app_year IS NOT NULL) y
  CROSS JOIN fact_patent p INDEXED BY idx_pat_appyear
  CROSS JOIN ads_graph_edge e INDEXED BY idx_gedge_dst_typ
 WHERE p.app_year <= y.time_slice
   AND e.dst_id = p.patent_id
   AND e.edge_type = 'files_patent'
UNION ALL
SELECT y.time_slice,
       e.src_type, e.src_id,
       e.dst_type, e.dst_id,
       e.edge_type, e.weight
  FROM (SELECT DISTINCT app_year AS time_slice
          FROM fact_patent WHERE app_year IS NOT NULL) y
  CROSS JOIN fact_patent p INDEXED BY idx_pat_appyear
  CROSS JOIN ads_graph_edge e INDEXED BY idx_gedge_src_typ
 WHERE p.app_year <= y.time_slice
   AND e.src_id = p.patent_id
   AND e.edge_type IN ('belongs_ipc', 'invented_by');

DROP VIEW IF EXISTS v_graph_slice_annual;
CREATE VIEW v_graph_slice_annual AS
WITH years AS (
  SELECT DISTINCT app_year AS time_slice
    FROM fact_patent
   WHERE app_year IS NOT NULL
),
m AS (
  SELECT app_year AS t, 'n_node_patent' AS metric, COUNT(*) AS n
    FROM fact_patent
   WHERE app_year IS NOT NULL
   GROUP BY app_year
  UNION ALL
  -- files_patent: patent_id 在 dst_id
  SELECT p.app_year, 'n_edge_' || e.edge_type, COUNT(*)
    FROM fact_patent p INDEXED BY idx_pat_appyear
    CROSS JOIN ads_graph_edge e INDEXED BY idx_gedge_dst_typ
   WHERE e.dst_id = p.patent_id AND e.edge_type = 'files_patent'
     AND p.app_year IS NOT NULL
   GROUP BY p.app_year, e.edge_type
  UNION ALL
  -- belongs_ipc / invented_by: patent_id 在 src_id
  SELECT p.app_year, 'n_edge_' || e.edge_type, COUNT(*)
    FROM fact_patent p INDEXED BY idx_pat_appyear
    CROSS JOIN ads_graph_edge e INDEXED BY idx_gedge_src_typ
   WHERE e.src_id = p.patent_id
     AND e.edge_type IN ('belongs_ipc', 'invented_by')
     AND p.app_year IS NOT NULL
   GROUP BY p.app_year, e.edge_type
),
grid AS (
  SELECT y.time_slice, k.metric
    FROM years y CROSS JOIN (SELECT DISTINCT metric FROM m) k
),
filled AS (
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
-- [V191GRAPHFIX:END]

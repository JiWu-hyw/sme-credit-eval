-- =============================================================================
-- v2.1  商标明细 + 产品节点并列层（企查查批量查询回传，2026-11-12）
--
-- 数据源：_data_collect/out/
--   商标明细.csv              8004 条 / 444 家 / 尼斯 1–45 类
--   产品节点_尼斯分类.csv      45 行（35 产品类 + 10 服务类）
--   合并_500家_企业主表.csv    500 行，本轮新增 9 列 商标_* 原始口径（仅作对照，不覆盖库内重算）
--
-- 纪律（与 v2.0 同构，只新增并列层）：
--   ① 严格不改 ads_graph_node / ads_graph_edge / v_graph_slice_annual* 任何对象，
--      产品节点 **不并入主图**，29 帧年度时序切面与守恒校验逐项不变；
--   ② 严格不改 ads_label / v_enterprise_label_matrix / v_enterprise_multilabel_grade /
--      v_model_train_ready* / dim_enterprise 任何列，不改 ASOF_VER / DEFINITION_VER；
--   ③ 产品节点粒度是 **尼斯核定类别（35 类）**，不是 SKU 级产品，也不是商标名称。
--      立项六类实体只能登记为「类别粒度部分闭合，SKU 级仍缺」，
--      禁止据此宣称「六类实体已完整覆盖」；
--   ④ 35–39、41–45 共 10 个服务类实际有 1902 条商标，但按用户口径
--      `是否服务类=是` **不作为产品节点**，在维表保留并标 is_product_node=0；
--   ⑤ 主表「商标_品牌数」与明细精确去重口径存在 68 家差异，规则未能反推，
--      故库内一律以贴源明细重算（n_brand_exact），主表 9 列仅作外部对照，
--      禁止混用或把一个当另一个的校验基准。
--
-- 入库清洗（见 apply_v2_1_product_node.py）：
--   · 1 条申请日期 '-'、2245 条注册公告日期 '-' → NULL；
--   · 商标状态 39 种保留原文，另给 is_registered（状态以「已注册」开头 = 1），
--     该口径与主表「商标_有效件数」合计 5257 严格一致；
--   · 企业名称经主表 QCC_现用名称 / QCC_曾用名 桥接，444 家 100% 命中 dim_enterprise，
--     company_name 保留回传原文；
--   · 桥接表只保留 35 个产品类，共 1418 行 / 433 户 / 6102 条产品类商标。
-- =============================================================================

-- [V21PRODUCT:BEGIN]  v2.1 商标明细 + 产品节点（并列层，不并入主图）

-- 商标贴源明细：8004 条 / 444 家，一行一条商标
CREATE TABLE IF NOT EXISTS ods_qcc_trademark (
    row_id            INTEGER PRIMARY KEY,
    enterprise_id     TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,  -- 企业代理键，经 QCC 现用名/曾用名桥接
    company_name      TEXT NOT NULL,        -- 回传原文企业名（可能与库内登记名不同）
    trademark_name    TEXT,                 -- 商标名称 / 品牌；含 '-' 与「图形」占位，不清洗原文
    nice_class        INTEGER NOT NULL CHECK(nice_class BETWEEN 1 AND 45),  -- 尼斯分类号 1–45
    nice_class_raw    TEXT,                 -- 国际分类原文，如「7类 机械设备」
    class_kind        TEXT NOT NULL CHECK(class_kind IN ('product','service')),  -- 产品类 35 / 服务类 10
    trademark_status  TEXT NOT NULL,        -- 商标状态原文（39 种，复合状态以 · 连接）
    is_registered     INTEGER NOT NULL CHECK(is_registered IN (0,1)),  -- 状态以「已注册」开头=1；合计 5257
    reg_no            TEXT NOT NULL,        -- 申请/注册号，无空缺
    apply_date        TEXT,                 -- 申请日期；原始 '-' 已清洗为 NULL（1 条）
    reg_announce_date TEXT,                 -- 注册公告日期；原始 '-' 已清洗为 NULL（2245 条）
    source_file       TEXT,                 -- 来源文件（商标明细.csv）
    built_at          TEXT                  -- 入库时间
) STRICT;

-- 尼斯分类维表 / 产品节点候选：45 行，其中 35 行 is_product_node=1
CREATE TABLE IF NOT EXISTS dim_product_nice_class (
    product_id           TEXT PRIMARY KEY,   -- PRD001…PRD045
    product_name         TEXT NOT NULL,      -- 如「7类机械设备」
    nice_class           INTEGER NOT NULL UNIQUE CHECK(nice_class BETWEEN 1 AND 45),  -- 尼斯分类号
    class_name           TEXT NOT NULL,      -- 类别名，如「机械设备」
    covered_firms_stated INTEGER NOT NULL,   -- 用户 CSV 给出的覆盖企业数（服务类填 0 是有意口径）
    is_service           INTEGER NOT NULL CHECK(is_service IN (0,1)),       -- 是否服务类
    is_product_node      INTEGER NOT NULL CHECK(is_product_node IN (0,1)),  -- 1=35 产品类可作节点；0=10 服务类不作节点
    class_kind           TEXT NOT NULL CHECK(class_kind IN ('product','service')),
    note                 TEXT                -- 口径说明
) STRICT;

-- 企业—产品类桥接：只保留 35 个产品类，1418 行 / 433 户
CREATE TABLE IF NOT EXISTS bridge_enterprise_product (
    bridge_id       INTEGER PRIMARY KEY,
    enterprise_id   TEXT NOT NULL REFERENCES dim_enterprise(enterprise_id) ON DELETE CASCADE,
    product_id      TEXT NOT NULL REFERENCES dim_product_nice_class(product_id),
    nice_class      INTEGER NOT NULL,     -- 冗余尼斯分类号，便于按类聚合
    n_trademark     INTEGER NOT NULL,     -- 该企业在该产品类的商标条数
    n_registered    INTEGER NOT NULL,     -- 其中「已注册」条数
    first_apply_date TEXT,                -- 该类最早申请日期
    built_at        TEXT,
    UNIQUE(enterprise_id, product_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_qcc_tm_ent ON ods_qcc_trademark(enterprise_id);
CREATE INDEX IF NOT EXISTS idx_qcc_tm_class ON ods_qcc_trademark(nice_class, class_kind);
CREATE INDEX IF NOT EXISTS idx_qcc_tm_reg ON ods_qcc_trademark(is_registered);
CREATE INDEX IF NOT EXISTS idx_bridge_ep_prod ON bridge_enterprise_product(product_id);
CREATE INDEX IF NOT EXISTS idx_bridge_ep_ent ON bridge_enterprise_product(enterprise_id);

-- 产品节点视图：只有 35 行。covered_firms_actual 由桥接表现场重算，
-- 与用户 CSV 的 covered_firms_stated 逐类比对（stated_match=1 表示一致）
CREATE VIEW v_product_node AS
SELECT p.product_id,
       p.product_name,
       p.nice_class,
       p.class_name,
       p.covered_firms_stated,
       COUNT(b.enterprise_id)                    AS covered_firms_actual,
       COALESCE(SUM(b.n_trademark), 0)          AS n_trademark,
       COALESCE(SUM(b.n_registered), 0)         AS n_registered,
       MIN(b.first_apply_date)                  AS first_apply_date,
       CASE WHEN p.covered_firms_stated = COUNT(b.enterprise_id)
            THEN 1 ELSE 0 END                   AS stated_match,
       1                                        AS is_product_node,
       '尼斯核定类别，非 SKU 级产品；并列层，未并入 ads_graph_node 主图' AS node_note
  FROM dim_product_nice_class p
  LEFT JOIN bridge_enterprise_product b ON b.product_id = p.product_id
 WHERE p.is_product_node = 1
 GROUP BY p.product_id, p.product_name, p.nice_class, p.class_name,
          p.covered_firms_stated;

-- 企业层商标汇总（500 行，无商标企业补 0）：一律以贴源明细重算
CREATE VIEW v_enterprise_trademark_summary AS
WITH t AS (
    SELECT enterprise_id,
           COUNT(*)                                              AS n_trademark,
           SUM(is_registered)                                    AS n_registered,
           COUNT(DISTINCT CASE WHEN COALESCE(trademark_name,'') NOT IN ('','-','图形')
                               THEN trademark_name END)          AS n_brand_exact,
           COUNT(DISTINCT CASE WHEN class_kind='product'
                               THEN nice_class END)              AS n_product_class,
           COUNT(DISTINCT CASE WHEN class_kind='service'
                               THEN nice_class END)              AS n_service_class,
           MIN(apply_date)                                       AS first_apply_date
      FROM ods_qcc_trademark
     GROUP BY enterprise_id
), b AS (
    SELECT enterprise_id,
           COUNT(*)              AS n_product_link,
           SUM(n_trademark)      AS n_product_trademark
      FROM bridge_enterprise_product
     GROUP BY enterprise_id
)
SELECT d.enterprise_id,
       d.name_display,
       COALESCE(t.n_trademark, 0)                       AS n_trademark,
       COALESCE(t.n_registered, 0)                     AS n_registered,
       COALESCE(t.n_brand_exact, 0)                    AS n_brand_exact,
       COALESCE(t.n_product_class, 0)                  AS n_product_class,
       COALESCE(t.n_service_class, 0)                  AS n_service_class,
       COALESCE(b.n_product_link, 0)                   AS n_product_link,
       COALESCE(b.n_product_trademark, 0)              AS n_product_trademark,
       t.first_apply_date,
       SUBSTR(t.first_apply_date, 1, 4)                AS first_apply_year,
       CASE WHEN COALESCE(t.n_trademark, 0) > 0 THEN 1 ELSE 0 END AS has_trademark,
       'n_brand_exact 为明细精确去重口径（排除 - / 图形），' ||
       '与用户主表「商标_品牌数」存在 68 家差异，禁止混用'  AS brand_note
  FROM dim_enterprise d
  LEFT JOIN t ON t.enterprise_id = d.enterprise_id
  LEFT JOIN b ON b.enterprise_id = d.enterprise_id;

-- 六类实体覆盖（6 行）：把用户 xlsx「六类实体覆盖」的两处错误口径现场纠正
--   人员：来源明细清洗后 2079 人（不是含表头脏行的 2569），且它不是图节点（图内人员=发明人 inventor）
--   产品：35 个类别节点 / 433 户 / 86.6%，未并入主图、SKU 级仍缺
CREATE VIEW v_six_entity_coverage AS
WITH g AS (
    SELECT node_type, COUNT(*) AS n_node FROM ads_graph_node GROUP BY node_type
), src AS (
    SELECT (SELECT COUNT(*) FROM ods_qcc_person)                       AS n_person_source,
           (SELECT COUNT(DISTINCT enterprise_id) FROM ods_qcc_person) AS n_person_firms,
           (SELECT COUNT(*) FROM dim_product_nice_class
             WHERE is_product_node = 1)                               AS n_product_node,
           (SELECT COUNT(DISTINCT enterprise_id)
              FROM bridge_enterprise_product)                         AS n_product_firms
)
SELECT '企业' AS entity_type, 'enterprise' AS node_type,
       (SELECT n_node FROM g WHERE node_type='enterprise') AS n_in_graph,
       500 AS n_source_rows, 500 AS n_covered_firms, 1.0 AS coverage_rate,
       '完整入图' AS coverage_state,
       '500 家样本' AS note
UNION ALL
SELECT '专利', 'patent', (SELECT n_node FROM g WHERE node_type='patent'),
       32969, 500, 1.0, '完整入图', '三源并集，公开号去重'
UNION ALL
SELECT '投资机构', 'investor', (SELECT n_node FROM g WHERE node_type='investor'),
       572, 182, 0.364, '完整入图', '182 户有融资事件，572 家机构'
UNION ALL
SELECT '人员', 'inventor', (SELECT n_node FROM g WHERE node_type='inventor'),
       (SELECT n_person_source FROM src), (SELECT n_person_firms FROM src), 0.98,
       '完整入图（图内=发明人）',
       '⚠️ 企查查主要人员清洗后 2079 人 / 490 户，是并列层来源明细而非图节点；' ||
       '用户 xlsx 写 2569 含 490 行表头脏行，已纠正'
UNION ALL
SELECT '领域', 'ipc', (SELECT n_node FROM g WHERE node_type='ipc'),
       11588, 500, 1.0, '完整入图', 'IPC 小类承担技术域语义'
UNION ALL
SELECT '产品', '（未入主图）', 0,
       (SELECT n_product_node FROM src), (SELECT n_product_firms FROM src), 0.866,
       '类别粒度部分闭合',
       '尼斯 35 个产品类 / 433 户；10 个服务类不作节点；' ||
       '⚠️ 产品节点在并列层，未插入 ads_graph_node，SKU 级产品仍缺，' ||
       '禁止宣称立项六类已完整覆盖';

-- 产品类覆盖与已发布五档交叉（并列分析，不改任何标签/分档）
CREATE VIEW v_product_grade_cross AS
SELECT g.grade_multilabel,
       COUNT(*)                                                   AS n_enterprise,
       SUM(s.has_trademark)                                       AS n_with_trademark,
       SUM(CASE WHEN s.n_product_class > 0 THEN 1 ELSE 0 END)     AS n_with_product_node,
       ROUND(AVG(s.n_product_class), 2)                           AS avg_product_class,
       SUM(CASE WHEN s.n_product_class >= 3 THEN 1 ELSE 0 END)    AS n_multi_class,
       '并列观察量，未进入标签定义与分档规则'                       AS cross_note
  FROM v_enterprise_multilabel_grade g
  JOIN v_enterprise_trademark_summary s USING (enterprise_id)
 GROUP BY g.grade_multilabel;

-- [V21PRODUCT:END]

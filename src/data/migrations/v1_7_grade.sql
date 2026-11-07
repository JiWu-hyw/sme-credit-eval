-- ============================================================================
-- v1.7 企业「技术与资质梯队分档」L0 规则基线
-- 依据：docs/论文/企业分档多标签体系方案-v1.0.md §4（判据）/ §6（P0）
--
-- 定位：可审计、可复现的对照基线，不是「企业信用好坏」结论。
-- 判据用字典序分层（硬约束 → 有序主序 → 兜底），无主观权重、可单测：
--   E 预警  sd>0 / ba>0 / 非开业        （A 轴结果型硬标签，一票定档）
--   D 观察  rn>0 / lab=1                （风险负面或资质断档信号）
--   A 引领  lg=1 / tech>=5
--   B 优质  tech=4 且 cred>=11
--   C 稳健  其余
-- 优先级：E > D > A > B > C。lab=-1 表示未打标的 193 家，不参与 D 档判定，
--         它们不是负例。admin_penalty_cnt 语义与字段名不符，明确排除在判据外。
--
-- ⚠️ 两条使用边界（写进 scope_note 列，防止下游误用）：
--   1. 本视图基于 dim_enterprise 静态快照，不是 as-of 特征切面；
--      任何建模实验只用 v_model_dataset_asof_flat 并固定单一 as_of_policy。
--   2. A/B 档判据用了 qcc_tech_level_ord / qcc_credit_level_ord，
--      禁止再用这两个字段验证本分档（循环定义）。
--      外部效度只能用 A 轴结果型标签。
--
-- 实测家数（v1.6 库，只读实跑）：A 44 / B 95 / C 275 / D 83 / E 3，合计 500。
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

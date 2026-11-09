-- ============================================================================
-- v1.9 标签方向纠正 + 多标签测评体系（P1，零采集）
--
-- 触发问题（2026-11-09）：
--   ① P1 应当是「添加更多的标签来测评」，不是扩样本 / 外部采集；
--   ② 旧文档把 ads_label.label_value=1（21 家「到期未再出现」）称为「正例」，
--      方向反了。本项目研究企业好坏 / 信用评价：
--        正例（好企业）= 资质正常存续、续期成功 = label_value 0 的 286 家
--        负例（坏企业）= 资格到期后断档      = label_value 1 的 21 家
--      label_value 原本是「风险预警方向」(1=坏)，这个数值是已发布 v1.4.0
--      快照与 12 个契约、历史 AUC 审计的绑定口径，**禁止原地 UPDATE**。
--
-- 定位：只增视图，不改任何事实表 / 不改 ads_label / 不改 v_model_train_ready /
--       不改 v_enterprise_grade_l0 / 不改 ASOF_VER / 不改 29 列主宽表结构。
--       方向纠正以「并列的信用方向视图」表达，旧视图保持向后兼容。
--
-- 六个视图：
--   ① v_label_direction              307 行  风险方向 ↔ 信用方向逐企业对照
--   ② v_model_train_ready_credit    307 行  label 已翻转的正确方向训练入口
--   ③ v_label_mechanism_credit        5 行  n_events × cohort 的正确方向台账
--   ④ v_enterprise_label_matrix     500 行  四轴 6 个二值锚点 + 3 个有序锚点
--   ⑤ v_enterprise_multilabel_grade 500 行  多标签五档 + L0 对照
--   ⑥ v_multilabel_eval_summary      长表    交叉测评：档次 → 各锚点正例率
--
-- 方向纪律：本组所有 y_* 二值标签一律 **1 = 好 / 正例，0 = 坏 / 负例，NULL = 不可判定**。
--           NULL 不计入票数；禁止把 NULL 当 0（否则 193 家未打标会被算成坏企业）。
-- 独立性纪律：A（硬结果）与 D（第三方等级）相对独立；
--            B（资质阶梯）与 C（资质存续）同源于公示名单，**不构成独立印证**（方案 §1）。
-- ============================================================================

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

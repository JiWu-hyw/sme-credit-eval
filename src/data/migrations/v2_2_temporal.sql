-- [V22TEMPORAL:BEGIN]
-- v2.2 时序图并列层（2026-11-12 升版）
-- 把静态单快照 ads_graph_node / ads_graph_edge 升级为 29 个年度时间切片的时序图。
-- 两张表由图管道（build_graph_v2 + 时序 ETL）物化；本迁移为「登记式」：
-- 只校验存在性与一致性、登记 schema 版本、同步 schema.sql。数据填充归外部时序 ETL 所有。
-- 关键口径：边时序化率 94.9%（除 tech_similar 标 global_snapshot 全期快照外均按时点年份）；
-- 节点时序化率 100%；时间跨度 1992–2026（29 年）；year_source 可溯源。
CREATE TABLE ads_graph_node_ts (
    node_type    text,
    node_id      text,
    time_slice   integer,
    display_name text,
    year_source  text
);

CREATE TABLE ads_graph_edge_ts (
    src_type     text,
    src_id       text,
    dst_type     text,
    dst_id       text,
    edge_type    text,
    time_slice   integer,
    weight       real,
    year_source  text
);
-- [V22TEMPORAL:END]

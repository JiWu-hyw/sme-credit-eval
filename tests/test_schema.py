# -*- coding: utf-8 -*-
"""标准数据库约束与映射测试。

验证重点：STRICT 类型校验、CHECK 枚举校验、外键校验、唯一约束，
以及 15 个指标 → 字段 映射是否全部可解析。

用法：.venv/Scripts/python.exe tests/test_schema.py
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.data import db as D  # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print("  ✓ %s" % name)
    except AssertionError as e:
        FAIL.append((name, str(e)))
        print("  ✗ %s —— %s" % (name, e))
    except Exception as e:
        FAIL.append((name, "%s: %s" % (type(e).__name__, e)))
        print("  ✗ %s —— %s: %s" % (name, type(e).__name__, e))


def expect_error(sql, params, keyword=None, conn=None):
    """断言该 SQL 必然报错（keyword 限定错误类型）。"""
    def _inner():
        try:
            conn.execute(sql, params)
        except sqlite3.Error as e:
            msg = str(e)
            assert keyword is None or keyword in msg, \
                "错误类型不符：期望含 %r，实际 %r" % (keyword, msg)
            return
        raise AssertionError("本应报错，却执行成功：%s" % sql[:70])
    return _inner


def main():
    if not os.path.exists(D.DB_PATH):
        print("数据库不存在，先建库……")
        D.init_db()

    print("=" * 66)
    print("标准数据库测试：%s" % D.DB_PATH)
    print("=" * 66)

    # ---------- 1. 结构完整性 ----------
    print("\n[1] 结构完整性")
    rep = D.verify_schema(D.DB_PATH, verbose=False)
    check("27 张表全部存在", lambda: _assert(not [e for e in rep["errors"] if e.startswith("缺表")],
                                             rep["errors"]))
    check("5 个视图全部存在", lambda: _assert(not [e for e in rep["errors"] if e.startswith("缺视图")],
                                             rep["errors"]))
    check("17 项指标映射全部落位",
          lambda: _assert(all(v["ok"] for v in rep["indicators"].values()),
                          [k for k, v in rep["indicators"].items() if not v["ok"]]))
    check("表数量 == 27", lambda: _assert(len(rep["tables"]) == 27, len(rep["tables"])))
    check("视图数量 == 5", lambda: _assert(len(rep["views"]) == 5, len(rep["views"])))

    conn = D.connect(D.DB_PATH)
    conn.execute("BEGIN")
    try:
        # ---------- 2. 字典种子数据 ----------
        print("\n[2] 字典种子数据")
        n_qt = conn.execute("SELECT COUNT(*) FROM dim_qual_type").fetchone()[0]
        n_src = conn.execute("SELECT COUNT(*) FROM meta_data_source").fetchone()[0]
        n_ipc = conn.execute("SELECT COUNT(*) FROM dim_ipc").fetchone()[0]
        check("资质类型种子 = 4", lambda: _assert(n_qt == 4, n_qt))
        check("数据源登记 >= 10", lambda: _assert(n_src >= 10, n_src))
        check("IPC 部类种子 = 8", lambda: _assert(n_ipc == 8, n_ipc))
        check("所有数据源成本为 0（免费路线）",
              lambda: _assert(conn.execute(
                  "SELECT COUNT(*) FROM meta_data_source WHERE cost_cny > 0").fetchone()[0] == 0,
                  "存在付费源"))

        # ---------- 3. STRICT 类型校验 ----------
        print("\n[3] STRICT 类型校验（弱类型是数据质量第一杀手）")
        ent = D.make_enterprise_id(D.norm_company_name("杭州示例智能科技有限公司"))
        conn.execute(
            "INSERT INTO dim_enterprise(enterprise_id, name_display, name_norm, region_code,"
            " enterprise_type, created_at) VALUES (?,?,?,?,?,?)",
            (ent, "杭州示例智能科技有限公司", "示例智能科技", "330100", "high_tech", D.now_iso()))
        check("插入企业成功", lambda: _assert(
            conn.execute("SELECT COUNT(*) FROM dim_enterprise WHERE enterprise_id=?",
                         (ent,)).fetchone()[0] == 1, "未插入"))

        check("STRICT：TEXT 写入 INTEGER 列被拒",
              expect_error("UPDATE dim_enterprise SET first_qual_year=? WHERE enterprise_id=?",
                           ("二〇二一年", ent), "cannot store TEXT value", conn))

        check("CHECK：非法 enterprise_type 被拒",
              expect_error("UPDATE dim_enterprise SET enterprise_type=? WHERE enterprise_id=?",
                           ("bogus_type", ent), "CHECK constraint", conn))

        check("UNIQUE：重复 name_norm 被拒",
              expect_error(
                  "INSERT INTO dim_enterprise(enterprise_id, name_display, name_norm)"
                  " VALUES (?,?,?)",
                  ("ENTDUPLICATE0001", "另一家公司", "示例智能科技"), "UNIQUE", conn))

        check("NOT NULL：name_norm 为空被拒",
              expect_error(
                  "INSERT INTO dim_enterprise(enterprise_id, name_display, name_norm)"
                  " VALUES (?,?,?)",
                  ("ENTNULLTEST0001", "无名字", None), "NOT NULL", conn))

        # ---------- 4. 外键校验 ----------
        print("\n[4] 外键校验（PRAGMA foreign_keys=ON 是否真生效）")
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        check("外键开关已开启", lambda: _assert(fk_on == 1, fk_on))
        check("FK：资质事实指向不存在企业被拒",
              expect_error(
                  "INSERT INTO fact_qualification(enterprise_id, qual_type, batch_no, year)"
                  " VALUES (?,?,?,?)",
                  ("ENTNOTEXIST9999", "high_tech", "2021", 2021), "FOREIGN KEY", conn))

        # ---------- 5. 正常业务链路（企业→资质→专利→特征→标签→宽表） ----------
        print("\n[5] 业务链路完整性")
        conn.execute(
            "INSERT INTO ods_qual_notice(qual_type, batch_no, publish_date, year, region_name,"
            " source_id, fetched_at) VALUES (?,?,?,?,?,?,?)",
            ("high_tech", "2021", "2021-12-20", 2021, "杭州市", "zj_high_tech", D.now_iso()))
        notice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO ods_qual_company_raw(notice_id, seq_no, company_name, region_raw,"
            " ingested_at) VALUES (?,?,?,?,?)",
            (notice_id, 1, "杭州示例智能科技有限公司", "杭州市滨江区", D.now_iso()))
        raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO fact_qualification(enterprise_id, qual_type, batch_no, year,"
            " publish_date, status, is_renewal, valid_until, notice_id, raw_id, match_score)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ent, "high_tech", "2021", 2021, "2021-12-20", "approved", 0,
             "2024-12-31", notice_id, raw_id, 1.0))
        check("资质事实写入成功", lambda: _assert(
            conn.execute("SELECT COUNT(*) FROM fact_qualification WHERE enterprise_id=?",
                         (ent,)).fetchone()[0] == 1, "未写入"))

        check("UNIQUE：同企业同资质同批次重复被拒",
              expect_error(
                  "INSERT INTO fact_qualification(enterprise_id, qual_type, batch_no, year)"
                  " VALUES (?,?,?,?)", (ent, "high_tech", "2021", 2021), "UNIQUE", conn))

        # 晋级事件
        conn.execute(
            "INSERT INTO fact_qualification_event(enterprise_id, event_date, event_year,"
            " event_type, from_qual, to_qual, rank_delta, note) VALUES (?,?,?,?,?,?,?,?)",
            (ent, "2021-12-20", 2021, "first_grant", None, "high_tech", 2, "首次获高新认定"))
        check("晋级事件写入成功", lambda: _assert(
            conn.execute("SELECT COUNT(*) FROM fact_qualification_event WHERE enterprise_id=?",
                         (ent,)).fetchone()[0] == 1, "未写入"))
        check("CHECK：非法 event_type 被拒",
              expect_error(
                  "INSERT INTO fact_qualification_event(enterprise_id, event_date, event_year,"
                  " event_type) VALUES (?,?,?,?)",
                  (ent, "2022-01-01", 2022, "whatever"), "CHECK constraint", conn))

        # 专利 + IPC + 发明人
        conn.execute(
            "INSERT INTO fact_patent(patent_id, app_no, title, app_date, app_year, patent_type,"
            " legal_status, is_granted, enterprise_id, match_confidence, match_method,"
            " n_claims, n_cite_fwd, ipc_main, source_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("CN110000001A", "201910000001.1", "一种基于图神经网络的信用评估方法",
             "2019-01-01", 2019, "invention", "granted", 1, ent, 1.0, "exact",
             12, 5, "G06Q40/00", "google_patents"))
        check("专利写入成功", lambda: _assert(
            conn.execute("SELECT COUNT(*) FROM fact_patent WHERE enterprise_id=?",
                         (ent,)).fetchone()[0] == 1, "未写入"))

        check("CHECK：非法 patent_type 被拒",
              expect_error(
                  "INSERT INTO fact_patent(patent_id, app_year, patent_type, is_granted)"
                  " VALUES (?,?,?,?)", ("CN110000002A", 2020, "trademark", 0),
                  "CHECK constraint", conn))
        check("CHECK：match_confidence 越界被拒",
              expect_error(
                  "INSERT INTO fact_patent(patent_id, app_year, patent_type, is_granted,"
                  " match_confidence) VALUES (?,?,?,?,?)",
                  ("CN110000003A", 2020, "invention", 0, 1.5), "CHECK constraint", conn))

        conn.execute("INSERT INTO bridge_patent_ipc(patent_id, ipc_code, ipc_subclass, is_main)"
                     " VALUES (?,?,?,?)", ("CN110000001A", "G06Q40/00", "G06Q", 1))
        inv = D.make_inventor_id("张三", "hash330100")
        conn.execute("INSERT INTO dim_inventor(inventor_id, name_display, name_norm, addr_hash,"
                     " first_year, last_year) VALUES (?,?,?,?,?,?)",
                     (inv, "张三", "张三", "hash330100", 2019, 2021))
        conn.execute("INSERT INTO bridge_patent_inventor(patent_id, inventor_id, seq, is_first)"
                     " VALUES (?,?,?,?)", ("CN110000001A", inv, 1, 1))
        check("桥表（IPC/发明人）写入成功", lambda: _assert(
            conn.execute("SELECT COUNT(*) FROM bridge_patent_ipc").fetchone()[0] == 1
            and conn.execute("SELECT COUNT(*) FROM bridge_patent_inventor").fetchone()[0] == 1,
            "桥表为空"))

        # 特征 + 标签 + 宽表
        conn.execute(
            "INSERT INTO dws_tech_feature(enterprise_id, stat_year, inv_patent_ratio, grant_rate,"
            " patent_citation, tech_breadth_ipc_class, tech_breadth_entropy, rd_intensity,"
            " rd_intensity_flag, n_patent_app, n_patent_grant, n_patent_invention,"
            " n_inventor, n_ipc_subclass, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ent, 2021, 1.0, 1.0, 5.0, 1, 0.0, 0.35, "proxy",
             1, 1, 1, 1, 1, D.now_iso()))
        check("CHECK：rd_intensity_flag 非法值被拒",
              expect_error(
                  "UPDATE dws_tech_feature SET rd_intensity_flag=? WHERE enterprise_id=?",
                  ("guessed", ent), "CHECK constraint", conn))

        conn.execute(
            "INSERT INTO dws_growth_feature(enterprise_id, stat_year, patent_cagr, m_cert,"
            " qual_rank, qual_rank_delta, qual_upgrade_cum, computed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ent, 2021, 0.0, 1, 2, 2, 1, D.now_iso()))
        conn.execute(
            "INSERT INTO ads_label(enterprise_id, label_task, cohort_year, label_value,"
            " label_name, observation_start, observation_end, definition_ver, n_events)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (ent, "qual_survival", 2021, 0, "科技资质存续失败（0=存续）",
             "2019-01-01", "2024-12-31", "label_v1.0", 1))
        check("CHECK：非法 label_value 被拒",
              expect_error(
                  "INSERT INTO ads_label(enterprise_id, label_task, cohort_year, label_value)"
                  " VALUES (?,?,?,?)", (ent, "qual_survival", 2019, 2), "CHECK constraint", conn))
        check("CHECK：非法 label_task 被拒",
              expect_error(
                  "INSERT INTO ads_label(enterprise_id, label_task, cohort_year, label_value)"
                  " VALUES (?,?,?,?)", (ent, "mystery_task", 2019, 1), "CHECK constraint", conn))

        conn.execute(
            "INSERT INTO ads_model_dataset(enterprise_id, dataset_ver, label_task, cohort_year,"
            " name_display, label, inv_patent_ratio, rd_intensity, split, split_protocol,"
            " built_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ent, "ds_v1.0", "qual_survival", 2021, "杭州示例智能科技有限公司", 0,
             1.0, 0.35, "train", "按 enterprise_id 分组 GroupShuffleSplit 8:2（test_size=0.2, seed=42）",
             D.now_iso()))
        check("CHECK：非法 split 被拒",
              expect_error(
                  "UPDATE ads_model_dataset SET split=? WHERE enterprise_id=?",
                  ("holdout", ent), "CHECK constraint", conn))

        # 图节点/边
        conn.execute("INSERT INTO ads_graph_node(node_type, node_id, time_slice, display_name)"
                     " VALUES (?,?,?,?)", ("enterprise", ent, 2021, "杭州示例智能科技有限公司"))
        conn.execute("INSERT INTO ads_graph_node(node_type, node_id, time_slice, display_name)"
                     " VALUES (?,?,?,?)", ("patent", "CN110000001A", 2021, "一种基于图神经网络的信用评估方法"))
        conn.execute("INSERT INTO ads_graph_edge(src_type, src_id, dst_type, dst_id, edge_type,"
                     " time_slice, weight) VALUES (?,?,?,?,?,?,?)",
                     ("enterprise", ent, "patent", "CN110000001A", "files_patent", 2021, 1.0))
        check("CHECK：非法 edge_type 被拒",
              expect_error(
                  "INSERT INTO ads_graph_edge(src_type, src_id, dst_type, dst_id, edge_type,"
                  " time_slice) VALUES (?,?,?,?,?,?)",
                  ("enterprise", ent, "patent", "CN110000001A", "teleports", 2021),
                  "CHECK constraint", conn))

        # ---------- 6. 视图可用性 ----------
        print("\n[6] 视图可用性（派生口径固化）")
        for v in ["v_enterprise_profile", "v_patent_yearly", "v_qual_history",
                  "v_model_dataset_flat", "v_data_coverage"]:
            check("视图可查：%s" % v,
                  (lambda vv: (lambda: conn.execute("SELECT * FROM %s LIMIT 1" % vv).fetchall()))(v))

        n_flat = conn.execute(
            "SELECT COUNT(*) FROM v_model_dataset_flat WHERE enterprise_id=?", (ent,)).fetchone()[0]
        check("宽表视图能关联到标签", lambda: _assert(n_flat == 1, n_flat))
        n_cov = conn.execute(
            "SELECT n FROM v_data_coverage WHERE obj='enterprise'").fetchone()[0]
        check("覆盖度视图统计正确", lambda: _assert(n_cov == 1, n_cov))

        # ---------- 7. 匹配审计 ----------
        print("\n[7] 名称匹配审计（论文写匹配准确率的依据）")
        conn.execute(
            "INSERT INTO log_match(stage, raw_name, candidate_ent, method, score, decision,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            ("patent_assignee", "杭州示例智能科技", ent, "fuzzy", 0.93, "accepted", D.now_iso()))
        row = conn.execute(
            "SELECT method, decision, score FROM log_match WHERE candidate_ent=?", (ent,)).fetchone()
        check("匹配日志可写入并回读", lambda: _assert(row is not None and row["score"] == 0.93,
                                                     dict(row) if row else None))
        check("CHECK：非法 decision 被拒",
              expect_error(
                  "INSERT INTO log_match(stage, raw_name, method, decision)"
                  " VALUES (?,?,?,?)",
                  ("patent_assignee", "某公司", "fuzzy", "maybe"), "CHECK constraint", conn))

    finally:
        conn.execute("ROLLBACK")   # 测试数据一律不留库
        conn.close()

    print("\n回滚完成 —— 库内无测试残留：", end=" ")
    c2 = D.connect(D.DB_PATH)
    left = c2.execute("SELECT COUNT(*) FROM dim_enterprise").fetchone()[0]
    c2.close()
    print("%d 家企业" % left)

    # ---------- 汇总 ----------
    print("\n" + "=" * 66)
    print("通过 %d 项" % len(PASS), end="")
    if FAIL:
        print("，失败 %d 项：" % len(FAIL))
        for n, e in FAIL:
            print("  ✗ %s —— %s" % (n, e))
        print("=" * 66)
        return 1
    print("，失败 0 项")
    print("✅ SCHEMA TESTS PASSED")
    print("=" * 66)
    return 0


def _assert(cond, detail=None):
    assert cond, "实际值：%r" % (detail,)


if __name__ == "__main__":
    sys.exit(main())

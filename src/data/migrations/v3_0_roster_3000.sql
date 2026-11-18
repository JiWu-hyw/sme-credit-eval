-- [V30ROSTER:BEGIN]
-- v3.0 名单扩样 500→3000：3000 家主名单 + 企查查画像入 ODS 层
-- 纪律：只增不改——不动 dim_enterprise / ads_label / 主图 / 已发布视图；
--       新增 2500 家企业以新 enterprise_id 入 dim_enterprise（is_sample=0 为扩样层，
--       原 500 家 is_sample=1），资质存续标签批次起算口径见 meta 登记说明。

-- 表注释：3000 家专精特新主名单（原500家+新增2500家），名单5列+批次+来源分组，与 _inbox 名单 xlsx 同源
CREATE TABLE ods_roster_3000 (
    roster_id      INTEGER PRIMARY KEY,   -- 名单序号（原500家 1-500 / 新增2500家 1-2500）
    company_name   TEXT NOT NULL,         -- 企业名称（名单原文，工商全称）
    batch          TEXT NOT NULL,         -- 认定批次：2023年度第一批/2024年度第一批/2024年度第二批/2025年度第一批
    roster_group   TEXT NOT NULL,         -- 来源分组：原500家/新增2500家
    city           TEXT,                  -- 地市（名单口径；438家待补在名单侧补齐后以本表为准）
    city_judge     TEXT,                  -- 地市判定：官方公示文件/按企业名称县区推断/待补/原抽样表
    data_source    TEXT                   -- 数据来源（官方文号）
) STRICT;

-- 表注释：3000 家企查查画像主表（91列中取信用评价核心约40列），行=3000，与企业一一对应
CREATE TABLE ods_qcc_profile_3000 (
    company_name        TEXT PRIMARY KEY,   -- 企业名称（名单原文）
    credit_code         TEXT,               -- 统一社会信用代码（3000/3000 全覆盖）
    cur_name            TEXT,               -- 现用名称（企查查口径）
    reg_status          TEXT,               -- 登记状态
    est_date            TEXT,               -- 成立日期
    legal_rep           TEXT,               -- 法定代表人
    reg_capital         TEXT,               -- 注册资本（原始字符串，万元）
    paid_capital        TEXT,               -- 实缴资本
    insure_num          TEXT,               -- 参保人数
    ent_type            TEXT,               -- 企业类型
    district            TEXT,               -- 所属区县
    city_qcc            TEXT,               -- 所属城市（企查查口径）
    ent_scale           TEXT,               -- 企业规模
    qcc_score           TEXT,               -- 企查分
    credit_level        TEXT,               -- 信用等级
    kechuang_score      TEXT,               -- 科创分
    gb_industry_l1      TEXT,               -- 国标行业大类
    gb_industry_l2      TEXT,               -- 国标行业小类
    website             TEXT,               -- 官网
    is_micro            TEXT,               -- 是否小微
    ann_rev_latest      TEXT,               -- 最新年报营业收入
    is_giant            TEXT,               -- 是否小巨人
    is_hn_tech          TEXT,               -- 是否高企
    is_sme_tech         TEXT,               -- 是否科技型中小
    is_zjtx             TEXT,               -- 是否专精特新
    hn_year             TEXT,               -- 高企认定年份
    hn_valid_to         TEXT,               -- 高企有效期至
    cert_cnt            TEXT,               -- 资质证书_条数
    cert_valid_cnt      TEXT,               -- 资质证书_有效数
    sys_cert_cnt        TEXT,               -- 体系认证数
    prod_cert_cnt       TEXT,               -- 产品认证数
    patent_cnt          TEXT,               -- 专利数
    inv_patent_cnt      TEXT,               -- 发明专利数
    um_patent_cnt       TEXT,               -- 实用新型数
    dp_patent_cnt       TEXT,               -- 外观设计数
    tm_cnt              TEXT,               -- 商标件数
    tm_valid_cnt        TEXT,               -- 商标有效数
    brand_cnt           TEXT,               -- 品牌数
    shareholder_cnt     TEXT,               -- 股东数
    exec_cnt            TEXT,               -- 高管人数
    judge_dishonest_cnt TEXT,               -- 司法_失信条数
    judge_exec_cnt      TEXT,               -- 司法_被执行条数
    judge_end_exec_cnt  TEXT,               -- 司法_终本条数
    judge_amt_sum       TEXT,               -- 司法_金额合计万元
    judge_default_flag  TEXT,               -- 司法_有违约
    fund_event_cnt      TEXT,               -- 融资_笔数
    fund_round_cnt      TEXT,               -- 融资_轮次数
    fund_inst_cnt       TEXT,               -- 融资_机构数
    fund_first_year     TEXT,               -- 融资_首次年
    fund_last_year      TEXT,               -- 融资_末次年
    kechuang_grade      TEXT,               -- 科创等级
    former_name         TEXT                -- 曾用名
) STRICT;

-- 表注释：3000 家专利明细（140,796 条 / 2972 家 / 1992–2026），申请号去重 140,734
CREATE TABLE ods_patent_3000 (
    pid           INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,   -- 归属企业（名单原文）
    title         TEXT,            -- 发明名称
    patent_type   TEXT,            -- 专利类型：发明/实用新型/外观设计
    legal_status  TEXT,            -- 法律状态
    app_no        TEXT,            -- 申请号
    app_date      TEXT,            -- 申请日
    pub_no        TEXT,            -- 公开号
    pub_date      TEXT,            -- 公开日
    inventors     TEXT,            -- 发明人（;分隔）
    applicants    TEXT             -- 申请人（;分隔，含共同申请）
) STRICT;

-- 表注释：3000 家资质证书明细（53,395 条），体系/产品/软件/贯标认证贴源
CREATE TABLE ods_cert_3000 (
    cid           INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,
    cert_name     TEXT,            -- 资质名称（如 质量管理体系认证）
    cert_detail   TEXT,            -- 资质类别及等级/产品名称/认证项目
    cert_no       TEXT,            -- 证书编号
    issuer        TEXT,            -- 发证机构
    issue_date    TEXT,            -- 发证日期
    valid_to      TEXT,            -- 有效期至
    cert_status   TEXT             -- 证书状态：有效/失效/…
) STRICT;

-- 表注释：3000 家商标明细（49,220 条 / 尼斯国际分类 1–45）
CREATE TABLE ods_tm_3000 (
    tid           INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,
    tm_name       TEXT,            -- 商标名称
    nice_class    TEXT,            -- 国际分类（尼斯 1–45）
    tm_status     TEXT,            -- 商标状态（原文）
    reg_no        TEXT,            -- 申请/注册号
    app_date      TEXT,            -- 申请日期
    announce_date TEXT             -- 注册公告日期
) STRICT;

-- 表注释：3000 家股东明细（13,050 行），出资人/股东贴源
CREATE TABLE ods_shareholder_3000 (
    sid           INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,
    holder_name   TEXT,            -- 股东名称
    holder_type   TEXT,            -- 股东类型
    share_type    TEXT,            -- 股份类型
    shares        TEXT,            -- 持股数（股）
    subscribed    TEXT,            -- 认缴出资额
    sub_date      TEXT,            -- 认缴出资日期
    indirect_pct  TEXT,            -- 间接持股比例
    first_date    TEXT,            -- 首次持股日期
    related       TEXT             -- 关联产品/机构
) STRICT;

-- 表注释：3000 家高管明细（14,809 行），主要人员贴源（并列层，非图节点）
CREATE TABLE ods_person_3000 (
    person_id     INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,
    person_name   TEXT,            -- 姓名
    position      TEXT             -- 职务
) STRICT;

-- 表注释：3000 家荣誉明细（17,784 行），荣誉/奖项贴源
CREATE TABLE ods_honor_3000 (
    hid           INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,
    honor_name    TEXT,            -- 名称
    honor_no      TEXT,            -- 编号
    honor_type    TEXT,            -- 荣誉类型
    level         TEXT,            -- 级别
    issuer        TEXT,            -- 发布单位
    cert_year     TEXT,            -- 认证年份
    pub_date      TEXT,            -- 发布日期
    valid_from    TEXT,            -- 有效期自
    valid_to      TEXT,            -- 有效期至
    src           TEXT             -- 来源
) STRICT;

-- 表注释：3000 家融资明细（1,476 笔），成长轨迹主特征源
CREATE TABLE ods_funding_3000 (
    fid           INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,
    fund_date     TEXT,            -- 融资日期
    round         TEXT,            -- 融资轮次
    amount        TEXT,            -- 融资金额（原始字符串）
    valuation     TEXT,            -- 企业估值
    investors     TEXT             -- 投资方（;分隔）
) STRICT;

-- 表注释：3000 家司法明细（255 条），失信/被执行/终本贴源（三类金额语义不同禁止相加）
CREATE TABLE ods_judicial_3000 (
    jid           INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,   -- 类别：失信/被执行/终本
    company_name  TEXT NOT NULL,
    case_no       TEXT,            -- 案号
    court         TEXT,            -- 执行法院
    amount        TEXT             -- 金额（原始字符串；kind 语义见 kind 列）
) STRICT;
-- [V30ROSTER:END]

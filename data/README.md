# data/ 目录说明

**本目录下的数据文件不入库**（`.gitignore` 已排除），仓库里只有这份说明和建库 DDL。

## 目录约定

| 路径 | 内容 | 是否入库 | 来源 |
|---|---|---|---|
| `db/zjtech_credit.db` | 数仓库，33 表 + 9 视图 + 698 列，约 104 MB | ❌ | 本项目 ETL 产出 |
| `db/*.bak` | 历次改库前的快照 | ❌ | 本地备份，仅用于回滚 |
| `raw/` | 采集原始文件（专利、资质、企业年报） | ❌ | CNIPA / 企查查 / 浙江省公示 |
| `inbound/` | 外部交付的批次包 | ❌ | 数据提供方 |
| `processed/` | 清洗后的中间表 | ❌ | `src/data/` 产出 |
| `derived/` | 派生特征 | ❌ | `src/data/` 产出 |

## 只有 DDL 在仓库里

`src/data/schema.sql` 是建库脚本（59 KB），可以据此建出**空库**：

```bash
sqlite3 /tmp/blank.db < src/data/schema.sql
sqlite3 /tmp/blank.db ".tables"
```

表结构、约束、视图定义都在里面，只是没有数据行。

## 怎么拿到完整库

数据含 500 家真实企业的工商与专利明细，不通过 Git 分发。获取途径：

1. 向项目维护者索取（内部共享盘）；
2. 按 `README.md` 第六节说明，用 `schema.sql` + `data/raw/` 自行跑一遍 ETL。

## 拿到库之后

```bash
python -m src.data.export_data_dictionary   # 生成 698 列字典
python tests/test_data_dictionary.py        # 确认字典与库同步
python tests/test_api_live.py               # 33 个接口探活
```

字段含义、空值原因（`null_by_design` / `source_missing` / `partial_insufficient`）
见 `configs/field_gap_registry.json`。

# 费用分类汇总系统

银行流水费用分类汇总工具：20 类费用自动分类 + 收支分离。

## 功能

- **自动分类**：根据关键词/规则自动将银行流水分类到 20 个费用类别
- **收支分离**：自动区分收入与支出
- **项目归集**：支持按项目/部门归集费用
- **汇总报表**：生成分类汇总报表与明细表
- **GUI 界面**：图形化操作，无需命令行

## 快速开始

```bash
# 安装依赖
pip install pandas openpyxl xlrd numpy

# 复制示例配置（可选：缺失时引擎使用内置默认值，开箱即用）
cp config.example.py config.py
# 编辑 config.py，配置项目名、表头行、文件识别关键词等数据源参数

# 运行 GUI
python expense_classification_gui.py
```

## 配置说明

`config.py` 为可选项：复制 `config.example.py` 修改即可，引擎缺失该文件时自动回退内置默认值。
可配置内容（数据源参数）：

```python
# 银行科目与预期项目（项目完整性校验）
BANK_ACCOUNT_CONFIG = {
    'target_account': '100201',
    'expected_projects': ['项目A', '项目B', '公司总部', '项目D'],
}

# 表头行（0 起算）与文件识别关键词（GUI/CLI 共用）
FILE_FORMAT_CONFIG = {
    'voucher_header_row': 1,        # 凭证列表表头在第 2 行
    'bank_header_row': 10,          # 银行明细账表头在第 11 行
    'voucher_keywords': ['凭证列表', '凭证'],
    'bank_keywords': ['银行存款辅助明细账'],
}

# 项目定制分类规则（项目名与辅助核算实际写法一致）
PROJECT_SPECIAL_RULES = {
    'clean_to_admin_project': '项目A',       # 该项目摘要含"保洁"归行政费用
    'greening_to_property_project': '项目D',  # 该项目摘要含"绿化养护"归物业成本
}
```

注意：**费用分类规则已外置可配**：28 类规则（`CLASSIFICATION_RULES` 覆盖/追加）、
特殊规则链关键词（`SPECIAL_RULES_CONFIG`：押金/保证金退回、增值税转出、信息系统）、
科目名称归组映射（`ACCOUNT_NAME_MAPPINGS`）均可通过 `config.py` 调整，改配置即生效，
无需改源码重新打包；清洗排除模式通过 `CLASSIFICATION_CONFIG.exclusion_patterns` 配置。

## 项目结构

```
费用分类汇总系统/
├── expense_classification.py      # 核心分类引擎
├── expense_classification_gui.py  # GUI 界面
├── bank_ledger_summary.py        # 银行台账汇总
├── config.example.py             # 示例配置（复制为 config.py 后生效）
├── test_expense_classification.py # 全量测试（pytest / unittest）
├── 用户操作说明书.md             # 详细操作手册
├── 费用分类原则文档.md           # 分类规则说明
└── 程序运行逻辑文档.md           # 技术架构文档
```

## 文档

- [用户操作说明书](用户操作说明书.md)：25KB 详细操作手册
- [费用分类原则文档](费用分类原则文档.md)：分类规则与逻辑说明
- [程序运行逻辑文档](程序运行逻辑文档.md)：技术架构与算法说明

## License

MIT

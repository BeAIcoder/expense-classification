# SPEC: 费用分类汇总系统 v2.2

> 版本: 2.2 | 更新: 2026-09-22 | 入口: expense_classification_gui.py（GUI）/ expense_classification.py（核心引擎/CLI）

## 1. 目的

读取银行流水（银行存款辅助明细账为主，凭证列表补充摘要/科目），按 28 类费用分类原则自动归类汇总，
输出 6-sheet 分类汇总报告，用于费用核算与现金流口径分析。严格收支分离（9 种收支类型）。

## 2. 模块结构

| 文件 | 职责 |
|------|------|
| `expense_classification.py` | 核心引擎：读取→整合→清洗→分类→汇总→导出；`run_pipeline(progress_callback, cancel_check)` 统一管线；`find_data_files()` 文件识别（GUI/CLI/台账工具三方共用） |
| `expense_classification_gui.py` | tkinter 图形界面：后台线程跑管线，经 `queue.Queue` + 主线程 `after` 轮询更新 UI（线程安全，v2.2 重构） |
| `bank_ledger_summary.py` | 银行台账控制台汇总工具（v2.2 薄壳版：复用引擎读取/分类/汇总，打印类别小计 + 审计追踪） |
| `config.py` / `config.example.py` | 可选配置：数据源参数 + 分类规则（缺失时引擎回退内置默认值，开箱即用） |
| `test_expense_classification.py` | 46 用例测试套件（pytest / unittest 双通道） |
| `费用分类汇总系统_v2.0.spec` | PyInstaller 打包配置（onefile，无控制台）——**当前使用** |
| `费用分类汇总系统.spec` | v1 遗留打包配置（upx 开启），不再使用，仅存档 |

## 3. 配置体系（v2.2 规则外置）

| 配置段 | 内容 |
|--------|------|
| `CLASSIFICATION_RULES` | 28 类分类规则：同名类别按字段覆盖，新类别追加末尾（注意顺序敏感：POS<财务费用，招商<行政） |
| `SPECIAL_RULES_CONFIG` | 特殊规则链关键词：押金/退回语义、增值税转出/调整标记、信息系统关键词 |
| `ACCOUNT_NAME_MAPPINGS` | 科目名称归组映射：同名替换关键词列表，新类别追加 |
| `FILE_FORMAT_CONFIG` / `BANK_ACCOUNT_CONFIG` / `PROJECT_SPECIAL_RULES` 等 | 表头行、目标科目、预期项目、项目定制规则、文件识别关键词 |
| `CLASSIFICATION_CONFIG.exclusion_patterns` | 清洗排除正则（损益结转等） |
| `OUTPUT_CONFIG` / `LOGGING_CONFIG` | 报告文件名格式（GUI/CLI 共用）、日志文件/级别/控制台开关 |

改配置即生效，**无需改源码重新打包**。

## 4. 输入/输出

| 方向 | 内容 |
|------|------|
| 输入 | 银行存款辅助明细账 Excel（必需）、凭证列表 Excel（可选，补摘要/科目）、config.py（可选） |
| 输出 | `费用分类汇总报告_v2_{timestamp}.xlsx`（6 sheet：财务收支摘要/全量分类明细/项目维度分析/月度趋势分析/分类标准快照/待人工复核预警）；bank_ledger_summary 输出控制台类别小计 + 审计追踪 |

> 详细口径与字段见 `程序运行逻辑文档.md`、`算法.md`、`费用分类原则文档.md`、`NEW_CLASSIFICATION_STANDARDS.md`

## 5. 运行方式

| 方式 | 命令 |
|------|------|
| exe | 双击 `dist/费用分类汇总系统_v2.0.exe` |
| 源码 GUI | `python expense_classification_gui.py` |
| 源码 CLI | `python expense_classification.py`（在当前目录自动识别数据文件） |
| 台账汇总 | `python bank_ledger_summary.py [Excel路径]`（缺省当前目录自动识别） |
| 测试 | `python -m pytest test_expense_classification.py -q`（46 用例） |

## 6. 依赖

`pandas`、`openpyxl`、`numpy`、`xlrd`、`dateutil`（tkinter/queue/threading 为标准库）

## 7. 重新打包

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --clean 费用分类汇总系统_v2.0.spec --distpath dist --workpath build
```

> 仅修改 config.py（分类规则/关键词）时**无需**重新打包；修改 .py 源码后才需要。

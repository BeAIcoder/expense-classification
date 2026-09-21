# 进度记录

> 项目: 费用分类汇总系统-v2.0
> 更新: 2026-09-22

## 本次操作（2026-09-22）v2.2：全量审查 BUG 修复 + 架构演进（方案 C）

### 1. 高危 BUG 修复（4 项 + 1 项新发现）

| # | 问题 | 修复 |
|---|------|------|
| 1 | GUI 后台线程直接操作 tkinter 并调 `root.update()`，随时 Tcl 崩溃 | worker 只投递 `queue.Queue`，主线程 `after` 轮询更新 UI；删除全部子线程 `root.update()`/messagebox |
| 2 | 日志挂 root logger 导致控制台 DEBUG + 双份输出 | 模块 logger 独占 handler + `propagate=False`；文件 DEBUG/控制台 INFO；`LOGGING_CONFIG` 真正生效 |
| 3 | 押金特殊规则吞掉"保证金退回"：退还保证金误记 ASSET | 方向感知：含退回/退还语义 → 保证金退回(OUTFLOW)，否则 → 押金类(ASSET)；5 个回归用例锁定 |
| 4 | 科目名小写化后 'POS'/'IT' 大写关键词永不命中 | 映射比较统一 `keyword.lower()`；补混合大小写用例 |
| 5 | （新发现）pandas 3 `astype(str)` 保留 NaN，纯银行源无凭证补摘要时 `strip` 崩溃 | `fillna('').astype(str).str.strip()`；bank_ledger_summary 冒烟暴露 |

### 2. 中危修复（4 项）

GUI 假取消（worker 经 `cancel_check` 在阶段边界真取消）、GUI 输出文件名绕过 `OUTPUT_CONFIG`（已接入 + 同秒防覆盖序号）、
bank_ledger_summary 静默全零输出与 `abs()` 方向错误（随薄壳化整体消除）、GUI 输出目录不可写到导出才炸（开始处理前预检）。

### 3. 架构演进（E1/E2/E3）

- **E1 规则外置**：`CLASSIFICATION_RULES`（28 类覆盖/追加）、`SPECIAL_RULES_CONFIG`（押金/退回/增值税/信息系统关键词）、`ACCOUNT_NAME_MAPPINGS`（科目名归组）均可 config 配置，改配置即生效，exe 用户无需重新打包
- **E2 bank_ledger_summary 薄壳化**：整文件重写为调用 `ExpenseClassifier` 的控制台工具，消除双轨分类逻辑；保留类别小计 + 审计追踪输出
- **E3 GUI 统一管线**：`run_pipeline` 新增 `progress_callback`/`cancel_check`，GUI 不再手抄 7 步管线，与 CLI 同一条代码路径

### 4. 测试与验证

| 测试项 | 结果 |
|--------|------|
| pytest / unittest 双通道（37 → 46 用例：方向/大小写/规则链优先级/改造基金/规则外置/pipeline 回调/GUI 线程安全与冒烟） | ✅ 46 passed |
| CLI 端到端（真实文件名 + 本机 config.py） | ✅ 退出码 0；控制台横幅恰好 1 次、无 DEBUG（B2 验证） |
| GUI 全链路冒烟（扫描→处理→产出 + 取消路径） | ✅ EXPENSE 50,916 / INFLOW 252,000 与 E2E 口径一致 |
| bank_ledger_summary 冒烟（纯银行源，含 NaN 摘要行） | ✅ 类别小计正确 |
| exe 重新打包（费用分类汇总系统_v2.0.spec，62.5MB） | ✅ 构建成功，启动 10 秒存活、可正常关闭 |

### 5. 遗留/建议

- exe 仍未做真实业务数据端到端测试，首次使用前用历史数据验证
- 标准示例 6（ipv6改造→资本性支出）与现行特殊规则（非基金改造→工程成本）口径分歧，仍待业务确认
- `read_all_sheets=True` 时 expected_sheets 不再强制非空（validate_config 矛盾已修）；`exclusion_patterns` 非法正则提前拦截

## 历史

- 2026-09-21：v2.1 P0 修复 + 全量测试，见 `测试报告_2026-09-21.md`
- 2026-08-15：打包 exe + 运行级测试
- 2026-02-27：v2.0 重构完成（GUI 化、分类标准升级），见 `更新说明_v2.0.md`

## 本次操作（2026-09-21）v2.1：P0 修复 + 全量测试

### 1. P0 修复（4 项）

| # | 问题 | 修复 |
|---|------|------|
| 1 | config.py 为死代码（无模块 import） | 引擎接入 config（`_cfg()` + 内置默认回退）；同步修正旧 config 的危险排除模式 |
| 2 | 摘要 sheet 漏 OUTFLOW/ADJUSTMENT/OTHER 三类 | 输出全部 9 种收支类型 + 未来类型兜底 |
| 3 | CLI 永远找不到凭证列表（与 GUI 识别逻辑分裂） | 新增共用 `find_data_files()`，GUI/CLI 统一 |
| 4 | 增值税留抵退税被误归税金调整 | 特殊规则限定转出/调整标记词且排除"退税" |

### 2. 测试发现并修复（8 项）

社会保险≠社保（人工成本漏分类）、项目特殊规则被小写化杀死（死代码）、
科目映射顺序遮蔽（工程改造基金/未知款项退回）、招商部差旅费被截走、
法律顾问费未分类、正则式关键词永不命中、8 个规则外类别落入 OTHER（已入典，28 类）、
sheet 名错字（待人工核核预警→待人工复核预警）、表头行可配置化。

### 3. 测试与打包

| 测试项 | 结果 |
|--------|------|
| `test_expense_classification.py`（37 用例，pytest 与 unittest 双通道） | ✅ 全部通过 |
| CLI 端到端（真实文件名 + 本机 config.py） | ✅ 退出码 0，摘要 9 类型齐全 |
| GUI 实例化 + 扫描识别 + 处理链路（隐藏窗口） | ✅ 通过 |
| exe 重新打包（PyInstaller 6.22，基于 v2.1 源码） | ✅ 构建成功 |

详细报告见 `测试报告_2026-09-21.md`；变更明细见 `更新说明_v2.0.md` 的 v2.1 章节。

### 4. 遗留/建议

- exe 仍未做真实业务数据端到端测试，首次使用前用历史数据验证
- 标准示例 6（ipv6改造→资本性支出）与现行特殊规则（非基金改造→工程成本）存在口径分歧，待业务确认
- 收入侧依赖源表贷方口径，换数据源时需实测确认借贷列含义

## 历史

- 2026-08-15：打包 exe + 运行级测试，见上文历史记录
- 2026-02-27：v2.0 重构完成（GUI 化、分类标准升级），见 `更新说明_v2.0.md`
- 2026-03-11：v2 重构测试通过，见 `V2_REFACTOR_TEST_REPORT.md`

## 本次操作（2026-08-15）

### 1. 打包 exe（此前无构建产物）

使用项目自带 PyInstaller spec（`费用分类汇总系统_v2.0.spec`）基于最新源码打包：

| 产物 | 路径 | 体积 |
|------|------|------|
| `费用分类汇总系统_v2.0.exe` | `dist/` 与项目根目录 | 34.6 MB |

### 2. 测试结果

| 测试项 | 结果 |
|--------|------|
| 源码导入（expense_classification / gui / config） | ✅ imports OK |
| exe 启动（GUI 窗口） | ✅ 启动后进程稳定存活，无崩溃 |

### 3. 环境说明

- 打包使用 Python 3.13 + PyInstaller 6.22 + pandas/openpyxl/xlrd
- spec 中 hiddenimports 已含 pandas、openpyxl、numpy、xlrd、dateutil、tkinter，打包无缺模块问题

## 遗留/建议

- exe 未做真实业务数据端到端测试（需要银行流水样例数据），建议首次实际使用时对照 `V2_REFACTOR_TEST_REPORT.md` 验证
- `expense_classification.py`（58KB）为主要业务逻辑，`expense_classification_gui.py` 为界面层；`bank_ledger_summary.py`、`config.py` 为辅助模块

## 历史

- 2026-02-27：v2.0 重构完成（GUI 化、分类标准升级），见 `更新说明_v2.0.md`
- 2026-03-11：v2 重构测试通过，见 `V2_REFACTOR_TEST_REPORT.md`

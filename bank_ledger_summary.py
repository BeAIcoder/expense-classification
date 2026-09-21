#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
银行存款辅助明细账分类汇总工具（薄壳版）

复用 expense_classification.ExpenseClassifier 的读取/清洗/分类/汇总逻辑，
在控制台打印各费用类别小计与审计明细，不再维护独立的分类规则。

用法：
    python bank_ledger_summary.py [Excel文件路径]
    缺省文件路径时，在当前目录自动识别银行存款辅助明细账（找不到则尝试凭证列表）。
"""

import os
import sys
import argparse

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Windows GBK 控制台打印中文/生僻字时避免 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    from expense_classification import ExpenseClassifier, find_data_files
except ImportError as e:
    print(f"错误：无法导入核心引擎 expense_classification: {e}")
    sys.exit(1)


def resolve_input(file_path):
    """确定输入文件：显式路径优先，缺省时在当前目录自动识别"""
    if file_path:
        if not os.path.exists(file_path):
            print(f"错误: 文件不存在: {file_path}")
            sys.exit(1)
        return file_path
    voucher_file, bank_file = find_data_files('.')
    resolved = bank_file or voucher_file
    if not resolved:
        print("错误: 当前目录未识别到银行存款辅助明细账或凭证列表文件，请显式指定文件路径")
        sys.exit(1)
    print(f"自动识别输入文件: {resolved}")
    return resolved


def main():
    parser = argparse.ArgumentParser(description="银行存款辅助明细账分类汇总工具（复用核心引擎规则）")
    parser.add_argument("file", nargs="?", default=None,
                        help="Excel文件路径（缺省时在当前目录自动识别）")
    args = parser.parse_args()

    file_path = resolve_input(args.file)

    clf = ExpenseClassifier()

    # 优先按银行明细账口径读取（筛选目标科目）；读不到数据时回退凭证列表口径
    bank_df = clf.read_bank_data(file_path)
    if bank_df is None or len(bank_df) == 0:
        print("按银行明细账口径未读到数据，尝试按凭证列表口径读取...")
        if clf.read_voucher_data(file_path) is None:
            print("错误: 两种口径均未读到有效数据，请检查文件格式与表头行配置")
            sys.exit(1)

    if clf.integrate_data() is None or clf.clean_data() is None or clf.classify_expenses() is None:
        print("错误: 数据处理失败，请查看日志 expense_classification.log")
        sys.exit(1)

    result = clf.summarize_expenses()
    if result is None:
        print("错误: 汇总计算失败，请查看日志 expense_classification.log")
        sys.exit(1)

    df = clf.processed_data.copy()
    # 收支类型列在 summarize_expenses 的副本上生成，这里自行映射
    rules = clf.get_classification_rules()
    cat_to_type = {cat: rule.get('type', 'OTHER') for cat, rule in rules.items()}
    df['收支类型'] = df['费用类别'].map(cat_to_type).fillna('OTHER')

    # 类别小计（按收支类型分组，金额降序；保留 2 位小数避免 float 累加漂移）
    print("\n" + "=" * 60)
    print("银行存款辅助明细账分类汇总结果")
    print("=" * 60)
    print(f"{'收支类型':<14}{'费用类别':<18}{'小计金额':>15}")
    print("-" * 60)
    grouped = (df.groupby(['收支类型', '费用类别'])['贷方本币'].sum()
                 .round(2).sort_values(ascending=False))
    for (flow_type, category), amount in grouped.items():
        print(f"{flow_type:<14}{category:<18}{amount:>15,.2f}")
    print("-" * 60)
    print(f"{'合计':<32}{round(grouped.sum(), 2):>15,.2f}")
    print("=" * 60)

    # 未分类预警
    unclassified = df[df['费用类别'] == '未分类']
    if not unclassified.empty:
        print(f"\n警告: {len(unclassified)} 条记录未能分类，建议补充分类规则：")
        for _, row in unclassified.iterrows():
            print(f"  - {row.get('凭证号', '')} | {row.get('贷方本币', 0):>12,.2f} | {str(row.get('摘要', ''))[:40]}")

    # 审计追踪明细
    print("\n" + "=" * 80)
    print("审计追踪信息")
    print("=" * 80)
    for (flow_type, category), group in df.groupby(['收支类型', '费用类别']):
        print(f"\n[{flow_type}] {category}:")
        print("-" * 80)
        for _, row in group.iterrows():
            print(f"{str(row.get('凭证号', '')):<12}{row.get('贷方本币', 0):>12,.2f}  {str(row.get('摘要', ''))[:50]}")
        print(f"小计: {round(group['贷方本币'].sum(), 2):,.2f}")


if __name__ == "__main__":
    main()

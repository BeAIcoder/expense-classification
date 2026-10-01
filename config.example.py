#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
费用分类汇总系统配置文件

功能：配置系统运行参数（工作表、科目编码、表头行、文件识别关键词、项目定制规则等）。
本文件为可选项：复制为 config.py 后按实际环境修改；缺失时引擎使用内置默认值，
开源克隆在无 config.py 时开箱即用。

注意：费用分类的 28 类规则、特殊规则关键词、科目名称映射均已外置——
可通过本文件的 CLASSIFICATION_RULES / SPECIAL_RULES_CONFIG / ACCOUNT_NAME_MAPPINGS
覆盖或追加，改配置即生效，无需改源码重新打包。
"""

# 凭证列表工作表配置
VOUCHER_SHEET_CONFIG = {
    # 预期的工作表名称列表（按实际项目填写，如各项目辅助账 sheet 名）
    'expected_sheets': [
        '项目A',
        '项目B',
        '公司总部',
        '项目D'
    ],
    # 是否读取所有工作表（True: 读取所有；False: 只读取 expected_sheets 中的工作表）
    'read_all_sheets': True,
    # 数据去重配置
    'deduplication': {
        'enabled': True,  # 是否启用去重
        'key_columns': ['期间', '凭证日期', '凭证号', '摘要', '贷方本币']  # 去重关键字段
    }
}

# 银行存款科目配置
BANK_ACCOUNT_CONFIG = {
    # 目标科目编码（银行存款，匹配其主科目及子科目）
    'target_account': '100201',
    # 预期的辅助核算项目名称（用于项目完整性校验警告）
    'expected_projects': [
        '项目A',
        '项目B',
        '公司总部',
        '项目D'
    ],
    # 项目完整性校验配置
    'project_validation': {
        'enabled': True,   # 是否启用项目完整性校验
        'strict_mode': False  # 严格模式：缺少预期项目时记 ERROR 而非 WARNING
    }
}

# 数据文件格式配置
FILE_FORMAT_CONFIG = {
    # 表头所在行（0 起算）：凭证列表默认第 2 行（索引 1），银行明细账默认第 11 行（索引 10）
    # 源表格式变化（多一行标题/合并单元格）时只需改这里，无需改源码
    'voucher_header_row': 1,
    'bank_header_row': 10,
    # 文件识别关键词（GUI 与 CLI 共用）：文件名含 bank_keywords 判为银行明细账，
    # 含 voucher_keywords 判为凭证列表；银行优先匹配
    'voucher_keywords': ['凭证列表', '凭证'],
    'bank_keywords': ['银行存款辅助明细账'],
}

# 项目定制分类规则（项目名需与辅助核算/摘要中的实际写法一致）
PROJECT_SPECIAL_RULES = {
    # 该项目摘要含"保洁"归行政费用（默认值即开源脱敏别名）
    'clean_to_admin_project': '项目A',
    # 该项目摘要含"绿化/养护"归物业成本
    'greening_to_property_project': '项目D',
}

# 数据清洗配置
CLEAN_CONFIG = {
    # 整合后数据的去重键（第二道去重，介于读取与分类之间）
    'dedup_key_columns': ['凭证号', '摘要', '贷方本币'],
}

# 费用分类配置
CLASSIFICATION_CONFIG = {
    # 绝对排除模式（正则，命中即剔除）：损益结转等完全不涉及现金流的记录
    # 注意：勿把'收入''折旧'等业务关键词加入此列表，否则会误删待分类数据
    'exclusion_patterns': [r'损益结转', r'结转.*损益', r'重新计提', r'期末结转'],
}

# 费用分类规则覆盖（可选）：28 类默认规则存放于 classification_rules.json
# （单一权威源，由 rules_loader.py 加载），此处同名类别按字段覆盖
# （如只改 keywords），不同名的类别追加到末尾。
# 注意顺序敏感的遮蔽关系：POS刷卡手续费 必须在 财务费用 之前，招商费用 必须在 行政费用 之前。
# 示例：
# CLASSIFICATION_RULES = {
#     '行政费用': {'keywords': ['办公', '差旅', '快递']},          # 覆盖已有类别关键词
#     '会议费': {'keywords': ['会议', '会务'], 'type': 'EXPENSE', 'description': '会议会务费'},
# }
CLASSIFICATION_RULES = {}

# 特殊规则链关键词（可选）：押金/保证金、增值税转出、信息系统等优先于关键词规则的特殊逻辑
SPECIAL_RULES_CONFIG = {
    # 押金/保证金类：摘要或科目名命中 deposit_keywords 即进入押金逻辑
    'deposit_keywords': ['押金', '保证金', '诚意金', '租赁押金'],
    # 命中 deposit_keywords 且摘要含退回语义 → 保证金退回（OUTFLOW）；否则 → 押金类（ASSET）
    'refund_keywords': ['退回', '退还'],
    # 增值税转出/调整（税金划拨，非费用）
    'vat_transfer_keywords': ['增值税转出', '进项税额转出', '转出未交', '未交增值税', '预缴税款调拨', '税额调整', '税费调整'],
    'vat_adjust_markers': ['转出', '调拨', '调整'],
    # 信息系统成本特殊规则（比较时自动转小写，'IT' 等英文关键词可正常命中）
    'info_system_keywords': ['网络', '网络使用费', '硬件', '硬件维护', '软件', '软件维护', '系统', '信息系统', 'IT', '信息化', '弱电布线', '宽带费', '客流', '软件许可', '备件采购', '布线'],
}

# 科目名称归组映射覆盖（可选）：默认映射内置在引擎 classify_expenses()，
# 同名类别替换关键词列表，新类别追加到末尾。比较时自动转小写。
# 示例：
# ACCOUNT_NAME_MAPPINGS = {
#     '行政费用': ['办公', '行政', '差旅', '快递'],
#     '会议费': ['会议费', '会务费'],
# }
ACCOUNT_NAME_MAPPINGS = {}

# 数据验证配置
DATA_VALIDATION_CONFIG = {
    # 金额勾稽关系验证（借贷平衡容差，元）
    'amount_reconciliation': {
        'enabled': True,
        'tolerance': 0.01
    },
}

# 输出配置
OUTPUT_CONFIG = {
    # 报告文件名格式（{timestamp} 会被替换为 YYYYMMDD_HHMMSS）
    'filename_format': '费用分类汇总报告_v2_{timestamp}.xlsx',
    # 输出工作表（与 export_results 实际产出一致，仅供查阅）
    'sheets': ['财务收支摘要', '全量分类明细', '项目维度分析', '月度趋势分析', '分类标准快照', '待人工复核预警']
}

# 日志配置
LOGGING_CONFIG = {
    'level': 'INFO',  # 控制台日志级别：DEBUG, INFO, WARNING, ERROR（文件日志固定 DEBUG）
    'file': 'expense_classification.log',
    'format': '%(asctime)s - %(levelname)s - %(module)s - %(message)s',
    'console_output': True  # 是否输出到控制台
}


def get_config(config_name):
    """获取指定配置

    Args:
        config_name: 配置名称

    Returns:
        配置字典
    """
    configs = {
        'voucher': VOUCHER_SHEET_CONFIG,
        'bank': BANK_ACCOUNT_CONFIG,
        'file_format': FILE_FORMAT_CONFIG,
        'project_special': PROJECT_SPECIAL_RULES,
        'clean': CLEAN_CONFIG,
        'classification': CLASSIFICATION_CONFIG,
        'classification_rules': CLASSIFICATION_RULES,
        'special_rules': SPECIAL_RULES_CONFIG,
        'account_mappings': ACCOUNT_NAME_MAPPINGS,
        'validation': DATA_VALIDATION_CONFIG,
        'output': OUTPUT_CONFIG,
        'logging': LOGGING_CONFIG
    }
    return configs.get(config_name, {})


def validate_config():
    """验证配置有效性

    Returns:
        (bool, list): (是否有效, 错误信息列表)
    """
    errors = []

    # 验证凭证配置：仅在 read_all_sheets=False 时强制 expected_sheets 非空
    # （read_all_sheets=True 时读取全部工作表，expected_sheets 不参与过滤）
    if not VOUCHER_SHEET_CONFIG.get('read_all_sheets', True) \
            and not VOUCHER_SHEET_CONFIG.get('expected_sheets'):
        errors.append("read_all_sheets=False 时，凭证工作表配置 expected_sheets 不能为空")

    # 验证银行科目配置
    if not BANK_ACCOUNT_CONFIG.get('target_account'):
        errors.append("银行科目编码不能为空")

    # 验证输出配置
    if not OUTPUT_CONFIG.get('filename_format'):
        errors.append("输出文件名格式不能为空")
    if '{timestamp}' not in OUTPUT_CONFIG.get('filename_format', ''):
        errors.append("输出文件名格式必须包含 {timestamp} 占位符")

    # 验证表头行配置
    if not isinstance(FILE_FORMAT_CONFIG.get('voucher_header_row'), int):
        errors.append("voucher_header_row 必须为整数（0 起算）")
    if not isinstance(FILE_FORMAT_CONFIG.get('bank_header_row'), int):
        errors.append("bank_header_row 必须为整数（0 起算）")

    # 验证排除模式为合法正则（坏正则会在运行时 re 调用才暴露，提前拦截）
    import re
    for pattern in CLASSIFICATION_CONFIG.get('exclusion_patterns', []):
        try:
            re.compile(pattern)
        except re.error as e:
            errors.append(f"exclusion_patterns 含非法正则 '{pattern}': {e}")

    # 验证容差为非负数
    tolerance = DATA_VALIDATION_CONFIG.get('amount_reconciliation', {}).get('tolerance', 0.01)
    if not isinstance(tolerance, (int, float)) or tolerance < 0:
        errors.append("amount_reconciliation.tolerance 必须为非负数")

    return len(errors) == 0, errors


if __name__ == "__main__":
    # 测试配置
    is_valid, errors = validate_config()
    if is_valid:
        print("配置验证通过")
        print(f"预期凭证工作表: {VOUCHER_SHEET_CONFIG['expected_sheets']}")
        print(f"银行科目编码: {BANK_ACCOUNT_CONFIG['target_account']}")
        print(f"预期项目: {BANK_ACCOUNT_CONFIG['expected_projects']}")
        print(f"表头行: 凭证={FILE_FORMAT_CONFIG['voucher_header_row']}, 银行={FILE_FORMAT_CONFIG['bank_header_row']}")
    else:
        print("配置验证失败:")
        for error in errors:
            print(f"  - {error}")

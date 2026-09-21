#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
费用分类汇总系统 v2.1 全量测试套件

覆盖：配置接入、分类规则完整性、22 个标准业务场景分类、数据清洗、汇总计算、
导出报表（6 sheet + 全收支类型）、文件识别（CLI/GUI 共用）、端到端管线。

运行方式：
    python -m pytest test_expense_classification.py -v
    或
    python -m unittest test_expense_classification -v
"""

import os
import sys
import gc
import tempfile
import time
import unittest
from datetime import datetime

import pandas as pd

import expense_classification as ec
from expense_classification import ExpenseClassifier, find_data_files, _cfg

CLASSIFY_COLUMNS = ['期间', '凭证日期', '凭证号', '摘要', '贷方本币', '科目编码', '科目名称', '辅助核算']


class _StubConfig:
    """测试用配置桩：模拟本机 config.py"""

    BANK_ACCOUNT_CONFIG = {
        'target_account': '100202',
        'expected_projects': ['不存在的项目'],
        'project_validation': {'enabled': True, 'strict_mode': True},
    }
    FILE_FORMAT_CONFIG = {'voucher_header_row': 1, 'bank_header_row': 10}
    VOUCHER_SHEET_CONFIG = {
        'read_all_sheets': False,
        'expected_sheets': ['项目A'],
        'deduplication': {'key_columns': ['期间', '凭证号', '摘要']},
    }


class ConfigWiringCase(unittest.TestCase):
    """config.py 接入与回退测试"""

    def setUp(self):
        self._orig = ec._app_config
        ec._app_config = None

    def tearDown(self):
        ec._app_config = self._orig

    def test_cfg_returns_default_without_config(self):
        self.assertEqual(_cfg('BANK_ACCOUNT_CONFIG.target_account', '100201'), '100201')
        self.assertEqual(_cfg('CLASSIFICATION_CONFIG.exclusion_patterns', ['x']), ['x'])

    def test_cfg_reads_stub_config(self):
        ec._app_config = _StubConfig
        self.assertEqual(_cfg('BANK_ACCOUNT_CONFIG.target_account', '100201'), '100202')
        self.assertEqual(_cfg('FILE_FORMAT_CONFIG.bank_header_row', 10), 10)

    def test_cfg_missing_key_falls_back(self):
        ec._app_config = _StubConfig
        self.assertEqual(_cfg('FILE_FORMAT_CONFIG.not_exist', 'dflt'), 'dflt')
        self.assertEqual(_cfg('NO_SUCH_SECTION.key', 'dflt'), 'dflt')

    def test_cfg_broken_config_falls_back(self):
        class _Broken:
            BANK_ACCOUNT_CONFIG = None  # 取 .get 会炸，需被 except 兜住

        ec._app_config = _Broken
        self.assertEqual(_cfg('BANK_ACCOUNT_CONFIG.target_account', '100201'), '100201')

    def test_real_config_if_present_is_consistent(self):
        """本机存在 config.py 时，验证其关键配置可被读取且格式正确"""
        try:
            import config as real_config
        except ImportError:
            self.skipTest('本机无 config.py（开源克隆场景）')
        account = _cfg('BANK_ACCOUNT_CONFIG.target_account', '100201')
        self.assertIsInstance(account, str)
        self.assertTrue(account)
        self.assertIsInstance(_cfg('FILE_FORMAT_CONFIG.bank_header_row', 10), int)
        self.assertIsInstance(_cfg('FILE_FORMAT_CONFIG.voucher_header_row', 1), int)
        # validate_config 应通过（防止手写配置缺字段/类型错）
        is_valid, errors = real_config.validate_config()
        self.assertTrue(is_valid, f'config.py 校验失败: {errors}')


class ClassificationRulesCase(unittest.TestCase):
    """规则字典完整性"""

    def setUp(self):
        self.rules = ExpenseClassifier().get_classification_rules()

    def test_category_count_and_unique(self):
        self.assertEqual(len(self.rules), 28)
        self.assertEqual(len(set(self.rules)), len(self.rules))

    def test_valid_types(self):
        valid = {'EXPENSE', 'INFLOW', 'CAPITAL', 'TRANSFER', 'TAX_TRANSFER', 'ASSET', 'ADJUSTMENT', 'OUTFLOW'}
        for cat, rule in self.rules.items():
            self.assertIn(rule['type'], valid, f'{cat} 类型非法: {rule["type"]}')

    def test_non_empty_keywords_and_description(self):
        for cat, rule in self.rules.items():
            self.assertTrue(rule.get('keywords'), f'{cat} 关键词为空')
            self.assertTrue(rule.get('description'), f'{cat} 缺少描述')

    def test_auxiliary_categories_present(self):
        """8 个辅助类别必须入典（修复 OTHER 黑洞）"""
        for cat in ['税金调整（非费用）', '销售费用', '营业外支出', '工会经费',
                    '折旧费用', '在职人工成本', '工程改造基金', '未知款项退回']:
            self.assertIn(cat, self.rules)

    def test_pos_before_finance(self):
        """POS刷卡手续费必须排在财务费用之前（顺序敏感）"""
        keys = list(self.rules)
        self.assertLess(keys.index('POS刷卡手续费'), keys.index('财务费用'))

    def test_zhaoshang_before_xingzheng(self):
        """招商费用必须排在行政费用之前（修复招商部差旅费被截走）"""
        keys = list(self.rules)
        self.assertLess(keys.index('招商费用'), keys.index('行政费用'))

    def test_no_regex_style_keywords(self):
        """关键词为子串匹配，不允许出现正则写法（历史 bug：'收到.*款' 永不命中）"""
        for cat, rule in self.rules.items():
            for kw in rule['keywords']:
                self.assertNotIn('.*', kw, f'{cat} 关键词含正则写法: {kw}')
                self.assertNotIn('*', kw, f'{cat} 关键词含正则写法: {kw}')


class ClassifyExpensesCase(unittest.TestCase):
    """分类引擎场景测试"""

    def setUp(self):
        self._orig = ec._app_config
        ec._app_config = None  # 强制内置默认值（项目A/项目D 别名），保证确定性
        self.clf = ExpenseClassifier()

    def tearDown(self):
        ec._app_config = self._orig

    def classify_one(self, summary, account_name='', account_code=''):
        df = pd.DataFrame([{'摘要': summary, '科目名称': account_name, '科目编码': account_code,
                            '凭证号': '记-1', '贷方本币': 100, '辅助核算': '项目A', '凭证日期': '2026-02-01', '期间': '2026-02'}])
        self.clf.processed_data = df
        result = self.clf.classify_expenses()
        return result.iloc[0]['费用类别'], result.iloc[0]['匹配关键字']

    def test_standards_22_examples(self):
        """NEW_CLASSIFICATION_STANDARDS.md 第 4 节 22 个典型场景"""
        cases = [
            ('支付某燃气公司1月燃气费', '公共事业费'),
            ('支付2026年1月员工社会保险费', '人工成本'),
            ('收到退回 1 月电费', '经营收入'),
            ('项目B银行账户资金划拨至项目D', '资金划拨'),
            ('支付电梯年度维护费', '工程成本'),
            # 已知分歧：标准示例6（ipv6改造→资本性支出）与现行特殊规则（非基金改造→工程成本）冲突，
            # 测试锁定现行行为，待业务确认阈值后调整（见 README/算法说明）
            ('支付 ipv6 改造费', '工程成本'),
            ('转出本月未交增值税', '税金调整（非费用）'),
            ('支付项目A2025年房产税', '税金支出'),
            ('支付薪班班1月外包服务费', '外包人工'),
            ('收到待清算商户转账款', '经营收入'),
            ('支付员工宿舍押金', '押金类（非费用）'),
            ('支付2025年三季度特约保洁费', '物业成本'),
            ('支付赢商网年度会员费', '企划类费用'),
            ('支付办公用品采购(中性笔、纸)', '行政费用'),
            ('支付招商部出差外地差旅费', '招商费用'),
            ('建行一般户手续费', '财务费用'),
            ('支付广告位日常加固维护', '工程成本'),
            ('收到张某个人款项', '经营收入'),
            ('确认202602月综合管理费', '计提确认类'),
            ('支付项目D绿化养护费', '物业成本'),
            ('收到税务局增值税留抵退税', '经营收入'),
            ('支付常年法律顾问费', '行政费用'),
        ]
        for summary, expected in cases:
            with self.subTest(summary=summary):
                category, _ = self.classify_one(summary)
                self.assertEqual(category, expected)

    def test_v2_regression_cases(self):
        """v2.0 更新说明中的修复案例"""
        cases = [
            ('报销起诉项目A违约商户案件公告费', '行政费用'),
            ('确认202602月固定租金', '计提确认类'),
            ('待清算商户款项转账手续费', 'POS刷卡手续费'),
        ]
        for summary, expected in cases:
            with self.subTest(summary=summary):
                category, _ = self.classify_one(summary)
                self.assertEqual(category, expected)

    def test_p0_vat_refund_fix(self):
        """P0 修复：增值税留抵退税必须归经营收入，不再被税金调整截走"""
        for summary in ['收到税务局增值税留抵退税', '增值税留抵退税', '收到增值税出口退税']:
            with self.subTest(summary=summary):
                category, _ = self.classify_one(summary)
                self.assertEqual(category, '经营收入')

    def test_vat_transfer_still_works(self):
        """增值税转出/调整仍归税金调整（税金划拨）"""
        for summary in ['转出本月未交增值税', '进项税额转出', '转出未交增值税', '增值税调拨']:
            with self.subTest(summary=summary):
                category, _ = self.classify_one(summary)
                self.assertEqual(category, '税金调整（非费用）')

    def test_account_name_mapping(self):
        """科目名称归组：工会经费/在职人工成本/未知款项退回"""
        cases = [
            ('支付2月工会经费', '工会经费', '', '工会经费'),
            ('一笔神秘发放', '工资费用', '', '在职人工成本'),
            ('收回一笔错账', '误打款退还', '', '未知款项退回'),
            ('一笔专项基金支出', '工程改造基金', '', '工程改造基金'),
        ]
        for summary, acc_name, acc_code, expected in cases:
            with self.subTest(summary=summary):
                category, _ = self.classify_one(summary, account_name=acc_name, account_code=acc_code)
                self.assertEqual(category, expected)

    def test_account_code_mapping(self):
        """科目编码前缀归组：6602/6603/6601/6711"""
        cases = [
            ('一笔神秘支出', '神秘科目', '660201', '行政费用'),
            ('一笔神秘支出', '神秘科目', '660301', '财务费用'),
            ('一笔神秘支出', '神秘科目', '660101', '销售费用'),
            ('一笔神秘支出', '神秘科目', '671101', '营业外支出'),
        ]
        for summary, acc_name, acc_code, expected in cases:
            with self.subTest(acc_code=acc_code):
                category, _ = self.classify_one(summary, account_name=acc_name, account_code=acc_code)
                self.assertEqual(category, expected)

    def test_exclude_keywords(self):
        """排除词：含'搬运'的物业费不走物业成本，走行政费用"""
        category, _ = self.classify_one('物业费-搬运服务')
        self.assertEqual(category, '行政费用')

    def test_unclassified(self):
        category, _ = self.classify_one('一笔完全无法识别的往来')
        self.assertEqual(category, '未分类')

    def test_project_special_rules_configurable(self):
        """项目定制规则的项目名来自配置（默认 项目A/项目D 别名）"""
        category, _ = self.classify_one('项目A保洁服务费')
        self.assertEqual(category, '行政费用')
        category, _ = self.classify_one('项目D绿化养护费')
        self.assertEqual(category, '物业成本')

    def test_deposit_refund_direction(self):
        """押金/保证金方向修正（B3）：含退回语义 → 保证金退回(OUTFLOW)，否则 → 押金类(ASSET)"""
        cases = [
            ('支付员工宿舍押金', '押金类（非费用）'),
            ('支付租赁保证金', '押金类（非费用）'),
            ('退还商户履约保证金', '保证金退回'),
            ('退回租赁诚意金', '保证金退回'),
            ('支付保证金及退回多收保证金', '保证金退回'),
        ]
        for summary, expected in cases:
            with self.subTest(summary=summary):
                category, _ = self.classify_one(summary)
                self.assertEqual(category, expected)

    def test_case_insensitive_english_keywords(self):
        """大小写修复（B4）：account_name 已小写化，'POS'/'IT' 等关键词必须仍能命中"""
        category, _ = self.classify_one('一笔结算扣款', account_name='POS结算户')
        self.assertEqual(category, 'POS刷卡手续费')
        category, _ = self.classify_one('支付IT运维服务费')
        self.assertEqual(category, '信息系统成本')
        category, _ = self.classify_one('年度宽带费缴纳')
        self.assertEqual(category, '信息系统成本')

    def test_special_rule_chain_priority(self):
        """特殊规则链优先级回归：押金规则先于确认/计提规则"""
        # 同时命中"确认"与"押金"：押金规则在链上更靠前，应归押金类
        category, _ = self.classify_one('确认收取租赁押金')
        self.assertEqual(category, '押金类（非费用）')
        # 仅命中确认+管理费：仍归计提确认类（不受押金规则影响）
        category, _ = self.classify_one('确认202602月综合管理费')
        self.assertEqual(category, '计提确认类')

    def test_gaizao_fund_goes_capital(self):
        """改造基金分支：工程改造基金必须归资本性支出，不被工程成本截走"""
        category, _ = self.classify_one('支付工程改造基金')
        self.assertEqual(category, '资本性支出')
        # 非基金改造仍归工程成本（现行口径，与标准示例6的分歧见 progress.md）
        category, _ = self.classify_one('支付ipv6改造费')
        self.assertEqual(category, '工程成本')

    def test_rules_override_via_config(self):
        """E1：CLASSIFICATION_RULES 可覆盖已有类别、追加新类别"""
        class _Stub:
            CLASSIFICATION_RULES = {
                '行政费用': {'keywords': ['快递费']},
                '会议费': {'keywords': ['会议', '会务'], 'type': 'EXPENSE', 'description': '会议会务费'},
            }

        ec._app_config = _Stub
        try:
            rules = self.clf.get_classification_rules()
            self.assertEqual(len(rules), 29)  # 28 默认 + 1 新增
            self.assertEqual(rules['行政费用']['keywords'], ['快递费'])
            self.assertEqual(rules['行政费用']['type'], 'EXPENSE')  # 未覆盖字段保留
            category, _ = self.classify_one('支付年度工作会议会务费')
            self.assertEqual(category, '会议费')
        finally:
            ec._app_config = None

    def test_special_rules_config_override(self):
        """E1：SPECIAL_RULES_CONFIG 可替换押金/退回关键词"""
        class _Stub:
            SPECIAL_RULES_CONFIG = {'deposit_keywords': ['押运金'], 'refund_keywords': ['返还']}

        ec._app_config = _Stub
        try:
            category, _ = self.classify_one('支付设备押运金')
            self.assertEqual(category, '押金类（非费用）')
            category, _ = self.classify_one('返还设备押运金')
            self.assertEqual(category, '保证金退回')
        finally:
            ec._app_config = None

    def test_unclassified_ratio_warning(self):
        """未分类占比超 3% 触发 WARNING"""
        rows = [{'摘要': '一笔完全无法识别的往来', '科目名称': '', '科目编码': '',
                 '凭证号': f'记-{i}', '贷方本币': 100, '辅助核算': '项目A',
                 '凭证日期': '2026-02-01', '期间': '2026-02'} for i in range(10)]
        self.clf.processed_data = pd.DataFrame(rows)
        with self.assertLogs('expense_classification', level='WARNING') as cm:
            self.clf.classify_expenses()
        self.assertTrue(any('未分类占比' in msg for msg in cm.output))


class CleanDataCase(unittest.TestCase):
    """数据清洗测试"""

    def setUp(self):
        self._orig = ec._app_config
        ec._app_config = None
        self.clf = ExpenseClassifier()

    def tearDown(self):
        ec._app_config = self._orig

    def _run_clean(self, rows):
        self.clf.processed_data = pd.DataFrame(rows)
        return self.clf.clean_data()

    def test_filter_non_positive_and_exclusions(self):
        rows = [
            {'摘要': '正常记录', '贷方本币': 100, '借方本币': 0, '凭证号': '记-1', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '零金额记录', '贷方本币': 0, '借方本币': 0, '凭证号': '记-2', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '负数记录', '贷方本币': -50, '借方本币': 0, '凭证号': '记-3', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '损益结转', '贷方本币': 200, '借方本币': 0, '凭证号': '记-4', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '重新计提费用', '贷方本币': 300, '借方本币': 0, '凭证号': '记-5', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '期末结转', '贷方本币': 400, '借方本币': 0, '凭证号': '记-6', '凭证日期': '2026-02-01', '期间': '2026-02'},
        ]
        result = self._run_clean(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]['摘要'], '正常记录')

    def test_dedup(self):
        rows = [
            {'摘要': '重复记录', '贷方本币': 100, '借方本币': 0, '凭证号': '记-1', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '重复记录', '贷方本币': 100, '借方本币': 0, '凭证号': '记-1', '凭证日期': '2026-02-01', '期间': '2026-02'},
        ]
        result = self._run_clean(rows)
        self.assertEqual(len(result), 1)

    def test_exclusion_patterns_from_config(self):
        """排除模式来自 CLASSIFICATION_CONFIG.exclusion_patterns"""
        class _Stub:
            CLASSIFICATION_CONFIG = {'exclusion_patterns': [r'测试排除']}

        self._orig_app = ec._app_config
        ec._app_config = _Stub
        try:
            rows = [
                {'摘要': '测试排除项', '贷方本币': 100, '借方本币': 0, '凭证号': '记-1', '凭证日期': '2026-02-01', '期间': '2026-02'},
                {'摘要': '损益结转', '贷方本币': 100, '借方本币': 0, '凭证号': '记-2', '凭证日期': '2026-02-01', '期间': '2026-02'},
            ]
            result = self._run_clean(rows)
            self.assertEqual(len(result), 1)
            self.assertEqual(result.iloc[0]['摘要'], '损益结转')
        finally:
            ec._app_config = self._orig_app


class SummarizeCase(unittest.TestCase):
    """汇总计算测试"""

    def setUp(self):
        self._orig = ec._app_config
        ec._app_config = None
        self.clf = ExpenseClassifier()

    def tearDown(self):
        ec._app_config = self._orig

    def _prepare(self):
        rows = [
            {'摘要': '支付某燃气公司1月燃气费', '科目名称': '', '科目编码': '', '凭证号': '记-1', '贷方本币': 100, '辅助核算': '项目A', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '收到待清算商户转账款', '科目名称': '', '科目编码': '', '凭证号': '记-2', '贷方本币': 500, '辅助核算': '项目B', '凭证日期': '2026-02-01', '期间': '2026-02'},
            {'摘要': '转出本月未交增值税', '科目名称': '', '科目编码': '', '凭证号': '记-3', '贷方本币': 300, '辅助核算': '公司总部', '凭证日期': '2026-03-01', '期间': '2026-03'},
            {'摘要': '支付2月工会经费', '科目名称': '工会经费', '科目编码': '', '凭证号': '记-4', '贷方本币': 50, '辅助核算': '公司总部', '凭证日期': '2026-03-01', '期间': '2026-03'},
            {'摘要': '一笔神秘支出', '科目名称': '神秘科目', '科目编码': '671101', '凭证号': '记-5', '贷方本币': 70, '辅助核算': '项目D', '凭证日期': '2026-03-01', '期间': '2026-03'},
        ]
        self.clf.processed_data = pd.DataFrame(rows)
        self.clf.classify_expenses()
        return self.clf.summarize_expenses()

    def test_type_totals_and_mapping(self):
        result = self._prepare()
        totals = result['type_totals']
        self.assertAlmostEqual(totals.get('EXPENSE', 0), 220.0)   # 100 + 50 + 70
        self.assertAlmostEqual(totals.get('INFLOW', 0), 500.0)
        self.assertAlmostEqual(totals.get('TAX_TRANSFER', 0), 300.0)  # 税金调整（非费用）入典
        self.assertNotIn('OTHER', totals)  # 无未分类、无规则外漏网

    def test_monthly_and_project(self):
        result = self._prepare()
        self.assertIn('2026-02', result['monthly_trend']['EXPENSE'])
        self.assertIn('项目A', result['project_distribution']['公共事业费'])

    def test_percentage_in_type(self):
        result = self._prepare()
        detail = next(d for d in result['details'] if d['category'] == '公共事业费')
        self.assertAlmostEqual(detail['percentage_in_type'], 100 / 220 * 100)


class FindDataFilesCase(unittest.TestCase):
    """CLI/GUI 共用文件识别测试"""

    def setUp(self):
        self._orig = ec._app_config
        ec._app_config = None
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name

    def tearDown(self):
        gc.collect()  # 释放 pandas/openpyxl 可能持有的文件句柄（Windows 删除需）
        self.tmp.cleanup()
        ec._app_config = self._orig

    def _touch(self, name, mtime=None):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            f.write('x')
        if mtime:
            os.utime(path, (mtime, mtime))
        return path

    def test_identify_both(self):
        v = self._touch('凭证列表_2026-02-27 09_23_58.xlsx')
        b = self._touch('2026.2.27银行存款辅助明细账_2026-2-27 09_22_45.xlsx')
        voucher_file, bank_file = find_data_files(self.dir)
        self.assertEqual(voucher_file, v)
        self.assertEqual(bank_file, b)

    def test_picks_latest(self):
        old = self._touch('银行存款辅助明细账_old.xlsx', mtime=1000000000)
        new = self._touch('银行存款辅助明细账_new.xlsx', mtime=2000000000)
        _, bank_file = find_data_files(self.dir)
        self.assertEqual(bank_file, new)
        self.assertNotEqual(bank_file, old)

    def test_skip_temp_and_unrelated(self):
        self._touch('~$凭证列表_temp.xlsx')
        self._touch('无关文件.xlsx')
        self._touch('说明.txt')
        voucher_file, bank_file = find_data_files(self.dir)
        self.assertIsNone(voucher_file)
        self.assertIsNone(bank_file)

    def test_nonexistent_dir(self):
        voucher_file, bank_file = find_data_files(os.path.join(self.dir, 'no_such_dir'))
        self.assertIsNone(voucher_file)
        self.assertIsNone(bank_file)

    def test_bank_priority_over_voucher(self):
        """同时含两类关键词时判为银行明细账（银行优先匹配）"""
        path = self._touch('凭证列表-银行存款辅助明细账.xlsx')
        voucher_file, bank_file = find_data_files(self.dir)
        self.assertEqual(bank_file, path)
        self.assertIsNone(voucher_file)


def build_voucher_file(path):
    """构造凭证列表 Excel：表头第 2 行，4 个项目 sheet"""
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        for proj in ['项目A', '项目B', '公司总部', '项目D']:
            pd.DataFrame({
                '期间': ['2026-02', '2026-02'],
                '凭证日期': ['2026-02-05', '2026-02-10'],
                '凭证号': [f'记-{proj}-1', f'记-{proj}-2'],
                '分录摘要': ['支付某燃气公司2月燃气费', '报销起诉项目A违约商户案件公告费'],
                '科目': ['100201', '660201'],
                '辅助核算': ['银行存款', '管理费用-办公费'],
                '借方金额': [0, 500],
                '贷方金额': [3200, 0],
            }).to_excel(writer, sheet_name=proj, index=False, startrow=1)


def build_bank_file(path):
    """构造银行存款辅助明细账 Excel：表头第 11 行，含各类业务场景"""
    rows = [
        # 科目编码, 科目名称, 日期, 核算账簿, 摘要, 借方本币, 贷方本币, 项目, 辅助核算, 凭证号
        ('10020101', '银行存款-基本户', '2026-02-03', '银行存款', '支付某燃气公司2月燃气费', 0, 3200, '项目A', '项目A', '记-1001'),
        ('100201', '银行存款', '2026-02-04', '银行存款', '收到待清算商户转账款', 0, 240000, '公司总部', '公司总部', '记-1002'),
        ('100201', '银行存款', '2026-02-05', '银行存款', '支付电梯年度维护费', 0, 8000, '项目B', '项目B', '记-1003'),
        ('100201', '银行存款', '2026-02-06', '银行存款', '转出本月未交增值税', 0, 50000, '公司总部', '公司总部', '记-1004'),
        ('100201', '银行存款', '2026-02-07', '银行存款', '收到税务局增值税留抵退税', 0, 12000, '公司总部', '公司总部', '记-1005'),
        ('100201', '银行存款', '2026-02-08', '银行存款', '建行一般户手续费', 0, 50, '公司总部', '公司总部', '记-1006'),
        ('100201', '银行存款', '2026-02-09', '银行存款', '待清算商户款项转账手续费', 0, 200, '公司总部', '公司总部', '记-1007'),
        ('100201', '银行存款', '2026-02-10', '银行存款', '项目B银行账户资金划拨至项目D', 0, 10000, '项目B', '项目B', '记-1008'),
        ('100201', '银行存款', '2026-02-11', '银行存款', '支付员工宿舍押金', 0, 5000, '公司总部', '公司总部', '记-1009'),
        ('100201', '银行存款', '2026-02-12', '银行存款', '确认202602月综合管理费', 0, 6000, '项目D', '项目D', '记-1010'),
        ('100201', '银行存款', '2026-02-13', '银行存款', '支付ipv6改造费', 0, 20000, '项目D', '项目D', '记-1011'),
        ('100201', '银行存款', '2026-02-14', '银行存款', '支付2026年1月员工社会保险费', 0, 15000, '公司总部', '公司总部', '记-1012'),
        ('100201', '银行存款', '2026-02-15', '银行存款', '支付办公用品采购(中性笔、纸)', 0, 800, '公司总部', '公司总部', '记-1013'),
        ('100201', '银行存款', '2026-02-16', '银行存款', '支付项目D绿化养护费', 0, 3000, '项目D', '项目D', '记-1014'),
        ('100201', '银行存款', '2026-02-17', '银行存款', '无法识别的一条神秘往来', 0, 1234, '项目A', '项目A', '记-1015'),
        ('100202', '银行存款-专户', '2026-02-18', '银行存款', '无关科目记录', 0, 99999, '项目A', '项目A', '记-1016'),
        ('100201', '银行存款', '2026-02-19', '银行存款', '结转损益', 0, 777, '公司总部', '公司总部', '记-1017'),
        ('100201', '银行存款', '2026-02-20', '银行存款', None, 0, 666, '项目A', '项目A', '记-项目A-2'),
    ]
    pd.DataFrame(rows, columns=['科目编码', '科目名称', '日期', '核算账簿', '摘要',
                                '借方本币', '贷方本币', '项目', '辅助核算', '凭证号']
                 ).to_excel(path, index=False, startrow=10)


class EndToEndCase(unittest.TestCase):
    """端到端管线测试（合成数据）"""

    def setUp(self):
        self._orig = ec._app_config
        ec._app_config = None
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.voucher = os.path.join(self.tmp.name, '凭证列表_2026-02-27.xlsx')
        self.bank = os.path.join(self.tmp.name, '2026.2.27银行存款辅助明细账.xlsx')
        self.output = os.path.join(self.tmp.name, '报告.xlsx')
        build_voucher_file(self.voucher)
        build_bank_file(self.bank)

    def tearDown(self):
        gc.collect()  # 释放 pandas/openpyxl 可能持有的文件句柄（Windows 删除需）
        self.tmp.cleanup()
        ec._app_config = self._orig

    def test_full_pipeline(self):
        clf = ExpenseClassifier()
        result = clf.run_pipeline(self.voucher, self.bank, self.output)
        self.assertEqual(result, self.output)
        self.assertTrue(os.path.exists(self.output))

        # 1. sheet 结构（6 个，错别字已修复）
        import openpyxl
        wb = openpyxl.load_workbook(self.output, read_only=True)
        self.assertEqual(wb.sheetnames,
                         ['财务收支摘要', '全量分类明细', '项目维度分析', '月度趋势分析', '分类标准快照', '待人工复核预警'])
        wb.close()

        # 2. 全量明细：16 行（18 行写入 - 100202 过滤 - 损益结转排除）
        details = pd.read_excel(self.output, sheet_name='全量分类明细')
        self.assertEqual(len(details), 16)
        by_voucher = details.set_index('凭证号')['费用类别'].to_dict()
        self.assertEqual(by_voucher['记-1005'], '经营收入')       # P0：留抵退税
        self.assertEqual(by_voucher['记-1004'], '税金调整（非费用）')
        self.assertEqual(by_voucher['记-1007'], 'POS刷卡手续费')  # 顺序敏感
        self.assertEqual(by_voucher['记-1015'], '未分类')
        self.assertEqual(by_voucher['记-项目A-2'], '行政费用')     # 凭证列表补摘要
        self.assertNotIn('记-1016', by_voucher)  # 100202 被过滤
        self.assertNotIn('记-1017', by_voucher)  # 损益结转被排除

        # 3. 财务收支摘要：全部 9 种类型都在（含 0 值行）
        summary = pd.read_excel(self.output, sheet_name='财务收支摘要')
        dims = dict(zip(summary['维度'], summary['金额']))
        self.assertAlmostEqual(dims['经营性支出 (EXPENSE)'], 50916.0)
        self.assertAlmostEqual(dims['经营性收入 (INFLOW)'], 252000.0)
        self.assertAlmostEqual(dims['税金划拨 (TAX_TRANSFER)'], 50000.0)
        self.assertAlmostEqual(dims['资金划拨 (TRANSFER)'], 10000.0)
        self.assertAlmostEqual(dims['资产/押金 (ASSET)'], 5000.0)
        self.assertAlmostEqual(dims['计提确认 (ADJUSTMENT)'], 6000.0)
        self.assertAlmostEqual(dims['未归类/规则外 (OTHER)'], 1234.0)
        self.assertAlmostEqual(dims['资本性支出 (CAPITAL)'], 0.0)
        self.assertAlmostEqual(dims['保证金退回 (OUTFLOW)'], 0.0)

        # 4. 待复核预警 sheet 含未分类记录
        warn = pd.read_excel(self.output, sheet_name='待人工复核预警')
        self.assertEqual(len(warn), 1)
        self.assertEqual(warn.iloc[0]['凭证号'], '记-1015')

        # 5. 分类标准快照含 8 个辅助类别
        rules_snapshot = pd.read_excel(self.output, sheet_name='分类标准快照')
        cats = set(rules_snapshot['类别'])
        for cat in ['税金调整（非费用）', '工会经费', '未知款项退回', '营业外支出']:
            self.assertIn(cat, cats)

    def test_project_and_monthly_sheets(self):
        clf = ExpenseClassifier()
        clf.run_pipeline(self.voucher, self.bank, self.output)
        proj = pd.read_excel(self.output, sheet_name='项目维度分析', index_col=0)
        self.assertIn('项目A', proj.index)
        monthly = pd.read_excel(self.output, sheet_name='月度趋势分析', index_col=0)
        self.assertIn('2026-02', monthly.index)

    def test_header_rows_from_config(self):
        """表头行走配置：错配置会读不到表头（验证配置生效）"""
        class _Stub:
            FILE_FORMAT_CONFIG = {'voucher_header_row': 0, 'bank_header_row': 0}

        ec._app_config = _Stub
        try:
            clf = ExpenseClassifier()
            bank_df = clf.read_bank_data(self.bank)
            # header=0 时第一行是数据不是表头，必要列缺失 -> 记录数为 0 或被过滤
            self.assertTrue(bank_df is None or len(bank_df) == 0 or '科目编码' not in bank_df.columns)
        finally:
            ec._app_config = self._orig

    def test_run_pipeline_progress_and_cancel(self):
        """run_pipeline 回调：进度单调推进；cancel_check 返回 True 时中止且不产出报告"""
        events = []
        clf = ExpenseClassifier()
        result = clf.run_pipeline(self.voucher, self.bank, self.output,
                                  progress_callback=lambda step, pct: events.append(pct))
        self.assertEqual(result, self.output)
        self.assertEqual(events[0], 10)
        self.assertEqual(events[-1], 100)
        self.assertEqual(events, sorted(events))  # 进度单调不减

        # 取消：第一步即中止，不生成输出文件
        clf2 = ExpenseClassifier()
        cancelled_output = os.path.join(self.tmp.name, '已取消.xlsx')
        result2 = clf2.run_pipeline(self.voucher, self.bank, cancelled_output,
                                    cancel_check=lambda: True)
        self.assertIsNone(result2)
        self.assertFalse(os.path.exists(cancelled_output))


class GuiIntegrationCase(unittest.TestCase):
    """GUI 与共用识别逻辑的集成测试"""

    def test_gui_uses_shared_finder(self):
        try:
            import expense_classification_gui as gui
        except Exception as e:
            self.skipTest(f'tkinter 环境不可用: {e}')
        self.assertTrue(hasattr(gui, 'find_data_files'))
        src = open(gui.__file__, encoding='utf-8').read()
        self.assertIn('find_data_files(source_dir)', src)
        self.assertNotIn("if '凭证列表' in filename", src)

    def test_gui_thread_safety_and_pipeline(self):
        """B1/B6/E3 回归：GUI 统一走 run_pipeline、经 queue 回主线程更新、
        子线程不得直接调用 root.update()"""
        try:
            import expense_classification_gui as gui
        except Exception as e:
            self.skipTest(f'tkinter 环境不可用: {e}')
        src = open(gui.__file__, encoding='utf-8').read()
        self.assertIn('run_pipeline', src)          # E3：统一管线
        self.assertIn('queue.Queue()', src)          # B1：队列桥接
        self.assertIn('cancel_check', src)           # B6：真取消
        self.assertNotIn('self.root.update()', src)  # B1：子线程禁重入事件循环

    def test_gui_smoke_lifecycle(self):
        """GUI 冒烟：隐藏窗口实例化 + 处理链路（经 run_pipeline 回调更新状态）"""
        try:
            import tkinter as tk
            import expense_classification_gui as gui_module
            from expense_classification_gui import ExpenseClassifierGUI
        except Exception as e:
            self.skipTest(f'tkinter 环境不可用: {e}')
        # 打桩 messagebox，避免终止消息弹窗阻塞测试
        shown = []
        orig_showinfo = gui_module.messagebox.showinfo
        gui_module.messagebox.showinfo = lambda *a, **k: shown.append(a)
        root = tk.Tk()
        root.withdraw()
        try:
            app = ExpenseClassifierGUI(root)
            self.assertEqual(app.status_var.get(), '就绪')
            app.processing = True
            app.ui_queue.put(('progress', '分类费用...', 60))
            app.ui_queue.put(('done', '报告.xlsx'))
            app._poll_ui_queue()
            self.assertEqual(app.progress_var.get(), 100)
            self.assertFalse(app.processing)
            self.assertTrue(shown)  # 成功提示已触发（打桩捕获）
        finally:
            gui_module.messagebox.showinfo = orig_showinfo
            root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)

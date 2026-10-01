#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
费用分类汇总系统

功能：以凭证列表和银行存款辅助明细账为数据源，按照指定分类标准进行费用分类汇总
"""

import pandas as pd
import re
import os
from datetime import datetime
import logging

# 配置加载：优先读取外部 config.py（gitignore，仅本机存在），缺失时使用内置默认值，
# 保证开源克隆在无 config.py 时开箱即用
try:
    import config as _app_config
except Exception:
    _app_config = None

# 分类规则单一权威源（2026-10-01 数据化重构）：
# 28 类规则 dict 已从本文件迁至 classification_rules.json，
# 由 rules_loader 加载；config.py 的 CLASSIFICATION_RULES 覆盖语义不变。
from rules_loader import load_classification_rules


def _cfg(dotted_key, default):
    """按 'SECTION.key' 路径从 config.py 读取配置，任何一级缺失即回退默认值"""
    if _app_config is None:
        return default
    try:
        value = _app_config
        for part in dotted_key.split('.'):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = getattr(value, part, None)
            if value is None:
                return default
        return value
    except Exception:
        return default


# 配置日志：文件 DEBUG 全量、控制台 INFO 简洁（文件名/级别/开关可由 LOGGING_CONFIG 覆盖）。
# handler 只挂模块 logger 且 propagate=False，避免与 root logger 叠加造成控制台重复输出。
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.propagate = False
if not logger.handlers:  # 重复导入（pytest/unittest 双通道）时不重复挂 handler
    _file_handler = logging.FileHandler(
        _cfg('LOGGING_CONFIG.file', 'expense_classification.log'), encoding='utf-8')
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(module)s - %(message)s'))
    logger.addHandler(_file_handler)

    if _cfg('LOGGING_CONFIG.console_output', True):
        _console_handler = logging.StreamHandler()
        _console_level = str(_cfg('LOGGING_CONFIG.level', 'INFO')).upper()
        _console_handler.setLevel(getattr(logging, _console_level, logging.INFO))
        _console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(_console_handler)

class ExpenseClassifier:
    def __init__(self):
        """初始化费用分类器"""
        self.voucher_data = None
        self.bank_data = None
        self.processed_data = None
        self.summary_result = None
    
    def read_voucher_data(self, file_path, sheet_names=None):
        """读取凭证列表数据（支持多工作表）
        
        Args:
            file_path: 凭证列表Excel文件路径
            sheet_names: 要读取的工作表名称列表，默认读取所有工作表
        
        Returns:
            处理后的凭证数据（合并所有工作表）
        """
        try:
            logger.info(f"开始读取凭证列表文件: {file_path}")
            
            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.error(f"凭证列表文件不存在: {file_path}")
                return None
            
            # 检查文件扩展名
            if not file_path.endswith('.xlsx') and not file_path.endswith('.xls'):
                logger.error(f"凭证列表文件格式不正确，仅支持Excel文件: {file_path}")
                return None
            
            # 读取所有工作表
            try:
                # 获取所有工作表名称
                xl_file = pd.ExcelFile(file_path)
                all_sheet_names = xl_file.sheet_names
                logger.info(f"发现 {len(all_sheet_names)} 个工作表: {all_sheet_names}")
                
                # 如果指定了工作表名称，则使用指定的；否则读取所有工作表
                if sheet_names is None:
                    sheets_to_read = all_sheet_names
                else:
                    sheets_to_read = [s for s in sheet_names if s in all_sheet_names]
                    if len(sheets_to_read) < len(sheet_names):
                        missing_sheets = set(sheet_names) - set(all_sheet_names)
                        logger.warning(f"以下指定的工作表不存在: {missing_sheets}")

                # read_all_sheets=False 时仅保留 expected_sheets 中的工作表
                if not _cfg('VOUCHER_SHEET_CONFIG.read_all_sheets', True):
                    expected_sheets = _cfg('VOUCHER_SHEET_CONFIG.expected_sheets', [])
                    if expected_sheets:
                        sheets_to_read = [s for s in sheets_to_read if s in expected_sheets]
                        logger.info(f"按配置仅读取预期工作表: {sheets_to_read}")
                
                logger.info(f"将读取 {len(sheets_to_read)} 个工作表: {sheets_to_read}")
                
            except Exception as e:
                logger.error(f"获取工作表信息失败: {str(e)}")
                return None
            
            # 处理列名映射 - 实际的列名与期望的列名可能不同
            column_mapping = {
                '期间': '期间',
                '凭证日期': '凭证日期',
                '凭证号': '凭证号',
                '分录摘要': '摘要',  # 实际列名是"分录摘要"
                '科目': '科目编码',  # 实际列名是"科目"
                '辅助核算': '科目名称',  # 使用辅助核算作为科目名称
                '借方金额': '借方本币',  # 实际列名是"借方金额"
                '贷方金额': '贷方本币'   # 实际列名是"贷方金额"
            }
            
            # 读取并合并所有工作表
            all_data = []
            for sheet_name in sheets_to_read:
                try:
                    logger.info(f"正在读取工作表: {sheet_name}")
                    # 表头行号可通过 config.py 的 FILE_FORMAT_CONFIG.voucher_header_row 配置
                    voucher_header_row = _cfg('FILE_FORMAT_CONFIG.voucher_header_row', 1)
                    df = pd.read_excel(file_path, sheet_name=sheet_name, header=voucher_header_row)
                    
                    # 添加工作表来源标识
                    df['工作表来源'] = sheet_name
                    
                    # 重命名列
                    for old_col, new_col in column_mapping.items():
                        if old_col in df.columns:
                            df = df.rename(columns={old_col: new_col})
                            logger.debug(f"[{sheet_name}] 重命名列: {old_col} -> {new_col}")
                    
                    # 确保必要列存在
                    required_columns = ['期间', '凭证日期', '凭证号', '摘要', '贷方本币']
                    for col in required_columns:
                        if col not in df.columns:
                            logger.warning(f"[{sheet_name}] 必要列 {col} 不存在，创建空列")
                            df[col] = None
                    
                    # 过滤掉空行（期间为空的行）
                    df = df[df['期间'].notna()]
                    
                    # 转换金额列为数值类型
                    for col in ['借方本币', '贷方本币']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                    
                    if len(df) > 0:
                        all_data.append(df)
                        logger.info(f"[{sheet_name}] 成功读取 {len(df)} 条记录")
                    else:
                        logger.warning(f"[{sheet_name}] 工作表中未找到有效数据")
                        
                except Exception as e:
                    logger.error(f"读取工作表 {sheet_name} 失败: {str(e)}")
                    continue
            
            # 合并所有工作表数据
            if len(all_data) > 0:
                merged_df = pd.concat(all_data, ignore_index=True)

                # 数据去重：去重键可通过 config.py 的 VOUCHER_SHEET_CONFIG.deduplication.key_columns 配置
                before_dedup = len(merged_df)
                dedup_keys = [c for c in _cfg('VOUCHER_SHEET_CONFIG.deduplication.key_columns',
                                              ['期间', '凭证日期', '凭证号', '摘要', '贷方本币'])
                              if c in merged_df.columns]
                if dedup_keys:
                    merged_df = merged_df.drop_duplicates(subset=dedup_keys, keep='first')
                after_dedup = len(merged_df)
                
                if before_dedup != after_dedup:
                    logger.info(f"数据去重: 从 {before_dedup} 条记录中移除 {before_dedup - after_dedup} 条重复记录")
                
                self.voucher_data = merged_df
                logger.info(f"成功读取凭证列表数据，共合并 {len(sheets_to_read)} 个工作表，{len(merged_df)} 条记录")
                logger.info(f"工作表来源分布:\n{merged_df['工作表来源'].value_counts().to_string()}")
                logger.info(f"列名: {merged_df.columns.tolist()}")
                return merged_df
            else:
                logger.warning("凭证列表文件中未找到有效数据")
                return None
                
        except Exception as e:
            logger.error(f"读取凭证列表失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def read_bank_data(self, file_path, target_account=None):
        """读取银行存款辅助明细账数据（支持多项目提取）

        Args:
            file_path: 银行存款辅助明细账Excel文件路径
            target_account: 目标科目编码，默认取 config.py 的 BANK_ACCOUNT_CONFIG.target_account（内置默认'100201'，银行存款）

        Returns:
            处理后的银行数据（包含所有项目）
        """
        try:
            if target_account is None:
                target_account = _cfg('BANK_ACCOUNT_CONFIG.target_account', '100201')
            logger.info(f"开始读取银行存款辅助明细账文件: {file_path}")
            logger.info(f"目标科目编码: {target_account}")
            
            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.error(f"银行存款辅助明细账文件不存在: {file_path}")
                return None
            
            # 检查文件扩展名
            if not file_path.endswith('.xlsx') and not file_path.endswith('.xls'):
                logger.error(f"银行存款辅助明细账文件格式不正确，仅支持Excel文件: {file_path}")
                return None
            
            # 读取所有工作表
            try:
                xl_file = pd.ExcelFile(file_path)
                all_sheet_names = xl_file.sheet_names
                logger.info(f"发现 {len(all_sheet_names)} 个工作表: {all_sheet_names}")
            except Exception as e:
                logger.error(f"获取工作表信息失败: {str(e)}")
                return None
            
            # 处理列名映射 - 实际的列名与期望的列名可能不同
            column_mapping = {
                '科目编码': '科目编码',
                '科目名称': '科目名称',
                '日期': '凭证日期',
                '核算账簿': '核算账簿',
                '摘要': '摘要',
                '借方本币': '借方本币',
                '贷方本币': '贷方本币',
                '项目': '项目标识',  # 辅助核算项目
                '辅助核算': '辅助核算'
            }
            
            # 读取并合并所有工作表
            all_data = []
            project_stats = {}  # 统计各项目数据量
            
            for sheet_name in all_sheet_names:
                try:
                    logger.info(f"正在读取工作表: {sheet_name}")
                    # 表头行号可通过 config.py 的 FILE_FORMAT_CONFIG.bank_header_row 配置
                    bank_header_row = _cfg('FILE_FORMAT_CONFIG.bank_header_row', 10)
                    df = pd.read_excel(file_path, sheet_name=sheet_name, header=bank_header_row)
                    
                    # 添加工作表来源标识
                    df['工作表来源'] = sheet_name
                    
                    # 重命名列
                    for old_col, new_col in column_mapping.items():
                        if old_col in df.columns:
                            df = df.rename(columns={old_col: new_col})
                            logger.debug(f"[{sheet_name}] 重命名列: {old_col} -> {new_col}")
                    
                    # 确保必要列存在
                    required_columns = ['科目编码', '科目名称', '凭证日期', '摘要', '贷方本币']
                    for col in required_columns:
                        if col not in df.columns:
                            logger.warning(f"[{sheet_name}] 必要列 {col} 不存在，创建空列")
                            df[col] = None
                    
                    # 添加期间列（从凭证日期提取）
                    if '凭证日期' in df.columns and '期间' not in df.columns:
                        df['期间'] = pd.to_datetime(df['凭证日期'], errors='coerce').dt.strftime('%Y-%m')
                    
                    # 添加凭证号列（从摘要提取或创建）
                    if '凭证号' not in df.columns:
                        df['凭证号'] = None
                    
                    # 过滤掉空行（科目编码为空的行）
                    df = df[df['科目编码'].notna()]
                    
                    # 转换金额列为数值类型
                    for col in ['借方本币', '贷方本币']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
                    
                    # 筛选目标科目数据（支持100201及其子科目）
                    if target_account:
                        # 匹配目标科目编码开头的记录（如100201匹配10020101、10020102等）
                        mask = df['科目编码'].astype(str).str.startswith(str(target_account))
                        df_filtered = df[mask].copy()
                        
                        # 统计各项目数据量
                        if '辅助核算' in df_filtered.columns:
                            projects = df_filtered['辅助核算'].value_counts().to_dict()
                            for proj, count in projects.items():
                                if proj not in project_stats:
                                    project_stats[proj] = {'count': 0, 'amount': 0}
                                project_stats[proj]['count'] += count
                                project_stats[proj]['amount'] += df_filtered[df_filtered['辅助核算'] == proj]['贷方本币'].sum()
                        
                        logger.info(f"[{sheet_name}] 目标科目 {target_account} 数据: {len(df_filtered)} 条")
                        
                        if len(df_filtered) > 0:
                            all_data.append(df_filtered)
                    else:
                        if len(df) > 0:
                            all_data.append(df)
                            logger.info(f"[{sheet_name}] 成功读取 {len(df)} 条记录")
                        else:
                            logger.warning(f"[{sheet_name}] 工作表中未找到有效数据")
                        
                except Exception as e:
                    logger.error(f"读取工作表 {sheet_name} 失败: {str(e)}")
                    continue
            
            # 合并所有工作表数据
            if len(all_data) > 0:
                merged_df = pd.concat(all_data, ignore_index=True)
                
                # 数据去重：基于关键字段去重
                before_dedup = len(merged_df)
                dedup_columns = ['期间', '凭证日期', '科目编码', '摘要', '贷方本币']
                # 只使用存在的列进行去重
                dedup_columns = [col for col in dedup_columns if col in merged_df.columns]
                merged_df = merged_df.drop_duplicates(subset=dedup_columns, keep='first')
                after_dedup = len(merged_df)
                
                if before_dedup != after_dedup:
                    logger.info(f"数据去重: 从 {before_dedup} 条记录中移除 {before_dedup - after_dedup} 条重复记录")
                
                # 项目完整性校验
                logger.info("=" * 60)
                logger.info("银行存款科目项目完整性统计:")
                logger.info("=" * 60)
                for proj, stats in sorted(project_stats.items()):
                    logger.info(f"项目: {proj:<30} 记录数: {stats['count']:>5}  金额合计: {stats['amount']:>15,.2f}")
                logger.info("=" * 60)
                logger.info(f"项目总数: {len(project_stats)}")
                
                # 检查是否包含预期的项目（预期项目名可通过 config.py 的 BANK_ACCOUNT_CONFIG.expected_projects 配置）
                expected_projects = _cfg('BANK_ACCOUNT_CONFIG.expected_projects', ['项目A', '项目B', '公司总部', '项目D'])
                strict_mode = _cfg('BANK_ACCOUNT_CONFIG.project_validation.strict_mode', False)
                found_projects = set(project_stats.keys())
                missing_projects = set(expected_projects) - found_projects
                
                if missing_projects:
                    msg = f"警告: 以下预期项目未找到数据: {missing_projects}"
                    if strict_mode:
                        logger.error(msg + "（strict_mode 已开启）")
                    else:
                        logger.warning(msg)
                else:
                    logger.info("✓ 所有预期项目数据均已提取")
                
                self.bank_data = merged_df
                logger.info(f"成功读取银行存款辅助明细账数据，共合并 {len(all_sheet_names)} 个工作表，{len(merged_df)} 条记录")
                logger.info(f"工作表来源分布:\n{merged_df['工作表来源'].value_counts().to_string()}")
                logger.info(f"列名: {merged_df.columns.tolist()}")
                return merged_df
            else:
                logger.warning("银行存款辅助明细账文件中未找到有效数据")
                return None
                
        except Exception as e:
            logger.error(f"读取银行存款辅助明细账失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def integrate_data(self):
        """整合两个数据源
        
        Returns:
            整合后的数据
        """
        try:
            logger.info("开始整合数据源")
            
            # 检查数据源状态
            if self.bank_data is None and self.voucher_data is None:
                logger.error("两个数据源都未读取，无法整合")
                return None
            
            # 以银行存款辅助明细账为主要数据源
            if self.bank_data is not None:
                logger.info("以银行存款辅助明细账为主要数据源")
                integrated_df = self.bank_data.copy()
                
                # 列名已经在read_bank_data中标准化，无需再次重命名
                logger.info(f"银行数据列名: {integrated_df.columns.tolist()}")
            else:
                # 如果银行数据不存在，使用凭证列表数据
                logger.warning("银行存款辅助明细账数据不存在，使用凭证列表数据")
                if self.voucher_data is None:
                    logger.error("凭证数据也不存在，无法整合")
                    return None
                integrated_df = self.voucher_data.copy()
            
            # 确保必要列存在
            required_columns = ['期间', '凭证日期', '凭证号', '摘要', '贷方本币', '科目编码', '科目名称']
            for col in required_columns:
                if col not in integrated_df.columns:
                    integrated_df[col] = None
            
            # 使用凭证列表数据补充科目和摘要信息
            if self.voucher_data is not None:
                logger.info("使用凭证列表数据补充科目和摘要信息")
                # 创建凭证号到信息的映射
                voucher_map = {}
                for idx, row in self.voucher_data.iterrows():
                    voucher_no = str(row.get('凭证号', '')).strip()
                    if voucher_no:
                        voucher_map[voucher_no] = {
                            '摘要': row.get('摘要', ''),
                            '科目编码': row.get('科目编码', ''),
                            '科目名称': row.get('科目名称', '')
                        }
                
                # 补充信息
                for idx, row in integrated_df.iterrows():
                    voucher_no = str(row.get('凭证号', '')).strip()
                    if voucher_no in voucher_map:
                        # 补充缺失的信息
                        if pd.isna(row.get('摘要')) or str(row.get('摘要', '')).strip() == '':
                            integrated_df.at[idx, '摘要'] = voucher_map[voucher_no]['摘要']
                        if pd.isna(row.get('科目编码')) or str(row.get('科目编码', '')).strip() == '':
                            integrated_df.at[idx, '科目编码'] = voucher_map[voucher_no]['科目编码']
                        if pd.isna(row.get('科目名称')) or str(row.get('科目名称', '')).strip() == '':
                            integrated_df.at[idx, '科目名称'] = voucher_map[voucher_no]['科目名称']
            
            # 数据质量检查
            logger.info("执行数据质量检查")
            # 检查缺失值
            missing_summary = integrated_df['摘要'].isna().sum()
            missing_voucher = integrated_df['凭证号'].isna().sum()
            missing_amount = integrated_df['贷方本币'].isna().sum()
            
            logger.info(f"数据质量检查结果:")
            logger.info(f"- 缺失摘要: {missing_summary}条")
            logger.info(f"- 缺失凭证号: {missing_voucher}条")
            logger.info(f"- 缺失金额: {missing_amount}条")
            
            self.processed_data = integrated_df
            logger.info(f"数据整合完成，共{len(integrated_df)}条记录")
            return integrated_df
        except Exception as e:
            logger.error(f"数据整合失败: {str(e)}")
            return None
    
    def clean_data(self):
        """数据清洗和标准化处理
        
        Returns:
            清洗后的标准化数据
        """
        try:
            if self.processed_data is None:
                logger.warning("数据未整合，无法清洗")
                return None
            
            logger.info("开始数据清洗和标准化处理")
            cleaned_df = self.processed_data.copy()
            
            # 1. 数据完整性检查
            logger.info("执行数据完整性检查")
            # 统计关键字段缺失情况
            missing_summary = cleaned_df['摘要'].isna().sum()
            missing_voucher = cleaned_df['凭证号'].isna().sum()
            missing_amount = cleaned_df['贷方本币'].isna().sum()
            
            logger.info(f"数据完整性检查结果:")
            logger.info(f"- 缺失摘要: {missing_summary}条")
            logger.info(f"- 缺失凭证号: {missing_voucher}条")
            logger.info(f"- 缺失金额: {missing_amount}条")
            
            # 处理空值
            cleaned_df = cleaned_df.dropna(subset=['摘要', '贷方本币'], how='all')
            
            # 2. 标准化金额字段
            for col in ['借方本币', '贷方本币']:
                if col in cleaned_df.columns:
                    try:
                        # 直接尝试转换为数值类型
                        numeric_values = pd.to_numeric(cleaned_df[col], errors='coerce')
                        
                        # 处理转换失败的情况
                        if numeric_values.isna().any():
                            # 对于转换失败的，尝试字符串处理
                            string_values = cleaned_df[col].astype(str)
                            # 去除非数字字符（保留数字、小数点和负号）
                            cleaned_df[col] = string_values.apply(lambda x: re.sub(r'[^\d.-]', '', x))
                            # 再次转换为数值类型
                            cleaned_df[col] = pd.to_numeric(cleaned_df[col], errors='coerce')
                        else:
                            cleaned_df[col] = numeric_values
                        
                        # 填充空值为0
                        cleaned_df[col] = cleaned_df[col].fillna(0)
                        
                        # 验证金额范围，识别异常金额
                        if cleaned_df[col].min() < 0:
                            logger.warning(f"发现负数金额: {cleaned_df[col].min()}")
                        if cleaned_df[col].max() > 10000000:
                            logger.warning(f"发现大额金额: {cleaned_df[col].max()}")
                        
                        # 金额一致性检查
                        if col == '贷方本币':
                            total_amount = cleaned_df[col].sum()
                            logger.info(f"{col}总金额: {total_amount:.2f}")
                    except Exception as e:
                        logger.error(f"处理金额字段 {col} 时出错: {str(e)}")
                        # 出错时填充为0
                        cleaned_df[col] = 0
            
            # 3. 数据格式检查
            logger.info("执行数据格式检查")
            # 检查凭证号格式
            if '凭证号' in cleaned_df.columns:
                invalid_voucher_no = cleaned_df[~cleaned_df['凭证号'].astype(str).str.match(r'^记-\d+$', na=False)]
                if not invalid_voucher_no.empty:
                    logger.warning(f"发现{len(invalid_voucher_no)}条凭证号格式异常")
            
            # 检查日期格式
            for col in ['凭证日期', '期间']:
                if col in cleaned_df.columns:
                    invalid_dates = cleaned_df[cleaned_df[col].isna()]
                    if not invalid_dates.empty:
                        logger.warning(f"发现{len(invalid_dates)}条{col}格式异常")
            
            # 4. 数据一致性检查
            logger.info("执行数据一致性检查")
            # 检查借贷平衡（如果有借方和贷方字段）
            if '借方本币' in cleaned_df.columns and '贷方本币' in cleaned_df.columns:
                debit_total = cleaned_df['借方本币'].sum()
                credit_total = cleaned_df['贷方本币'].sum()
                tolerance = _cfg('DATA_VALIDATION_CONFIG.amount_reconciliation.tolerance', 0.01)
                logger.info(f"借贷平衡检查: 借方合计={debit_total:.2f}, 贷方合计={credit_total:.2f}")
                if abs(debit_total - credit_total) > tolerance:
                    logger.warning(f"借贷不平衡，差额: {abs(debit_total - credit_total):.2f}")
            
            # 5. 数据范围检查
            logger.info("执行数据范围检查")
            # 检查金额范围
            if '贷方本币' in cleaned_df.columns:
                min_amount = cleaned_df['贷方本币'].min()
                max_amount = cleaned_df['贷方本币'].max()
                logger.info(f"金额范围: 最小值={min_amount:.2f}, 最大值={max_amount:.2f}")
                
                # 识别异常金额
                abnormal_amounts = cleaned_df[(cleaned_df['贷方本币'] < 0) | (cleaned_df['贷方本币'] > 1000000)]
                if not abnormal_amounts.empty:
                    logger.warning(f"发现{len(abnormal_amounts)}条异常金额记录")
            
            # 6. 标准化摘要字段（fillna 前置：pandas 3 的 astype(str) 保留 NaN，
            #    直接 strip 会对 float NaN 报 AttributeError）
            if '摘要' in cleaned_df.columns:
                cleaned_df['摘要'] = cleaned_df['摘要'].fillna('').astype(str).str.strip()
            
            # 7. 标准化日期字段
            for col in ['凭证日期', '期间']:
                if col in cleaned_df.columns:
                    cleaned_df[col] = pd.to_datetime(cleaned_df[col], errors='coerce')
            
            # 8. 去除重复记录（去重键可通过 config.py 的 CLEAN_CONFIG.dedup_key_columns 配置）
            clean_dedup_keys = [c for c in _cfg('CLEAN_CONFIG.dedup_key_columns', ['凭证号', '摘要', '贷方本币'])
                                if c in cleaned_df.columns]
            if clean_dedup_keys:
                cleaned_df = cleaned_df.drop_duplicates(subset=clean_dedup_keys, keep='first')
            
            # 9. 筛选有效的费用记录（贷方本币>0）
            cleaned_df = cleaned_df[cleaned_df['贷方本币'] > 0]
            
            # 10. 排除非实际支出的记录（仅初步过滤，具体分类交由 v2.0 逻辑）
            logger.info("执行初步过滤")
            
            # 定义绝对排除的关键字模式（如结转损益等完全不涉及现金流的）
            # 可通过 config.py 的 CLASSIFICATION_CONFIG.exclusion_patterns 配置
            absolute_exclude_patterns = _cfg('CLASSIFICATION_CONFIG.exclusion_patterns',
                                             [r'损益结转', r'结转.*损益', r'重新计提', r'期末结转'])
            
            # 创建排除标记
            original_count = len(cleaned_df)
            exclude_mask = pd.Series([False] * len(cleaned_df), index=cleaned_df.index)
            
            for pattern in absolute_exclude_patterns:
                mask = cleaned_df['摘要'].str.contains(pattern, case=False, na=False, regex=True)
                exclude_mask = exclude_mask | mask
            
            # 应用排除规则
            cleaned_df = cleaned_df[~exclude_mask]
            logger.info(f"初步过滤掉 {original_count - len(cleaned_df)} 条记录")
            
            self.processed_data = cleaned_df
            logger.info(f"数据清洗完成，共{len(cleaned_df)}条有效费用记录")
            return cleaned_df
        except Exception as e:
            logger.error(f"数据清洗失败: {str(e)}")
            return None
    
    def get_classification_rules(self):
        """获取费用分类映射规则（v2.x 数据化版）

        规则单一权威源为包内 classification_rules.json，由 rules_loader 加载；
        config.py 的 CLASSIFICATION_RULES 仍可覆盖已有类别（按字段 update）
        或追加新类别（追加在末尾，注意顺序敏感的遮蔽关系），覆盖机制与
        数据化前完全一致。修改规则请编辑 JSON 或 config，无需改本源码。
        """
        return load_classification_rules(config_module=_app_config)
    
    def classify_expenses(self):
        """费用分类逻辑，基于摘要和科目信息的关键字匹配
        
        Returns:
            分类后的费用数据
        """
        try:
            if self.processed_data is None:
                logger.warning("数据未清洗，无法分类")
                return None
            
            logger.info("开始费用分类处理")
            classified_df = self.processed_data.copy()
            rules = self.get_classification_rules()
            
            # 添加分类结果列
            classified_df['费用类别'] = '未分类'
            classified_df['匹配关键字'] = ''
            
            # 遍历每条记录进行分类
            for idx, row in classified_df.iterrows():
                summary = str(row.get('摘要', '')).lower()
                account_name = str(row.get('科目名称', '')).lower()
                account_code = str(row.get('科目编码', '')).strip()
                voucher_no = str(row.get('凭证号', '')).strip()
                amount = row.get('贷方本币', 0)
                matched_category = '未分类'
                matched_keywords = []
                
                logger.debug(f"开始分类第{idx}条记录: 凭证号={voucher_no}, 金额={amount}, 摘要={summary[:50]}...")
                
                # 检查项目标识（项目名可通过 config.py 的 PROJECT_SPECIAL_RULES 配置）
                # 注意：summary/account_name 已小写化，项目名比较必须同步小写
                clean_admin_project = _cfg('PROJECT_SPECIAL_RULES.clean_to_admin_project', '项目A')
                greening_property_project = _cfg('PROJECT_SPECIAL_RULES.greening_to_property_project', '项目D')
                is_clean_admin_project = clean_admin_project.lower() in summary or clean_admin_project.lower() in account_name
                is_greening_property_project = greening_property_project.lower() in summary or greening_property_project.lower() in account_name

                # 增值税转出/税额调整属税金划拨（非费用）；退税流入除外（应归经营收入）
                # 关键词列表可通过 config.py 的 SPECIAL_RULES_CONFIG 配置
                vat_transfer_keywords = _cfg('SPECIAL_RULES_CONFIG.vat_transfer_keywords',
                                             ['增值税转出', '进项税额转出', '转出未交', '未交增值税', '预缴税款调拨', '税额调整', '税费调整'])
                vat_adjust_markers = _cfg('SPECIAL_RULES_CONFIG.vat_adjust_markers', ['转出', '调拨', '调整'])
                vat_transfer_hit = '退税' not in summary and (
                    any(k in summary for k in vat_transfer_keywords)
                    or ('增值税' in summary and any(k in summary for k in vat_adjust_markers))
                )

                # 特殊处理：押金/保证金类——含退回语义的归保证金退回（OUTFLOW），
                # 否则归押金类（ASSET 资产类，非费用）。关键词可通过 SPECIAL_RULES_CONFIG 配置
                deposit_keywords = _cfg('SPECIAL_RULES_CONFIG.deposit_keywords', ['押金', '保证金', '诚意金', '租赁押金'])
                refund_keywords = _cfg('SPECIAL_RULES_CONFIG.refund_keywords', ['退回', '退还'])
                has_deposit = any(keyword in summary or keyword in account_name for keyword in deposit_keywords)
                has_refund = any(keyword in summary for keyword in refund_keywords)
                if has_deposit and has_refund:
                    matched_category = '保证金退回'
                    matched_keywords.append('保证金退回')
                    logger.debug(f"特殊分类: 押金/保证金退回 -> 保证金退回")
                elif has_deposit:
                    matched_category = '押金类（非费用）'
                    matched_keywords.append('押金类')
                    logger.debug(f"特殊分类: 押金支付 -> 押金类（非费用）")
                # 特殊处理：增值税转出等税金调整（税金划拨，非费用）
                elif vat_transfer_hit:
                    matched_category = '税金调整（非费用）'
                    matched_keywords.append('增值税转出')
                    logger.debug(f"特殊分类: 增值税转出/调整 -> 税金调整（非费用）")
                # 特殊处理：信息系统成本相关费用（关键词可配置；比较时统一小写，修复 'IT' 永不命中）
                elif any(keyword.lower() in summary or keyword.lower() in account_name
                         for keyword in _cfg('SPECIAL_RULES_CONFIG.info_system_keywords',
                                             ['网络', '网络使用费', '硬件', '硬件维护', '软件', '软件维护', '系统', '信息系统', 'IT', '信息化', '弱电布线', '宽带费', '客流', '软件许可', '备件采购', '布线'])):
                    matched_category = '信息系统成本'
                    matched_keywords.append('信息系统相关费用')
                    logger.debug(f"特殊分类: 信息系统相关费用 -> 信息系统成本")
                # 特殊处理：项目A项目的保洁服务费
                elif is_clean_admin_project and ('保洁' in summary or '保洁' in account_name):
                    matched_category = '行政费用'
                    matched_keywords.append('项目A-保洁服务费')
                    logger.debug(f"特殊分类: 项目A项目保洁服务费 -> 行政费用")
                # 特殊处理：项目D项目的绿化养护费用
                elif is_greening_property_project and ('绿化' in summary or '绿化' in account_name or '养护' in summary or '养护' in account_name):
                    matched_category = '物业成本'
                    matched_keywords.append('项目D-绿化养护费')
                    logger.debug(f"特殊分类: 项目D项目绿化养护费用 -> 物业成本")
                # 特殊处理：涉及改造但不属于工程改造基金范畴的费用
                elif '改造' in summary and '工程改造基金' not in summary and '改造基金' not in summary:
                    matched_category = '工程成本'
                    matched_keywords.append('改造-工程成本')
                    logger.debug(f"特殊分类: 改造费用(非基金) -> 工程成本")
                # 特殊处理：确认类租金和管理费（属于计提，非实际支出）
                elif '确认' in summary and ('租金' in summary or '管理费' in summary):
                    # 这类记录属于计提/确认，不是实际支出，标记为不计入费用
                    matched_category = '计提确认类'
                    matched_keywords.append('确认-计提类')
                    logger.debug(f"特殊分类: 确认类租金/管理费 -> 计提确认类（非实际支出）")
                else:
                    # 1. 首先基于摘要分类
                    for category, rule in rules.items():
                        keywords = rule.get('keywords', [])
                        exclude_keywords = rule.get('exclude_keywords', [])
                        
                        # 检查是否包含排除关键字
                        exclude_match = False
                        for exclude_keyword in exclude_keywords:
                            if exclude_keyword.lower() in summary:
                                exclude_match = True
                                logger.debug(f"排除分类 {category}: 包含排除关键字 '{exclude_keyword}'")
                                break
                        
                        if exclude_match:
                            continue
                        
                        # 检查是否包含关键字
                        for keyword in keywords:
                            if keyword.lower() in summary:
                                matched_category = category
                                matched_keywords.append(keyword)
                                logger.debug(f"基于摘要分类成功: 类别={category}, 关键字={keyword}")
                                break  # 找到第一个匹配的类别就停止
                        
                        if matched_category != '未分类':
                            break
                
                # 2. 如果摘要无法分类，基于科目信息辅助分类
                if matched_category == '未分类':
                    logger.debug(f"摘要无法分类，尝试使用科目信息辅助分类: 凭证号={voucher_no}, 科目={account_name}, 科目编码={account_code}")
                    
                    # 扩展科目名称映射（注意顺序：特异性强的类别排前面，避免被宽泛关键词遮蔽）
                    account_mappings = {
                        '工会经费': ['工会经费', '工会'],
                        '折旧费用': ['折旧', '累计折旧'],
                        '在职人工成本': ['工资', '薪金', '社保', '公积金', '个税', '个人所得税'],
                        '租金支出': ['租金', '租赁', '整租租金'],
                        '财务费用': ['财务费用', '利息', '银行手续', '手续费', '银行手续费'],
                        'POS刷卡手续费': ['刷卡', 'POS', '刷卡手续费', 'pos', '其他货币资金'],
                        '行政费用': ['办公', '行政', '差旅费', '招待费', '日常用品', '邮递费', '邮寄费', '接待', '保洁', '特约保洁', '搬运', '搬家', '水费', '电费', '水票'],
                        '税金支出': ['税金', '税费', '应交税费'],
                        '物业成本': ['绿化', '养护', '保安'],
                        # 工程改造基金必须先于工程成本（"工程改造基金"含"工程"，否则被工程成本截走）
                        '工程改造基金': ['工程改造基金', '改造基金', '专项改造'],
                        '工程成本': ['工程', '维护', '维修', '更换', '修理', '修缮', '养护'],
                        '外包人工': ['外委服务', '外包', '人力资源部关于基础经营业务外委服务'],
                        # 未知款项退回必须先于保证金退回（"误打款退还"含"退还"，否则被保证金退回截走）
                        '未知款项退回': ['误打款退还', '误打款', '错打款', '未知款项'],
                        '保证金退回': ['保证金', '退回', '退还', '诚意金'],
                        '资金划拨': ['资金划拨', '划拨至'],
                        '企划类费用': ['企划部', '美陈制作'],
                        '公共事业费': ['燃气'],
                        '招商费用': ['招商部'],
                        '信息系统成本': ['网络', '网络使用费', '硬件', '硬件维护', '软件', '软件维护', '系统', '信息系统', 'IT', '信息化', '弱电布线', '宽带费', '客流', '软件许可', '备件采购', '布线']
                    }
                    
                    # config.py 的 ACCOUNT_NAME_MAPPINGS：已有类别按字段覆盖，新类别追加到末尾
                    mapping_overrides = _cfg('ACCOUNT_NAME_MAPPINGS', None)
                    if isinstance(mapping_overrides, dict):
                        account_mappings.update(mapping_overrides)

                    # 基于科目名称分类（account_name 已小写化，关键词比较统一转小写，
                    # 修复 'POS'/'IT' 等大写关键词永不命中的问题）
                    for category, account_keywords in account_mappings.items():
                        for keyword in account_keywords:
                            if keyword.lower() in account_name:
                                matched_category = category
                                matched_keywords.append(f"科目:{keyword}")
                                logger.debug(f"基于科目名称分类成功: 类别={category}, 关键字={keyword}")
                                break
                        if matched_category != '未分类':
                            break

                    # 基于科目编码分类
                    if matched_category == '未分类' and account_code:
                        # 简单的科目编码规则映射
                        if account_code.startswith('6602'):
                            matched_category = '行政费用'
                            matched_keywords.append(f"科目编码:{account_code}")
                            logger.debug(f"基于科目编码分类成功: 类别=行政费用, 科目编码={account_code}")
                        elif account_code.startswith('6603'):
                            matched_category = '财务费用'
                            matched_keywords.append(f"科目编码:{account_code}")
                            logger.debug(f"基于科目编码分类成功: 类别=财务费用, 科目编码={account_code}")
                        elif account_code.startswith('6601'):
                            matched_category = '销售费用'
                            matched_keywords.append(f"科目编码:{account_code}")
                            logger.debug(f"基于科目编码分类成功: 类别=销售费用, 科目编码={account_code}")
                        elif account_code.startswith('6711'):
                            matched_category = '营业外支出'
                            matched_keywords.append(f"科目编码:{account_code}")
                            logger.debug(f"基于科目编码分类成功: 类别=营业外支出, 科目编码={account_code}")

                    # 其他货币资金科目下的刷卡手续费（独立分支，科目编码为空的行也能命中）
                    if matched_category == '未分类' and '其他货币资金' in account_name:
                        if any(keyword.lower() in account_name for keyword in ['刷卡', 'POS', '手续费']):
                            matched_category = 'POS刷卡手续费'
                            matched_keywords.append(f"科目:其他货币资金-刷卡手续费")
                            logger.debug(f"基于科目名称分类成功: 类别=POS刷卡手续费, 科目={account_name}")
                
                if matched_category == '未分类':
                    logger.debug(f"分类失败: 凭证号={voucher_no}, 摘要={summary[:100]}...")
                else:
                    logger.debug(f"分类完成: 凭证号={voucher_no}, 类别={matched_category}, 关键字={matched_keywords}")
                
                classified_df.at[idx, '费用类别'] = matched_category
                classified_df.at[idx, '匹配关键字'] = ','.join(matched_keywords)
            
            self.processed_data = classified_df
            
            # 统计分类结果
            classification_stats = classified_df['费用类别'].value_counts()
            logger.info("\n费用分类结果统计:")

            # 未分类占比预警（目标 <3%，与 NEW_CLASSIFICATION_STANDARDS.md 5.2 一致）
            total_count = len(classified_df)
            unclassified_count = int((classified_df['费用类别'] == '未分类').sum())
            if total_count > 0:
                unclassified_ratio = unclassified_count / total_count
                logger.info(f"未分类占比: {unclassified_ratio:.2%} ({unclassified_count}/{total_count})")
                if unclassified_ratio > 0.03:
                    logger.warning(f"未分类占比 {unclassified_ratio:.2%} 超过 3% 目标，请复核'待人工复核预警' sheet 并补充分类规则")
            for category, count in classification_stats.items():
                logger.info(f"{category}: {count}条")
            
            return classified_df
        except Exception as e:
            logger.error(f"费用分类失败: {str(e)}")
            return None
    
    def summarize_expenses(self):
        """汇总计算功能 (v2.0 支持收支分离和多维度分析)
        
        Returns:
            汇总结果字典
        """
        try:
            if self.processed_data is None:
                logger.warning("数据未分类，无法汇总")
                return None
            
            logger.info("开始多维度费用汇总计算")
            df = self.processed_data.copy()
            rules = self.get_classification_rules()
            
            # 建立类别到类型的映射
            cat_to_type = {cat: rule.get('type', 'OTHER') for cat, rule in rules.items()}
            df['收支类型'] = df['费用类别'].map(cat_to_type).fillna('OTHER')
            
            # 1. 整体收支汇总
            summary_by_type = df.groupby('收支类型')['贷方本币'].sum().to_dict()
            
            # 2. 按类别汇总
            summary_df = df.groupby(['收支类型', '费用类别'])['贷方本币'].sum().reset_index()
            
            # 3. 按时间汇总 (月度趋势)
            df['月份'] = pd.to_datetime(df['凭证日期']).dt.strftime('%Y-%m')
            monthly_summary = df.groupby(['月份', '收支类型'])['贷方本币'].sum().unstack().fillna(0)
            
            # 4. 按部门/项目汇总 (基于辅助核算或项目标识)
            project_col = '辅助核算' if '辅助核算' in df.columns else ('项目标识' if '项目标识' in df.columns else None)
            if project_col:
                project_summary = df.groupby([project_col, '费用类别'])['贷方本币'].sum().unstack().fillna(0)
            else:
                project_summary = pd.DataFrame()
            
            # 构建汇总结果
            summary_result = {
                'details': [],
                'type_totals': summary_by_type,
                'monthly_trend': monthly_summary.to_dict(),
                'project_distribution': project_summary.to_dict()
            }
            
            # 填充明细数据
            total_expense = summary_by_type.get('EXPENSE', 0)
            for idx, row in summary_df.iterrows():
                type_total = summary_by_type.get(row['收支类型'], 0)
                summary_result['details'].append({
                    'type': row['收支类型'],
                    'category': row['费用类别'],
                    'amount': row['贷方本币'],
                    'percentage_in_type': (row['贷方本币'] / type_total * 100) if type_total else 0.0
                })
            
            self.summary_result = summary_result
            
            # 打印核心汇总结果（输出全部收支类型，避免漏看 OUTFLOW/ADJUSTMENT/OTHER）
            logger.info("\n=== 2.0 财务收支分析摘要 ===")
            for type_name, type_amount in sorted(summary_by_type.items()):
                logger.info(f"{type_name}: {type_amount:,.2f}")
            logger.info("=" * 30)
            
            return summary_result
        except Exception as e:
            logger.error(f"费用汇总计算失败: {str(e)}")
            return None
    
    
    def export_results(self, output_file=None):
        """结果输出功能 (v2.0 增强多维度分析 Tab 页)
        
        Args:
            output_file: 输出文件路径
        
        Returns:
            输出文件路径
        """
        try:
            if self.processed_data is None or self.summary_result is None:
                logger.warning("数据未处理完成，无法导出")
                return None
            
            if output_file is None:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename_format = _cfg('OUTPUT_CONFIG.filename_format', '费用分类汇总报告_v2_{timestamp}.xlsx')
                output_file = filename_format.format(timestamp=timestamp)
            
            logger.info(f"准备导出重构报告: {output_file}")
            
            with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
                # 1. 核心摘要 (收支分析)：输出全部收支类型，杜绝"计算了但不展示"
                type_totals = self.summary_result['type_totals']
                type_labels = {
                    'EXPENSE': '经营性支出 (EXPENSE)',
                    'INFLOW': '经营性收入 (INFLOW)',
                    'CAPITAL': '资本性支出 (CAPITAL)',
                    'OUTFLOW': '保证金退回 (OUTFLOW)',
                    'TRANSFER': '资金划拨 (TRANSFER)',
                    'TAX_TRANSFER': '税金划拨 (TAX_TRANSFER)',
                    'ASSET': '资产/押金 (ASSET)',
                    'ADJUSTMENT': '计提确认 (ADJUSTMENT)',
                    'OTHER': '未归类/规则外 (OTHER)',
                }
                summary_data = [
                    {'维度': type_labels[t], '金额': type_totals.get(t, 0)}
                    for t in ['EXPENSE', 'INFLOW', 'CAPITAL', 'OUTFLOW', 'TRANSFER', 'TAX_TRANSFER', 'ASSET', 'ADJUSTMENT', 'OTHER']
                ]
                # 兜底：未来新增类型也能出现在摘要中
                for extra_type in type_totals:
                    if extra_type not in type_labels:
                        summary_data.append({'维度': f'{extra_type} (其他)', '金额': type_totals[extra_type]})
                pd.DataFrame(summary_data).to_excel(writer, sheet_name='财务收支摘要', index=False)

                # 2. 分类明细
                self.processed_data.to_excel(writer, sheet_name='全量分类明细', index=False)
                
                # 3. 按项目/部门分布
                if 'project_distribution' in self.summary_result:
                    proj_df = pd.DataFrame(self.summary_result['project_distribution'])
                    proj_df.to_excel(writer, sheet_name='项目维度分析')

                # 4. 月度趋势分析
                if 'monthly_trend' in self.summary_result:
                    trend_df = pd.DataFrame(self.summary_result['monthly_trend'])
                    trend_df.to_excel(writer, sheet_name='月度趋势分析')

                # 5. 分类规则快照
                rules = self.get_classification_rules()
                rules_list = []
                for cat, rule in rules.items():
                    rules_list.append({
                        '类别': cat,
                        '类型': rule.get('type'),
                        '关键字': ','.join(rule.get('keywords', [])),
                        '排除词': ','.join(rule.get('exclude_keywords', [])) if 'exclude_keywords' in rule else '',
                        '定义': rule.get('description')
                    })
                pd.DataFrame(rules_list).to_excel(writer, sheet_name='分类标准快照', index=False)

                # 6. 未分类预警
                unclassified = self.processed_data[self.processed_data['费用类别'] == '未分类']
                if not unclassified.empty:
                    unclassified.to_excel(writer, sheet_name='待人工复核预警', index=False)

            logger.info(f"v2.0 多维度报告导出成功: {output_file}")
            return output_file
            
        except Exception as e:
            logger.error(f"结果导出失败: {str(e)}")
            return None
    
    def run_pipeline(self, voucher_file=None, bank_file=None, output_file=None,
                     progress_callback=None, cancel_check=None):
        """运行完整的数据处理流程

        Args:
            voucher_file: 凭证列表文件路径
            bank_file: 银行存款辅助明细账文件路径
            output_file: 输出文件路径
            progress_callback: 可选回调 fn(step_name, percent)，每个阶段开始时调用（GUI 进度条用）
            cancel_check: 可选回调 fn() -> bool，返回 True 时在下一阶段前中止（GUI 取消按钮用）

        Returns:
            处理结果（输出文件路径；被取消或失败时返回 None）
        """
        def _report(step, percent):
            if progress_callback:
                try:
                    progress_callback(step, percent)
                except Exception:
                    pass

        def _cancelled():
            try:
                return bool(cancel_check and cancel_check())
            except Exception:
                return False

        try:
            logger.info("=" * 60)
            logger.info("费用分类汇总系统开始运行")
            logger.info("=" * 60)

            # 读取凭证列表数据
            _report("读取凭证列表...", 10)
            if _cancelled():
                logger.warning("处理已被用户取消")
                return None
            if voucher_file:
                self.read_voucher_data(voucher_file)

            # 读取银行存款辅助明细账数据
            _report("读取银行存款辅助明细账...", 20)
            if _cancelled():
                logger.warning("处理已被用户取消")
                return None
            if bank_file:
                self.read_bank_data(bank_file)

            # 整合数据
            _report("整合数据源...", 30)
            if _cancelled():
                logger.warning("处理已被用户取消")
                return None
            self.integrate_data()

            # 清洗数据
            _report("清洗数据...", 40)
            if _cancelled():
                logger.warning("处理已被用户取消")
                return None
            self.clean_data()

            # 费用分类
            _report("分类费用...", 60)
            if _cancelled():
                logger.warning("处理已被用户取消")
                return None
            self.classify_expenses()

            # 费用汇总
            _report("汇总计算...", 80)
            if _cancelled():
                logger.warning("处理已被用户取消")
                return None
            self.summarize_expenses()

            # 导出结果
            _report("生成报告...", 90)
            if _cancelled():
                logger.warning("处理已被用户取消")
                return None
            output_path = self.export_results(output_file)

            _report("处理完成", 100)
            logger.info("=" * 60)
            logger.info("费用分类汇总系统运行结束")
            logger.info("=" * 60)

            return output_path

        except Exception as e:
            logger.error(f"处理流程失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None


def find_data_files(directory='.'):
    """识别数据源目录中的凭证列表与银行存款辅助明细账（GUI 与 CLI 共用）

    识别规则（关键词可在 config.py 的 FILE_FORMAT_CONFIG 中配置）：
    - 银行明细账：文件名含 bank_keywords（默认"银行存款辅助明细账"）
    - 凭证列表：文件名含 voucher_keywords（默认"凭证列表"/"凭证"）
    同一类型存在多个文件时取修改时间最新的一个；跳过 Excel 临时文件（~$ 开头）。

    Args:
        directory: 数据源目录

    Returns:
        (voucher_file, bank_file)：文件路径或 None
    """
    voucher_keywords = _cfg('FILE_FORMAT_CONFIG.voucher_keywords', ['凭证列表', '凭证'])
    bank_keywords = _cfg('FILE_FORMAT_CONFIG.bank_keywords', ['银行存款辅助明细账'])

    if not os.path.isdir(directory):
        logger.warning(f"数据源目录不存在: {directory}")
        return None, None

    voucher_candidates = []
    bank_candidates = []
    for filename in os.listdir(directory):
        if not (filename.endswith('.xlsx') or filename.endswith('.xls')):
            continue
        if filename.startswith('~$'):
            continue
        full_path = os.path.join(directory, filename)
        if any(kw in filename for kw in bank_keywords):
            bank_candidates.append(full_path)
        elif any(kw in filename for kw in voucher_keywords):
            voucher_candidates.append(full_path)

    def _latest(paths):
        return max(paths, key=os.path.getmtime) if paths else None

    voucher_file = _latest(voucher_candidates)
    bank_file = _latest(bank_candidates)
    logger.info(f"文件识别结果: 凭证列表={voucher_file or '未找到'}, 银行明细账={bank_file or '未找到'}")
    return voucher_file, bank_file


if __name__ == "__main__":
    # 自动识别当前目录下的凭证列表与银行存款辅助明细账（与 GUI 共用识别逻辑）
    classifier = ExpenseClassifier()
    voucher_file, bank_file = find_data_files()

    if voucher_file or bank_file:
        output = classifier.run_pipeline(voucher_file, bank_file)
        if output:
            print(f"处理成功，结果已导出到: {output}")
        else:
            print("处理失败，请查看日志文件")
    else:
        print("未找到相关Excel文件，请手动指定文件路径")

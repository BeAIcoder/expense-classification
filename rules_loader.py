#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分类规则 JSON 数据加载器（单一权威源）

设计（2026-10-01 规则数据化重构）：
- 规则数据存放于包内 ``classification_rules.json``（28 类，含顺序/注释说明），
  本文件只负责加载与 config 覆盖应用，不再在引擎源码中维护规则字典。
- ``config.py`` 的 ``CLASSIFICATION_RULES`` 覆盖语义与数据化前完全一致：
  同名类别按字段 update（未覆盖字段保留），新类别追加到末尾；
  加载时应用，不改动 JSON 文件本身。
- config 注入语义（config_module 参数）：
  * 缺省（不传）→ 自动探测本机 config.py（开源克隆无该文件时等价无覆盖）；
  * 显式传 None → 确证无 config，返回纯 JSON 内置规则（测试桩/可复现场景用）；
  * 显式传模块/对象 → 使用调用方提供的配置（引擎传自身模块级 _app_config，
    外部消费方如 vendored 副本可注入项目私有配置）。

用法：
    from rules_loader import load_classification_rules
    rules = load_classification_rules()                       # 自动探测本机 config.py
    rules = load_classification_rules(config_module=my_cfg)   # 显式注入 config 模块
    rules = load_classification_rules(config_module=None)     # 确证无 config（纯内置规则）
    rules = load_classification_rules(rules_file='...')       # 指定 JSON 路径
"""

import json
import os

# 缺省规则文件：本模块同目录下的 classification_rules.json
DEFAULT_RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'classification_rules.json')

# config_module 缺省哨兵：区分"不传（自动探测）"与"显式传 None（确证无 config）"
_AUTO_CONFIG = object()


def _cfg_from(config_module, dotted_key, default):
    """按 'SECTION.key' 路径从给定 config 模块读取配置。

    config_module 为 None 或任何一级缺失/异常，均返回 default，
    与引擎 _cfg 的回退语义一致。
    """
    if config_module is None:
        return default
    try:
        value = config_module
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


def _try_import_config():
    """尝试导入本机 config.py（该文件被 .gitignore，开源克隆场景缺失时返回 None）。"""
    try:
        import config as app_config
        return app_config
    except Exception:
        return None


def load_rules_file(rules_file=None):
    """读取 JSON 规则文件，返回顶层结构 dict（version/description/notes/categories）。

    Args:
        rules_file: JSON 文件路径；缺省为本模块同目录的 classification_rules.json

    Returns:
        规则文件顶层 dict

    Raises:
        FileNotFoundError / json.JSONDecodeError: 文件缺失或格式错误时向上抛，
        由调用方决定兜底策略（不静默吞异常，避免规则悄悄失效）。
    """
    path = rules_file if rules_file else DEFAULT_RULES_FILE
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_classification_rules(rules_file=None, config_module=_AUTO_CONFIG):
    """加载分类规则，返回有序 dict（类别名 -> 规则 dict）。

    - 顺序即 JSON 文档顺序（Python 3.7+ dict 保序），首中优先匹配；
      POS刷卡手续费在财务费用前、招商费用在行政费用前等顺序敏感关系
      由 JSON 数据文件保证。
    - config_module 见模块 docstring 的三态语义。

    Args:
        rules_file: 可选，指定 JSON 规则文件路径
        config_module: 可选，_AUTO_CONFIG（缺省，自动探测）/ None（无 config）/ 模块对象

    Returns:
        规则有序 dict，可直接供 classify_expenses 使用
    """
    if config_module is _AUTO_CONFIG:
        config_module = _try_import_config()

    payload = load_rules_file(rules_file)
    if isinstance(payload, dict) and 'categories' in payload:
        categories = payload['categories']
    else:
        # 兼容裸 dict 格式（无顶层包装的规则文件）
        categories = payload

    # dict() 拷贝保证每次调用返回全新对象：config 覆盖的 update 不会污染文件语义
    rules = dict(categories)

    # config.py 的 CLASSIFICATION_RULES：已有类别按字段覆盖，新类别追加到末尾
    overrides = _cfg_from(config_module, 'CLASSIFICATION_RULES', None)
    if isinstance(overrides, dict):
        for cat, rule in overrides.items():
            if cat in rules and isinstance(rule, dict):
                rules[cat].update(rule)
            else:
                rules[cat] = rule
    return rules

"""
Tool Calling 统一导出模块
统一管理所有可用的工具
"""
from .devices_info_tools import get_tools as device_get_tools, get_tool_map as device_get_tool_map

# 合并所有工具 Schema
AVAILABLE_TOOLS = []
AVAILABLE_TOOLS.extend(device_get_tools())

# 合并所有工具映射
TOOL_MAP = {}
TOOL_MAP.update(device_get_tool_map())


def get_all_tools():
    """获取所有工具 schema 列表"""
    return AVAILABLE_TOOLS


def get_all_tool_map():
    """获取所有工具映射"""
    return TOOL_MAP

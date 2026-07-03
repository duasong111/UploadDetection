"""
标准化的响应模块
统一管理所有 API 响应格式
"""
from typing import Optional, Any
from flask import jsonify


def create_response(status_code: int, message: str, success: bool, data: Optional[dict] = None) -> tuple:
    """
    生成标准化的 JSON 响应

    Args:
        status_code: HTTP 状态码
        message: 响应消息
        success: 是否成功
        data: 响应数据（可选）

    Returns:
        (json_response, status_code)
    """
    response = {
        "status_code": status_code,
        "message": message,
        "success": success
    }
    if data is not None:
        response["data"] = data
    return jsonify(response), status_code


# ==================== AI 相关响应 ====================

def create_ai_response(
    status_code: int,
    message: str,
    success: bool,
    answer: Optional[str] = None,
    tool_calls: Optional[list] = None,
    references: Optional[list] = None,
    daily_usage: Optional[int] = None,
    daily_limit: Optional[int] = None,
    extra: Optional[dict] = None
) -> tuple:
    """
    AI 对话专用响应格式

    Args:
        status_code: HTTP 状态码
        message: 响应消息
        success: 是否成功
        answer: AI 生成的回答
        tool_calls: 调用的工具列表
        references: 参考来源（如 RAG 检索结果）
        daily_usage: 当日已使用次数
        daily_limit: 当日限额
        extra: 扩展字段

    Returns:
        (json_response, status_code)
    """
    response = {
        "status_code": status_code,
        "message": message,
        "success": success
    }

    data = {}
    if answer is not None:
        data["answer"] = answer
    if tool_calls is not None:
        data["tool_calls"] = tool_calls
    if references is not None:
        data["references"] = references
    if daily_usage is not None:
        data["daily_usage"] = daily_usage
    if daily_limit is not None:
        data["daily_limit"] = daily_limit
    if extra is not None:
        data.update(extra)

    if data:
        response["data"] = data

    return jsonify(response), status_code


def create_tool_response(
    status_code: int,
    tool_name: str,
    result: Any,
    success: bool,
    error: Optional[str] = None
) -> tuple:
    """
    Tool Calling 执行结果响应格式

    Args:
        status_code: HTTP 状态码
        tool_name: 工具名称
        result: 工具执行结果
        success: 是否成功
        error: 错误信息（可选）

    Returns:
        (json_response, status_code)
    """
    response = {
        "status_code": status_code,
        "success": success,
        "tool": tool_name
    }

    data = {
        "result": result
    }
    if error is not None:
        data["error"] = error

    response["data"] = data
    return jsonify(response), status_code


def create_success_response(message: str, data: Optional[dict] = None, status_code: int = 200) -> tuple:
    """成功响应简化版"""
    return create_response(status_code, message, True, data)


def create_error_response(message: str, status_code: int = 400, data: Optional[dict] = None) -> tuple:
    """错误响应简化版"""
    return create_response(status_code, message, False, data)

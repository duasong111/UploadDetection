"""
标准化的响应模块
统一管理所有 API 响应格式
支持 Flask 和 FastAPI 双框架
"""
from typing import Optional, Any, Union, Dict
from fastapi.responses import JSONResponse

# 尝试导入 Flask（仅用于兼容模式）
try:
    from flask import jsonify
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    jsonify = None


def create_response(
    status_code: int,
    message: str,
    success: bool,
    data: Optional[dict] = None
) -> Union[tuple, JSONResponse, Dict]:
    """
    生成标准化的 JSON 响应

    Args:
        status_code: HTTP 状态码
        message: 响应消息
        success: 是否成功
        data: 响应数据（可选）

    Returns:
        FastAPI: JSONResponse
        Flask: (json_response, status_code)
        Dict: 无框架模式
    """
    response = {
        "status_code": status_code,
        "message": message,
        "success": success
    }
    if data is not None:
        response["data"] = data

    # FastAPI 模式
    if not FLASK_AVAILABLE or jsonify is None:
        return JSONResponse(content=response, status_code=status_code)

    # 尝试检测是否在 Flask 应用上下文中
    try:
        from flask import has_app_context
        if has_app_context():
            return jsonify(response), status_code
    except:
        pass

    # 不在 Flask 上下文中，返回 dict 让 FastAPI 处理
    return JSONResponse(content=response, status_code=status_code)


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
) -> Union[tuple, JSONResponse, Dict]:
    """
    AI 对话专用响应格式
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

    # FastAPI 模式
    if not FLASK_AVAILABLE or jsonify is None:
        return JSONResponse(content=response, status_code=status_code)

    # 尝试检测是否在 Flask 应用上下文中
    try:
        from flask import has_app_context
        if has_app_context():
            return jsonify(response), status_code
    except:
        pass

    return JSONResponse(content=response, status_code=status_code)


def create_tool_response(
    status_code: int,
    tool_name: str,
    result: Any,
    success: bool,
    error: Optional[str] = None
) -> Union[tuple, JSONResponse, Dict]:
    """
    Tool Calling 执行结果响应格式
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

    # FastAPI 模式
    if not FLASK_AVAILABLE or jsonify is None:
        return JSONResponse(content=response, status_code=status_code)

    try:
        from flask import has_app_context
        if has_app_context():
            return jsonify(response), status_code
    except:
        pass

    return JSONResponse(content=response, status_code=status_code)


def create_success_response(message: str, data: Optional[dict] = None, status_code: int = 200) -> Union[tuple, JSONResponse, Dict]:
    """成功响应简化版"""
    return create_response(status_code, message, True, data)


def create_error_response(message: str, status_code: int = 400, data: Optional[dict] = None) -> Union[tuple, JSONResponse, Dict]:
    """错误响应简化版"""
    return create_response(status_code, message, False, data)

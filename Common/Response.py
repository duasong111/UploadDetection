#这个函数可以去用来专门的去给出反应
from typing import Optional, Tuple
from flask import Flask, request, jsonify, Response
def create_response(status_code: int, message: str, success: bool, data: Optional[dict] = None) -> tuple[Response, int]:
    """生成标准化的 JSON 响应---这样就避免了多次的重复的定义了"""
    response = {
        "status_code": status_code,
        "message": message,
        "success": success
    }
    if data:
        response["data"] = data
    return jsonify(response), status_code

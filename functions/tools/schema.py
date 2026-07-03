"""
Tool Schema 定义模块
定义统一的 schema 结构，方便各工具模块使用
"""

# 基础 schema 模板
BASE_FUNCTION_SCHEMA = {
    "type": "object",
    "properties": {}
}

# 字符串类型 schema
def string_schema(description: str, example: str = None) -> dict:
    schema = {
        "type": "string",
        "description": description
    }
    if example:
        schema["description"] += f"，例如：{example}"
    return schema

# 整数类型 schema
def integer_schema(description: str, example: int = None) -> dict:
    schema = {
        "type": "integer",
        "description": description
    }
    if example:
        schema["description"] += f"，例如：{example}"
    return schema

# 构建完整的 function schema
def build_function_schema(name: str, description: str, parameters: dict, required: list = None) -> dict:
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": parameters,
                "required": required or []
            }
        }
    }
    return schema

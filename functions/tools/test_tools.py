"""
天气工具模块
使用 @tool 装饰器自动生成 schema
"""
import requests
from langchain_core.tools import tool


def _get_weather_impl(city: str) -> dict:
    """
    实际天气查询实现函数

    Args:
        city: 城市名称

    Returns:
        dict: 天气信息
    """
    try:
        # 使用 wttr.in 免费天气 API
        url = f"http://wttr.in/{city}?format=j1"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # 解析 wttr.in 返回格式
            current = data.get("current_condition", [{}])[0]
            result = {
                "城市": city,
                "温度": f"{current.get('temp_C', 'N/A')}°C",
                "体感温度": f"{current.get('FeelsLikeC', 'N/A')}°C",
                "湿度": f"{current.get('humidity', 'N/A')}%",
                "天气": current.get('weatherDesc', [{'value': 'N/A'}])[0].get('value', 'N/A'),
                "风速": f"{current.get('windspeedKmph', 'N/A')} km/h",
                "能见度": f"{current.get('visibility', 'N/A')} km",
            }
            return {
                "success": True,
                "data": result
            }
        else:
            return {
                "success": False,
                "message": f"请求失败: {response.status_code}"
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"服务器错误: {str(e)}"
        }


@tool
def get_weather(city: str) -> str:
    """查询指定城市的天气信息

    当用户询问天气相关问题时调用此工具，例如'今天天气怎么样'、'北京热吗'等。

    Args:
        city: 城市名称，例如：北京、上海、武汉
    """
    # 调用实际实现
    result = _get_weather_impl(city)
    return str(result)


def get_tools():
    """获取天气工具 schema 列表（OpenAI function calling 格式）"""
    tool_obj = get_weather
    schema = {
        "name": tool_obj.name,
        "description": tool_obj.description,
        "parameters": tool_obj.args_schema.model_json_schema()
    }
    return [{
        "type": "function",
        "function": schema
    }]


def get_tool_map():
    """获取天气工具映射（使用实际实现函数）"""
    return {"get_weather": _get_weather_impl}

"""
设备信息查询工具
用于 Tool Calling，让 AI 能够查询设备相关信息
带 Redis 缓存
"""
import json
import redis
from database.operateFunction import execuFunction
from config import REDIS_URL

# Redis 配置
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# 缓存 TTL（秒）
CACHE_TTL_DEVICE_LIST = 300      # 设备列表缓存 5 分钟
CACHE_TTL_DEVICE_STATUS = 300    # 设备状态缓存 5 分钟
CACHE_TTL_DEVICE_HISTORY = 120    # 设备历史缓存 2 分钟
CACHE_TTL_DEVICE_STATS = 600      # 设备统计缓存 10 分钟

# 缓存 key 前缀
CACHE_KEY_ALL_DEVICES = "ai_tools:all_devices"
CACHE_KEY_DEVICE_STATUS = "ai_tools:device_status:"
CACHE_KEY_DEVICE_HISTORY = "ai_tools:device_history:"
CACHE_KEY_DEVICE_STATS = "ai_tools:device_stats"


def get_all_devices():
    """
    获取所有设备列表及状态（带缓存）
    :return: 所有设备信息，包含在线/离线状态
    """
    try:
        # 尝试从缓存获取
        cached = r.get(CACHE_KEY_ALL_DEVICES)
        if cached:
            result = json.loads(cached)
            return {
                "success": True,
                "data": result,
                "from_cache": True
            }

        # 缓存未命中，查询数据库
        db = execuFunction()
        result = db.get_all_devices()

        if "error" in result:
            return {
                "success": False,
                "message": result["error"]
            }

        # 写入缓存
        cache_data = {
            "total_devices": result["total"],
            "online_devices": result["online"],
            "offline_devices": result["offline"],
            "devices": result["devices"]
        }
        r.setex(CACHE_KEY_ALL_DEVICES, CACHE_TTL_DEVICE_LIST, json.dumps(cache_data, ensure_ascii=False))

        return {
            "success": True,
            "data": cache_data,
            "from_cache": False
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"查询设备列表失败: {str(e)}"
        }


def get_device_status(device_sn: str = None):
    """
    查询指定设备的状态（带缓存）
    :param device_sn: 设备序列号，如不提供则返回说明
    :return: 设备详细信息
    """
    if not device_sn:
        return {
            "success": False,
            "message": "device_sn 参数缺失，请在调用时提供设备序列号"
        }
    try:
        cache_key = f"{CACHE_KEY_DEVICE_STATUS}{device_sn}"

        # 尝试从缓存获取
        cached = r.get(cache_key)
        if cached:
            result = json.loads(cached)
            return {
                "success": True,
                "data": result,
                "from_cache": True
            }

        # 缓存未命中，查询数据库
        db = execuFunction()
        result = db.get_device_status(device_sn)

        if "error" in result:
            return {
                "success": False,
                "message": result["error"]
            }

        # 写入缓存
        r.setex(cache_key, CACHE_TTL_DEVICE_STATUS, json.dumps(result, ensure_ascii=False))

        return {
            "success": True,
            "data": result,
            "from_cache": False
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"查询设备状态失败: {str(e)}"
        }


def get_device_history(device_sn: str = None, number: int = 10):
    """
    查询指定设备的运行历史（带缓存）
    :param device_sn: 设备序列号，如不提供则返回所有设备的历史记录概要
    :param number: 返回记录数，默认10条
    :return: 设备运行历史列表
    """
    if not device_sn:
        return {
            "success": False,
            "message": "device_sn 参数缺失，请在调用时提供设备序列号"
        }
    try:
        cache_key = f"{CACHE_KEY_DEVICE_HISTORY}{device_sn}:{number}"

        # 尝试从缓存获取
        cached = r.get(cache_key)
        if cached:
            result = json.loads(cached)
            return {
                "success": True,
                "data": result,
                "from_cache": True
            }

        # 缓存未命中，查询数据库
        db = execuFunction()
        result = db.get_device_history(device_sn, number)

        if "error" in result:
            return {
                "success": False,
                "message": result["error"]
            }

        cache_data = {
            "device_sn": result["device_sn"],
            "total_records": result["total"],
            "records": result["records"]
        }

        # 写入缓存
        r.setex(cache_key, CACHE_TTL_DEVICE_HISTORY, json.dumps(cache_data, ensure_ascii=False))

        return {
            "success": True,
            "data": cache_data,
            "from_cache": False
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"查询设备历史失败: {str(e)}"
        }


def get_device_statistics():
    """
    获取设备统计信息（带缓存）
    :return: 设备统计汇总
    """
    try:
        # 尝试从缓存获取
        cached = r.get(CACHE_KEY_DEVICE_STATS)
        if cached:
            result = json.loads(cached)
            return {
                "success": True,
                "data": result,
                "from_cache": True
            }

        # 缓存未命中，查询数据库
        db = execuFunction()
        result = db.get_device_statistics()

        if "error" in result:
            return {
                "success": False,
                "message": result["error"]
            }

        # 写入缓存
        r.setex(CACHE_KEY_DEVICE_STATS, CACHE_TTL_DEVICE_STATS, json.dumps(result, ensure_ascii=False))

        return {
            "success": True,
            "data": result,
            "from_cache": False
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"获取设备统计失败: {str(e)}"
        }


def clear_device_cache():
    """
    清除所有设备相关缓存（设备上报时调用）
    """
    try:
        # 清除设备列表缓存
        r.delete(CACHE_KEY_ALL_DEVICES)
        # 清除统计缓存
        r.delete(CACHE_KEY_DEVICE_STATS)
        # 清除所有设备状态缓存
        for key in r.scan_iter(f"{CACHE_KEY_DEVICE_STATUS}*"):
            r.delete(key)
        # 清除所有设备历史缓存
        for key in r.scan_iter(f"{CACHE_KEY_DEVICE_HISTORY}*"):
            r.delete(key)
        return True
    except Exception as e:
        print(f"清除设备缓存失败: {e}")
        return False


# ============ Tool Schema 定义 ============

from .schema import build_function_schema, string_schema, integer_schema

DEVICE_TOOLS = [
    build_function_schema(
        name="get_all_devices",
        description="获取所有设备列表及在线状态。当用户询问有哪些设备、设备总数、在线设备数量时调用此工具。（结果缓存5分钟）",
        parameters={},
        required=[]
    ),
    build_function_schema(
        name="get_device_status",
        description="查询指定设备的详细状态信息，包括是否在线、最后上报时间、运行次数等。当用户询问某个具体设备的状态时调用此工具。（结果缓存5分钟）",
        parameters={
            "device_sn": string_schema("设备序列号", "303、118")
        },
        required=[]
    ),
    build_function_schema(
        name="get_device_history",
        description="查询指定设备的运行历史记录。当用户询问某个设备的历史运行情况时调用此工具。（结果缓存2分钟）",
        parameters={
            "device_sn": string_schema("设备序列号", "303"),
            "number": integer_schema("返回记录数量", 10)
        },
        required=[]
    ),
    build_function_schema(
        name="get_device_statistics",
        description="获取设备统计汇总信息，包括总设备数、在线数，今日新增等。当用户询问设备统计信息时调用此工具。（结果缓存10分钟）",
        parameters={},
        required=[]
    )
]

# 工具映射
DEVICE_TOOL_MAP = {
    "get_all_devices": get_all_devices,
    "get_device_status": get_device_status,
    "get_device_history": get_device_history,
    "get_device_statistics": get_device_statistics
}


def get_tools():
    """获取设备工具 schema 列表"""
    return DEVICE_TOOLS


def get_tool_map():
    """获取设备工具映射"""
    return DEVICE_TOOL_MAP

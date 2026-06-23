from flask_socketio import SocketIO, emit
from Common.Response import create_response
from flask import Flask, request
from functions.user import LoginFunction, RegisterFunction, UserContributionView, ChangePasswordView
from functions.device import ListDevicesView, QueryDeviceOnlineHistoryView, StaticRunTimeView
from functions.frp import QueryFrpDeviceUptimeView,UpdateFrpConfigView,UpdateN2NConfigView
from functions.ssh_config import AddLicenseView, BatchDeployView
from functions.device_query import QueryDeviceView
from functions.avatar import AvatarManager
from functions.ai_chat import ai_chat_view
from database.operateFunction import execuFunction
from functions.transmission import Configuration
from flask_cors import CORS
from http import HTTPStatus
checkLogin = LoginFunction()
registerFunc = RegisterFunction()
userContributionView = UserContributionView()
changePasswordView = ChangePasswordView()
avatar_manager = AvatarManager()
db_function = execuFunction()
config = Configuration()


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')


# ==================== WebSocket 事件 ====================
@socketio.on('connect')
def handle_connect():
    """客户端连接"""
    print(f"客户端连接: {request.sid}")
    emit('connected', {'sid': request.sid})


@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开"""
    print(f"客户端断开: {request.sid}")


@socketio.on('ai_chat')
def handle_ai_chat(data):
    """处理 AI 聊天 WebSocket 事件"""
    from functions.ai_chat import ai_chat_view

    message = data.get('message')
    history = data.get('history', [])
    username = data.get('username')

    if not message:
        emit('ai_response', {'success': False, 'message': '消息内容不能为空'})
        return

    # 调用 AI
    result, success = ai_chat_view.chat(message, history, username)

    # 通过 WebSocket 发送结果
    emit('ai_response', result)

# 不计入请求统计的接口
EXCLUDED_PATHS = {'/api/login/', '/api/register/', '/api/user_contributions/'}

@app.before_request
def track_request_count():
    """统计所有API请求次数"""
    from functions.user import UserContributionView

    # 排除不需要计数的接口
    if request.path in EXCLUDED_PATHS:
        return

    # 仅统计 /api/ 开头的请求
    if not request.path.startswith('/api/'):
        return

    # 从请求中获取用户名
    username = None

    # 1. 尝试从请求体获取
    if request.is_json:
        data = request.get_json(silent=True) or {}
        username = data.get('username')

    # 2. 尝试从 header 获取
    if not username:
        username = request.headers.get('X-Username')

    # 3. 尝试从 query string 获取
    if not username:
        username = request.args.get('username')

    if username:
        UserContributionView.increment_request_count(username)


@app.route("/api/register/", methods=["POST"], strict_slashes=False)
def register():
    try:
        data = request.get_json()
        user = data.get('username')
        pwd = data.get('password')
        return registerFunc.register(user, pwd)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

@app.route("/api/login/", methods=["POST"], strict_slashes=False)
def login():
    try:
        data = request.get_json()
        user = data.get('username')
        pwd = data.get('password')
        return checkLogin.checklogin(user, pwd)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

# 修改密码
app.add_url_rule(
    '/api/change_password/',
    view_func=ChangePasswordView.as_view('change_password'),
    methods=['POST']
)

# 展示设备列表
app.add_url_rule(
    '/api/list_devices/',
    view_func=ListDevicesView.as_view('list_devices'),
    methods=['GET']
)

# 查询设备上线历史
app.add_url_rule(
    '/api/query_device_online_history/',
    view_func=QueryDeviceOnlineHistoryView.as_view('query_device_online_history'),
    methods=['POST']
)

# 统计系统开始时间
app.add_url_rule(
    '/api/static_time/',
    view_func=StaticRunTimeView.as_view('static_runtime'),
    methods=['POST']
)

# 查询FRP设备在线表
app.add_url_rule(
    '/api/device_uptime/',
    view_func=QueryFrpDeviceUptimeView.as_view('query_frp_device_uptime'),
    methods=['POST']
)

# FRP 配置更新接口
app.add_url_rule(
    '/api/frp_config_update/',
    view_func=UpdateFrpConfigView.as_view('update_frp_config'),
    methods=['POST']
)

# N2N配置更新接口
app.add_url_rule(
    '/api/n2n_config_update/',
    view_func=UpdateN2NConfigView.as_view('update_n2n_config'),
    methods=['POST']
)

# 设备检验流程配置
app.add_url_rule(
    '/api/quick_configuration/',
    view_func=Configuration.as_view('quick_configuration'),
    methods=['POST']
)
# 远程增加鉴权文件
app.add_url_rule(
    '/api/add_license/',
    view_func=AddLicenseView.as_view('add_license'),
    methods=['POST']
)

# 批量部署SSH配置文件
app.add_url_rule(
    '/api/batch_deploy/',
    view_func=BatchDeployView.as_view('batch_deploy'),
    methods=['POST']
)

# 查询设备信息
app.add_url_rule(
    '/api/query_device/',
    view_func=QueryDeviceView.as_view('query_device'),
    methods=['POST']
)

# 用户贡献统计（每日登录次数）
@app.route("/api/user_contributions/", methods=["POST"], strict_slashes=False)
def user_contributions():
    try:
        data = request.get_json()
        username = data.get('username')
        month = data.get('month')  # 格式: "YYYY-MM"
        return userContributionView.get_contributions(username, month)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


# 用户头像上传
@app.route("/api/upload_avatar/", methods=["POST"], strict_slashes=False)
def upload_avatar():
    try:
        username = request.form.get('username') or (request.json.get('username') if request.is_json else None)
        file = request.files.get('file')

        if not username:
            return create_response(HTTPStatus.BAD_REQUEST, "用户名为必填项", False)
        if not file:
            return create_response(HTTPStatus.BAD_REQUEST, "文件为必填项", False)

        return avatar_manager.upload_avatar(username, file)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

# 获取用户头像
@app.route("/api/avatar/<path:filename>/", methods=["GET"], strict_slashes=False)
@app.route("/api/avatar/<path:filename>", methods=["GET"], strict_slashes=False)
def get_avatar(filename):
    try:
        return avatar_manager.get_avatar(filename)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)


# AI 聊天接口
app.add_url_rule(
    '/api/ai_chat/',
    view_func=ai_chat_view,
    methods=['POST']
)


if __name__ == '__main__':

    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
from flask_socketio import emit
from Common.Response import create_response
from flask import Flask, request
from functions.user import LoginFunction, RegisterFunction, UserContributionView
from functions.device import ListDevicesView, QueryDeviceOnlineHistoryView, StaticRunTimeView
from functions.frp import QueryFrpDeviceUptimeView,UpdateFrpConfigView,UpdateN2NConfigView
from functions.ssh_config import AddLicenseView, BatchDeployView
from functions.device_query import QueryDeviceView
from database.operateFunction import execuFunction
from functions.transmission import Configuration
from flask_cors import CORS
from http import HTTPStatus

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

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

checkLogin = LoginFunction()
registerFunc = RegisterFunction()
userContributionView = UserContributionView()
db_function = execuFunction()
config = Configuration()

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
        return userContributionView.get_contributions(username)
    except Exception as e:
        return create_response(HTTPStatus.INTERNAL_SERVER_ERROR, f"服务器错误: {str(e)}", False)

if __name__ == '__main__':

    app.run(host='0.0.0.0', port=5000, debug=False)
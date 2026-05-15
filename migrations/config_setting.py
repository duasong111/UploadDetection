"""
迁移脚本扩展：创建设备配置日志表
"""
from database.Postgresql import get_postgres_connection

def create_config_log_table():
    conn = None
    try:
        conn = get_postgres_connection()
        with conn.cursor() as cur:
            # 创建设备配置日志表 (device_config_log)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS device_config_log (
                    id SERIAL PRIMARY KEY,
                    device_sn VARCHAR(64) NOT NULL,
                    device_ip VARCHAR(50) NOT NULL,
                    operator VARCHAR(255) NOT NULL,
                    status VARCHAR(20) DEFAULT 'running', -- running, success, failed
                    config_details JSONB,                -- 存储具体的配置参数
                    full_log TEXT,                       -- 存储 SSH 过程中的完整输出内容
                    step_results JSONB,                  -- 存储你说的 9 个步骤的各自成功与否 [true, true, false...]
                    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finish_time TIMESTAMP
                );
            """)
            conn.commit()
            print("✅ 表 device_config_log 已创建或已存在 (用于记录一键配置流程)")
    except Exception as e:
        print(f"❌ 创建配置日志表失败: {e}")
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    # 执行新增的配置日志表
    create_config_log_table()
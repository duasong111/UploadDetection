"""
迁移脚本：创建设备相关三个表
文件路径：migrations/002_create_device_tables.py
运行方式：在项目根目录执行 python migrations/002_create_device_tables.py
"""

from database.Postgresql import get_postgres_connection


def create_device_tables():
    """创建 Device、DeviceRunSession 和 User 三个表"""
    conn = None
    try:
        conn = get_postgres_connection()
        with conn.cursor() as cur:
            # 1. 创建设备表 (Device)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS device (
                    id SERIAL PRIMARY KEY,
                    sn VARCHAR(64) UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                );
            """)
            print("✅ 表 device 已创建或已存在")

            # 2. 创建运行会话表 (DeviceRunSession)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS device_run_session (
                    id SERIAL PRIMARY KEY,
                    device_id INTEGER NOT NULL,
                    uuid VARCHAR(64) NOT NULL,
                    first_report_time TIMESTAMP NOT NULL,
                    last_report_time TIMESTAMP NOT NULL,
                    max_runtime_seconds INTEGER DEFAULT 0 NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,

                    CONSTRAINT fk_device 
                        FOREIGN KEY (device_id) 
                        REFERENCES device(id) 
                        ON DELETE CASCADE,

                    CONSTRAINT unique_device_uuid 
                        UNIQUE (device_id, uuid)
                );
            """)
            print("✅ 表 device_run_session 已创建或已存在")

            # 3. 创建用户表 (User) - 适配您现有的登录逻辑
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "user" (
                    name VARCHAR(255) PRIMARY KEY,
                    password BYTEA NOT NULL,
                    salt TEXT NOT NULL,
                    avatar_path VARCHAR(500) DEFAULT NULL,
                    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                );
            """)
            print("✅ 表 \"user\" 已创建或已存在")

            conn.commit()
            print("\n🎉 所有三个表创建成功！")
            print("表结构如下：")
            print("   - device              (sn 唯一)")
            print("   - device_run_session  (device_id + uuid 联合唯一，外键关联 device)")
            print("   - \"user\"             (name 为主键，password 使用 BYTEA 存储哈希)")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 创建表失败: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    create_device_tables()
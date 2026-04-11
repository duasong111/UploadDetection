"""迁移脚本：创建 FRP 设备相关两个表
文件路径：migrations/003_create_frp_device_tables.py
运行方式：在项目根目录执行 python migrations/003_create_frp_device_tables.py
"""

from database.Postgresql import get_postgres_connection


def create_frp_device_tables():
    """创建 FRP 设备相关表：frp_device 和 frp_device_uptime_log"""
    conn = None
    try:
        conn = get_postgres_connection()
        with conn.cursor() as cur:
            # 1. 创建 FRP 设备基础表 (frp_device)
            # host 作为唯一标识，存储连接所需密码
            cur.execute("""
                CREATE TABLE IF NOT EXISTS frp_device (
                    id SERIAL PRIMARY KEY,
                    host VARCHAR(255) UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                );
            """)
            print("✅ 表 frp_device 已创建或已存在")

            # 2. 创建 FRP 设备 uptime 上报日志表 (frp_device_uptime_log)
            # 记录每次巡检的结果，支持历史查询
            cur.execute("""
                CREATE TABLE IF NOT EXISTS frp_device_uptime_log (
                    id BIGSERIAL PRIMARY KEY,
                    device_id INTEGER NOT NULL,
                    host VARCHAR(255) NOT NULL,
                    uptime_result TEXT NOT NULL,
                    query_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,

                    CONSTRAINT fk_frp_device 
                        FOREIGN KEY (device_id) 
                        REFERENCES frp_device(id) 
                        ON DELETE CASCADE,

                    CONSTRAINT unique_frp_device_query 
                        UNIQUE (device_id, query_time)
                );
            """)
            print("✅ 表 frp_device_uptime_log 已创建或已存在")

            # 添加常用索引，提升查询性能
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_frp_uptime_device_id 
                ON frp_device_uptime_log(device_id);
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_frp_uptime_query_time 
                ON frp_device_uptime_log(query_time DESC);
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_frp_uptime_host 
                ON frp_device_uptime_log(host);
            """)

            print("✅ 索引创建完成")

            conn.commit()
            print("\n🎉 FRP 设备相关表创建成功！")
            print("表结构如下：")
            print(" - frp_device (host 唯一，存储 host 和 password)")
            print(" - frp_device_uptime_log (device_id 外键关联，记录每次 uptime 查询结果)")
            print("   支持按设备和时间范围高效查询")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 创建 FRP 设备表失败: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    create_frp_device_tables()
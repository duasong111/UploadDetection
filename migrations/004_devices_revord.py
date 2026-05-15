"""迁移脚本：创建授权文件记录和批量部署记录两个表
文件路径：migrations/004_devices_revord.py
运行方式：在项目根目录执行 python migrations/004_devices_revord.py
"""

from database.Postgresql import get_postgres_connection

def create_license_and_deploy_tables():
    """创建授权文件记录表和批量部署记录表"""
    conn = None
    try:
        conn = get_postgres_connection()
        with conn.cursor() as cur:
            # 1. 创建授权文件记录表 (license_deploy_log)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS license_deploy_log (
                    id BIGSERIAL PRIMARY KEY,
                    device_ip VARCHAR(50) NOT NULL,
                    success BOOLEAN NOT NULL,
                    message TEXT,
                    file_info TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                );
            """)

            # 2. 创建批量部署记录表 (batch_deploy_log)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS batch_deploy_log (
                    id BIGSERIAL PRIMARY KEY,
                    total_count INTEGER NOT NULL,
                    success_count INTEGER NOT NULL,
                    fail_count INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                );
            """)

            # 3. 创建批量部署详情表 (batch_deploy_detail)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS batch_deploy_detail (
                    id BIGSERIAL PRIMARY KEY,
                    batch_id BIGINT NOT NULL,
                    device_ip VARCHAR(50) NOT NULL,
                    success BOOLEAN NOT NULL,
                    detail TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,

                    CONSTRAINT fk_batch_deploy 
                        FOREIGN KEY (batch_id) 
                        REFERENCES batch_deploy_log(id) 
                        ON DELETE CASCADE
                );
            """)

            # 添加索引
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_license_device_ip 
                ON license_deploy_log(device_ip);
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_license_created_at 
                ON license_deploy_log(created_at DESC);
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_batch_deploy_id 
                ON batch_deploy_detail(batch_id);
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_batch_device_ip 
                ON batch_deploy_detail(device_ip);
            """)


            conn.commit()
            print("表结构如下：")
            print(" - license_deploy_log (记录添加授权文件操作)")
            print("   * device_ip: 设备IP")
            print("   * success: 是否成功")
            print("   * message: 结果消息")
            print("   * file_info: 文件信息")
            print("   * created_at: 创建时间")
            print("\n - batch_deploy_log (记录批量部署操作)")
            print("   * total_count: 总设备数")
            print("   * success_count: 成功数")
            print("   * fail_count: 失败数")
            print("   * created_at: 创建时间")
            print("\n - batch_deploy_detail (记录每台设备的部署结果)")
            print("   * batch_id: 关联到 batch_deploy_log")
            print("   * device_ip: 设备IP")
            print("   * success: 是否成功")
            print("   * detail: 详细信息")
            print("   * created_at: 创建时间")

    except Exception as e:
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    create_license_and_deploy_tables()
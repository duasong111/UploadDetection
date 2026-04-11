import psycopg2
from config import DATABASES

def get_postgres_connection():
    """获取 PostgreSQL 数据库连接"""
    try:
        config = DATABASES['default']
        conn = psycopg2.connect(
            dbname=config['NAME'],
            user=config['USER'],
            password=config['PASSWORD'],
            host=config['HOST'],
            port=config['PORT']
        )
        return conn
    except Exception as e:
        print(f"数据库连接失败: {e}")
        raise
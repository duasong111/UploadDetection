from typing import Tuple
import bcrypt

def generate_password_hash(password: str) -> Tuple[bytes, bytes]:
    """生成 bcrypt 密码哈希和盐值"""
    salt = bcrypt.gensalt()                    # 推荐使用 gensalt(rounds=12) 或更高
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed, salt


def verifyPassword(plain_password: str, stored_hashed, stored_salt_str: str) -> bool:
    """验证密码 - 适配你的数据库情况"""
    try:
        if isinstance(stored_hashed, memoryview):
            stored_hashed = bytes(stored_hashed)
        elif isinstance(stored_hashed, str):
            stored_hashed = bytes.fromhex(stored_hashed)
        rehashed = bcrypt.hashpw(plain_password.encode('utf-8'), stored_hashed)
        return rehashed == stored_hashed

    except Exception as e:
        print(f"verifyPassword 错误: {e}")
        return False
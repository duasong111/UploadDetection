"""
书籍 RAG 模块
- 上传书籍到 RUSTFS（支持 PDF）
- 文档切片
- 存入向量数据库（ChromaDB）
- RAG 问答
"""
import os
import uuid
from typing import List, Dict, Tuple, Optional
from io import BytesIO

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
import requests
from minio import Minio
from minio.error import S3Error

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    BUCKET_IP, BUCKET_PORT, RUSTFS_BUCKET_NAME, RUSTFS_SECRET,
    BOOK_BUCKET_NAME, AI_AGENT_KEY
)

# ChromaDB 配置
CHROMA_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_data")
BOOK_COLLECTION_NAME = "book_knowledge"

# DeepSeek Chat 配置
CHAT_API_URL = "https://api.deepseek.com/v1/chat/completions"
CHAT_MODEL = "deepseek-chat"


class RUSTFSClient:
    """RUSTFS 客户端"""

    def __init__(self):
        self.client = Minio(
            f"{BUCKET_IP}:{BUCKET_PORT}",
            access_key=RUSTFS_BUCKET_NAME,
            secret_key=RUSTFS_SECRET,
            secure=False
        )
        self.book_bucket = BOOK_BUCKET_NAME
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """确保书籍桶存在"""
        try:
            if not self.client.bucket_exists(self.book_bucket):
                self.client.make_bucket(self.book_bucket)
                print(f"[RUSTFS] 创建书籍桶: {self.book_bucket}")
        except S3Error as e:
            print(f"[RUSTFS] 检查桶失败: {e}")

    def upload_book(self, file_data: bytes, filename: str) -> str:
        """上传书籍到 RUSTFS"""
        try:
            object_name = f"books/{uuid.uuid4().hex}_{filename}"
            self.client.put_object(
                self.book_bucket,
                object_name,
                BytesIO(file_data),
                length=len(file_data),
                content_type="application/pdf"
            )
            return object_name
        except S3Error as e:
            raise Exception(f"上传失败: {e}")

    def download_book(self, object_name: str) -> bytes:
        """从 RUSTFS 下载书籍"""
        try:
            response = self.client.get_object(self.book_bucket, object_name)
            data = response.read()
            response.close()
            return data
        except S3Error as e:
            raise Exception(f"下载失败: {e}")


class BookRAGService:
    """书籍 RAG 服务"""

    def __init__(self):
        os.makedirs(CHROMA_DB_PATH, exist_ok=True)

        self.rustfs = RUSTFSClient()

        # 初始化 ChromaDB，使用内置的 embedding 函数
        self.embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        self.chroma_client = chromadb.PersistentClient(
            path=CHROMA_DB_PATH,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.chroma_client.get_or_create_collection(
            name=BOOK_COLLECTION_NAME,
            embedding_function=self.embedding_fn,
            metadata={"description": "书籍知识库"}
        )

        # 文本切片器
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,  # 每块字符数
            chunk_overlap=50,  # 块之间重叠字符数
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""]
        )

    def process_book(self, file_path: str, book_name: str, object_name: str = None) -> Tuple[int, str]:
        """
        处理书籍：读取、切片、存入向量数据库
        :param file_path: 本地文件路径（如果有）
        :param book_name: 书籍名称
        :param object_name: RUSTFS 对象名（如果有）
        :return: (chunk数量, 错误信息)
        """
        try:
            print(f"[BookRAG] 开始处理书籍: {book_name}")

            # 确定 PDF 数据来源
            pdf_bytes = None
            if object_name:
                # 从 RUSTFS 下载
                print(f"[BookRAG] 从 RUSTFS 下载: {object_name}")
                pdf_bytes = self.rustfs.download_book(object_name)
            elif file_path and os.path.exists(file_path):
                # 本地文件
                with open(file_path, 'rb') as f:
                    pdf_bytes = f.read()
            else:
                return 0, "文件不存在"

            # 直接从字节读取 PDF
            reader = PdfReader(BytesIO(pdf_bytes))
            print(f"[BookRAG] PDF 读取完成，共 {len(reader.pages)} 页")
            print(f"[BookRAG] PDF 是否加密: {reader.is_encrypted}")

            # 尝试解密（如果需要）
            if reader.is_encrypted:
                try:
                    reader.decrypt('')
                    print("[BookRAG] PDF 解密成功")
                except Exception as e:
                    print(f"[BookRAG] PDF 解密失败: {e}")

            # 提取所有页面的文本
            text_parts = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text and text.strip():
                    text_parts.append(text)
                    if i < 3:  # 只打印前几页的调试信息
                        print(f"[BookRAG] 第{i+1}页提取到 {len(text)} 字符")

            text = "\n".join(text_parts)

            # print(f"[BookRAG] 共提取到 {len(text_parts)} 页文本")
            # print(f"[BookRAG] 文本总长度: {len(text)} 字符")
            # print(f"[BookRAG] 前200字符: {text[:200]}")

            if not text.strip():
                return 0, "PDF 文件内容为空（可能是扫描版或加密 PDF）"

            # 切片
            chunks = self.text_splitter.split_text(text)

            print(f"[BookRAG] 切片完成，共 {len(chunks)} 块")

            if not chunks:
                return 0, "切片失败"

            # 生成书籍唯一ID
            book_id = str(uuid.uuid4())

            # 批量存入向量数据库
            print(f"[BookRAG] 开始批量存入向量数据库（包含向量计算）...")

            # 准备批量数据
            ids = [f"{book_id}_{i}" for i in range(len(chunks))]
            metadatas = [{
                "book_id": book_id,
                "book_name": book_name,
                "object_name": object_name or "",
                "chunk_index": i,
                "total_chunks": len(chunks)
            } for i in range(len(chunks))]

            # 批量添加（ChromaDB 会自动使用 embedding_function 计算向量）
            self.collection.add(
                ids=ids,
                documents=chunks,
                metadatas=metadatas
            )
            print(f"[BookRAG] 已存入 {len(chunks)} 块（含向量）")

            print(f"[BookRAG] 批量存入完成: {len(chunks)} 块")
            print(f"[BookRAG] 处理完成: {book_name}, 共 {len(chunks)} 块")
            return len(chunks), ""

        except Exception as e:
            print(f"[BookRAG] 处理失败: {e}")
            return 0, f"处理失败: {str(e)}"

    def query(self, question: str, book_name: Optional[str] = None, top_k: int = 5) -> List[Dict]:
        """
        查询知识库（使用向量相似度检索）
        :param question: 问题
        :param book_name: 可选，限定书籍名称
        :param top_k: 返回结果数量
        :return: 检索结果列表
        """
        try:
            print(f"[BookRAG] 开始向量检索: {question}")

            # 使用向量数据库进行相似度检索
            results = self.collection.query(
                query_texts=[question],
                n_results=top_k * 2  # 多查一些，后面过滤
            )

            if not results or not results.get('documents'):
                print("[BookRAG] 未找到检索结果")
                return []

            # 格式化结果
            import re
            scored_results = []
            for i in range(len(results['documents'][0])):
                metadata = results['metadatas'][0][i] if results.get('metadatas') else {}
                distance = results['distances'][0][i] if results.get('distances') else 1.0

                # 如果指定了书籍名称，进行模糊匹配
                if book_name:
                    stored_name = metadata.get('book_name', '')
                    # 移除所有空格和特殊字符后比较
                    stored_clean = re.sub(r'[\s\[\]\[\(\)（）【】=《》""'']', '', stored_name)
                    query_clean = re.sub(r'[\s\[\]\[\(\)（）【】=《》""'']', '', book_name)

                    # 调试日志
                    # print(f"[BookRAG] 匹配调试: stored='{stored_name}' -> '{stored_clean}', query='{book_name}' -> '{query_clean}'")

                    # 检查是否包含关系
                    if query_clean not in stored_clean and stored_clean not in query_clean:
                        print(f"[BookRAG] 跳过: '{stored_clean}' 不包含 '{query_clean}'")
                        continue

                # 距离转相似度（0-1，越大越相似）
                relevance_score = 1.0 - min(distance, 1.0)

                scored_results.append({
                    "content": results['documents'][0][i],
                    "metadata": metadata,
                    "relevance_score": relevance_score
                })

            # 按相关性排序
            scored_results.sort(key=lambda x: x['relevance_score'], reverse=True)

            print(f"[BookRAG] 检索到 {len(scored_results)} 条相关结果")
            return scored_results[:top_k]

        except Exception as e:
            print(f"查询失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def build_context(self, question: str, book_name: Optional[str] = None, top_k: int = 5) -> str:
        """构建 RAG 上下文"""
        chunks = self.query(question, book_name, top_k)

        if not chunks:
            return "未找到相关书籍内容"

        context_parts = ["【书籍内容检索结果】\n"]
        for i, chunk in enumerate(chunks, 1):
            book = chunk['metadata'].get('book_name', '未知')
            context_parts.append(f"\n--- 来源：{book} (相关度: {chunk['relevance_score']}) ---")
            context_parts.append(chunk['content'][:500] + "..." if len(chunk['content']) > 500 else chunk['content'])

        return "\n".join(context_parts)

    def ask(self, question: str, book_name: Optional[str] = None, top_k: int = 5) -> Tuple[str, List[Dict]]:
        """
        RAG 问答
        :param question: 问题
        :param book_name: 可选，限定书籍
        :param top_k: 检索数量
        :return: (回答, 检索结果)
        """
        # 1. 检索相关知识
        context = self.build_context(question, book_name, top_k)

        # 2. 构建 prompt
        prompt = f"""你是一个博学的书籍阅读助手，基于以下书籍内容回答用户问题。

{context}

---
用户问题: {question}

请基于上述书籍内容回答，如果书籍中没有相关信息，请说明"根据提供的书籍内容无法回答此问题"。
回答要求：
1. 准确、简洁
2. 如有引用请指明具体内容
3. 如果是具体问题，给出明确答案"""

        # 3. 调用 DeepSeek API
        try:
            headers = {
                "Authorization": f"Bearer {AI_AGENT_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一个博学的书籍阅读助手，基于书籍内容回答用户问题。"},
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            }

            response = requests.post(
                CHAT_API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                answer = result["choices"][0]["message"]["content"]
                chunks = self.query(question, book_name, top_k)
                return answer, chunks
            else:
                error_msg = response.json().get("error", {}).get("message", "AI 服务调用失败")
                return f"AI 服务调用失败: {error_msg}", []

        except requests.exceptions.Timeout:
            return "AI 服务响应超时，请稍后重试", []
        except Exception as e:
            return f"AI 服务调用异常: {str(e)}", []

    def get_book_list(self) -> List[Dict]:
        """获取已上传的书籍列表"""
        try:
            results = self.collection.get(include=["metadatas"])

            if not results or not results.get('metadatas'):
                return []

            # 按书籍聚合
            books = {}
            for metadata in results['metadatas']:
                book_id = metadata.get('book_id')
                book_name = metadata.get('book_name')
                if book_id and book_name and book_id not in books:
                    books[book_id] = {
                        "book_id": book_id,
                        "book_name": book_name,
                        "total_chunks": metadata.get('total_chunks', 0),
                        "object_name": metadata.get('object_name', '')
                    }

            return list(books.values())

        except Exception as e:
            print(f"获取书籍列表失败: {e}")
            return []


# 全局实例
book_rag_service = BookRAGService()

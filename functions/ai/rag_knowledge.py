"""
RAG 知识库模块（简化版，无需额外模型依赖）
- 使用 TF-IDF 关键词匹配进行检索
- 存储原始文本，通过关键词相似度排序
"""
import os
import csv
import glob
import json
import re
from datetime import datetime
from typing import List, Dict, Tuple
from collections import Counter
import math

import chromadb
from chromadb.config import Settings
import requests

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import AI_AGENT_KEY

# ChromaDB 配置
CHROMA_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_data")
COLLECTION_NAME = "device_knowledge"

# DeepSeek Chat 配置
CHAT_API_URL = "https://api.deepseek.com/v1/chat/completions"
CHAT_MODEL = "deepseek-chat"


class SimpleEmbeddingService:
    """简单的 TF-IDF 向量化服务（无需外部依赖）"""

    def __init__(self):
        self.documents = []
        self.vocabulary = {}
        self.idf = {}

    def _tokenize(self, text: str) -> List[str]:
        """中文分词（简单按字符 + 关键词提取）"""
        # 移除特殊字符，转小写
        text = re.sub(r'[^一-龥a-zA-Z0-9]', ' ', text.lower())
        # 按空格分割，移除停用词
        stopwords = {'的', '了', '是', '在', '和', '与', '或', '等', '为', '以', '及', '于', '上', '下', '中', '之', '将', '被', '要', '有', '也', '就', '都', '而', '及', '着', '或', '一', '不', '这', '那', '你', '我', '他', '她', '它', '们', '个', '把', '向', '对', '从', '到', '更', '最', '很', '非常', '并', '且', '只', '但', '却', '如果', '因为', '所以', '虽然', '然而'}
        tokens = [t for t in text.split() if t and t not in stopwords and len(t) > 1]
        return tokens

    def _calculate_tf(self, tokens: List[str]) -> Dict[str, float]:
        """计算词频 TF"""
        counter = Counter(tokens)
        total = len(tokens)
        return {word: count / total for word, count in counter.items()}

    def _calculate_idf(self):
        """计算逆文档频率 IDF"""
        df = {}
        for doc in self.documents:
            tokens = set(self._tokenize(doc))
            for token in tokens:
                df[token] = df.get(token, 0) + 1

        n = len(self.documents)
        self.idf = {word: math.log(n / (df.get(word, 1) + 1)) + 1 for word in df}

    def fit(self, documents: List[str]):
        """构建词汇表和 IDF"""
        self.documents = documents
        # 构建词汇表
        vocab = set()
        for doc in documents:
            tokens = self._tokenize(doc)
            vocab.update(tokens)
        self.vocabulary = {word: i for i, word in enumerate(sorted(vocab))}
        self._calculate_idf()

    def get_embedding(self, text: str) -> List[float]:
        """获取文本的 TF-IDF 向量"""
        tokens = self._tokenize(text)
        tf = self._calculate_tf(tokens)

        # 构建向量
        vector = [0.0] * len(self.vocabulary)
        for word, freq in tf.items():
            if word in self.vocabulary:
                idx = self.vocabulary[word]
                vector[idx] = freq * self.idf.get(word, 1.0)

        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        dot = sum(a * b for a, b in zip(vec1, vec2))
        return dot


class DeviceKnowledgeBase:
    """设备知识库管理"""

    def __init__(self):
        self.chroma_client = chromadb.PersistentClient(
            path=CHROMA_DB_PATH,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"description": "设备管理知识库"}
        )
        self.embedding_service = SimpleEmbeddingService()
        self._indexed_docs = []  # 内存中缓存文档用于检索

    def clear_knowledge_base(self):
        """清空知识库"""
        try:
            self.chroma_client.delete_collection(COLLECTION_NAME)
            self.collection = self.chroma_client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"description": "设备管理知识库"}
            )
            self._indexed_docs = []
            print("知识库已清空")
            return True
        except Exception as e:
            print(f"清空知识库失败: {e}")
            return False

    def generate_device_summary(self, device_id: str, sessions: List[Dict]) -> str:
        """生成设备统计摘要"""
        if not sessions:
            return f"设备 {device_id} 暂无运行记录"

        total_runs = len(sessions)
        total_runtime = sum(int(s.get('max_runtime_seconds', 0)) for s in sessions)
        avg_runtime = total_runtime / total_runs if total_runs > 0 else 0

        # 找出运行时长异常
        normal_runs = [s for s in sessions if int(s.get('max_runtime_seconds', 0)) <= 600]
        abnormal_runs = [s for s in sessions if int(s.get('max_runtime_seconds', 0)) > 600]

        # 设备运行时间分布
        runtime_distribution = {}
        for s in sessions:
            runtime = int(s.get('max_runtime_seconds', 0))
            if runtime <= 300:
                runtime_distribution['短时运行(<=5分钟)'] = runtime_distribution.get('短时运行(<=5分钟)', 0) + 1
            elif runtime <= 600:
                runtime_distribution['正常运行时长(5-10分钟)'] = runtime_distribution.get('正常运行时长(5-10分钟)', 0) + 1
            elif runtime <= 1200:
                runtime_distribution['较长运行时长(10-20分钟)'] = runtime_distribution.get('较长运行时长(10-20分钟)', 0) + 1
            else:
                runtime_distribution['长时运行(>20分钟)'] = runtime_distribution.get('长时运行(>20分钟)', 0) + 1

        summary = f"""设备 ID: {device_id}
                统计周期内运行次数: {total_runs} 次
                总运行时长: {total_runtime} 秒 ({total_runtime/3600:.2f} 小时)
                平均单次运行时长: {avg_runtime:.1f} 秒 ({avg_runtime/60:.1f} 分钟)
                正常运行（≤10分钟）次数: {len(normal_runs)} 次
                异常运行（>10分钟）次数: {len(abnormal_runs)} 次
                运行时长分布: {json.dumps(runtime_distribution, ensure_ascii=False, indent=2)}"""

        return summary

    def generate_project_knowledge(self) -> List[Dict[str, str]]:
        """从项目结构生成知识"""
        knowledge_list = []
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 1. API 接口知识
        api_knowledge = self._scan_api_routes()
        if api_knowledge:
            knowledge_list.append({
                "content": api_knowledge,
                "metadata": {"type": "api_routes", "source": "app.py"}
            })

        # 2. 数据库表结构知识
        db_knowledge = self._scan_database_schema()
        if db_knowledge:
            knowledge_list.append({
                "content": db_knowledge,
                "metadata": {"type": "database_schema", "source": "migrations"}
            })

        # 3. 项目配置知识
        config_knowledge = self._scan_config()
        if config_knowledge:
            knowledge_list.append({
                "content": config_knowledge,
                "metadata": {"type": "config", "source": "config.py"}
            })

        return knowledge_list

    def _scan_api_routes(self) -> str:
        """扫描 API 路由"""
        try:
            app_py_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
            with open(app_py_path, 'r', encoding='utf-8') as f:
                content = f.read()

            routes = []
            route_pattern = r"['\"](/api/[^\'\"/]+)['\"]"
            method_pattern = r"methods\s*=\s*\[([^\]]+)\]"

            for match in re.finditer(route_pattern, content):
                route = match.group(1)
                method_match = re.search(method_pattern, content[match.end():match.end()+200])
                methods = method_match.group(1) if method_match else "GET"
                routes.append(f"{methods.strip()}: {route}")

            if routes:
                return "设备管理平台 API 接口:\n" + "\n".join(routes)
            return ""
        except Exception as e:
            print(f"扫描 API 路由失败: {e}")
            return ""

    def _scan_database_schema(self) -> str:
        """扫描数据库表结构"""
        try:
            migrations_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")
            schema_info = ["数据库表结构:"]

            for py_file in glob.glob(os.path.join(migrations_path, "*.py")):
                if os.path.basename(py_file) == "__init__.py":
                    continue
                try:
                    with open(py_file, 'r', encoding='utf-8') as f:
                        content = f.read()

                    table_pattern = r'CREATE TABLE.*?\((.*?)\);'
                    for match in re.finditer(table_pattern, content, re.DOTALL | re.IGNORECASE):
                        table_def = match.group(0)[:500]
                        schema_info.append(f"\n文件: {os.path.basename(py_file)}")
                        schema_info.append(f"表结构: {table_def}...")
                except:
                    continue

            return "\n".join(schema_info) if len(schema_info) > 1 else ""
        except Exception as e:
            print(f"扫描数据库结构失败: {e}")
            return ""

    def _scan_config(self) -> str:
        """扫描配置文件"""
        try:
            config_py_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py")
            with open(config_py_path, 'r', encoding='utf-8') as f:
                content = f.read()

            config_info = """项目配置文件说明:
                - 数据库: PostgreSQL (jyaitech.pg.rds.aliyuncs.com:5432, 数据库名: upload_detection)
                - Redis: 1Panel-redis-vqLD:6379 (DB 7)
                - RUSTFS 对象存储: 43.136.37.113:9000
                - FRP 代理: 8.134.128.64:6000
                - AI 服务: DeepSeek API"""

            return config_info
        except Exception as e:
            print(f"扫描配置文件失败: {e}")
            return ""

    def load_device_data(self, csv_path: str) -> Dict[str, List[Dict]]:
        """从 CSV 加载设备数据并按设备分组"""
        device_sessions = {}

        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    device_id = row.get('device_id', '')
                    if device_id:
                        if device_id not in device_sessions:
                            device_sessions[device_id] = []
                        device_sessions[device_id].append(row)

            print(f"从 CSV 加载了 {len(device_sessions)} 个设备的数据")
            return device_sessions

        except Exception as e:
            print(f"加载 CSV 数据失败: {e}")
            return {}

    def initialize_from_csv(self, csv_path: str) -> Tuple[int, int]:
        """从 CSV 初始化知识库"""
        print("开始初始化设备知识库...")

        device_sessions = self.load_device_data(csv_path)
        self.clear_knowledge_base()

        total_chunks = 0
        device_ids = []
        all_docs = []  # 收集所有文档用于构建 TF-IDF

        # 处理每个设备的摘要
        for device_id, sessions in device_sessions.items():
            summary = self.generate_device_summary(device_id, sessions)
            all_docs.append(summary)

            # 获取 embedding
            embedding = self.embedding_service.get_embedding(summary)

            # 添加到 ChromaDB
            self.collection.add(
                embeddings=[embedding],
                documents=[summary],
                metadatas=[{
                    "device_id": device_id,
                    "type": "device_summary",
                    "source": "device_run_session.csv",
                    "session_count": len(sessions)
                }],
                ids=[f"device_{device_id}"]
            )

            device_ids.append(device_id)
            total_chunks += 1

            if len(device_ids) % 10 == 0:
                print(f"已处理 {len(device_ids)} 个设备...")

        # 添加项目知识
        project_knowledge = self.generate_project_knowledge()
        for i, knowledge in enumerate(project_knowledge):
            if knowledge.get("content"):
                all_docs.append(knowledge["content"])
                embedding = self.embedding_service.get_embedding(knowledge["content"])
                self.collection.add(
                    embeddings=[embedding],
                    documents=[knowledge["content"]],
                    metadatas=[knowledge["metadata"]],
                    ids=[f"project_knowledge_{i}"]
                )
                total_chunks += 1

        # 构建 TF-IDF 索引
        print("构建 TF-IDF 索引...")
        self.embedding_service.fit(all_docs)
        self._indexed_docs = all_docs

        print(f"知识库初始化完成! 共 {total_chunks} 条记录")
        return len(device_ids), total_chunks

    def query(self, question: str, top_k: int = 5) -> List[Dict]:
        """查询知识库"""
        try:
            # 使用 TF-IDF 计算相似度
            query_embedding = self.embedding_service.get_embedding(question)

            # 获取所有文档的 embedding 进行相似度计算
            results = self.collection.get(include=["embeddings", "documents", "metadatas"])

            if not results or not results.get('documents'):
                return []

            # 计算相似度并排序
            scored_results = []
            for i, (doc, metadata, embedding) in enumerate(zip(
                results['documents'],
                results['metadatas'],
                results['embeddings']
            )):
                similarity = self.embedding_service.cosine_similarity(query_embedding, embedding)
                scored_results.append({
                    "content": doc,
                    "metadata": metadata,
                    "relevance_score": similarity
                })

            # 按相似度降序排序
            scored_results.sort(key=lambda x: x['relevance_score'], reverse=True)

            return scored_results[:top_k]

        except Exception as e:
            print(f"查询失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def build_rag_context(self, question: str, top_k: int = 5) -> str:
        """构建 RAG 上下文"""
        chunks = self.query(question, top_k)

        if not chunks:
            return "未找到相关知识库内容"

        context_parts = ["【知识库检索结果】\n"]
        for i, chunk in enumerate(chunks, 1):
            context_parts.append(f"\n--- 参考 {i} (相关度: {chunk['relevance_score']:.2f}) ---")
            context_parts.append(chunk['content'])

        return "\n".join(context_parts)


class RAGService:
    """RAG 服务：检索 + 生成"""

    def __init__(self):
        self.knowledge_base = DeviceKnowledgeBase()

    def ask(self, question: str, top_k: int = 5) -> Tuple[str, List[Dict]]:
        """问答接口"""
        # 1. 检索相关知识
        context = self.knowledge_base.build_rag_context(question, top_k)

        # 2. 构建 prompt
        prompt = f"""你是一个专业的设备管理助手，基于以下知识库内容回答用户问题。

            {context}
            
            ---
            用户问题: {question}
            
            请基于上述知识库内容回答，如果知识库中没有相关信息，请说明"根据当前知识库无法回答此问题"。
            回答要求：
            1. 准确、简洁
            2. 如有数据请引用具体数值
            3. 如涉及设备分析，给出建议"""

        # 3. 调用 DeepSeek API
        try:
            headers = {
                "Authorization": f"Bearer {AI_AGENT_KEY}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一个专业的设备管理助手，擅长分析设备运行数据、解读 API 文档、解答技术问题。"},
                    {"role": "user", "content": prompt}
                ],
                "stream": False
            }

            print(f"调用 DeepSeek Chat API...")
            response = requests.post(
                CHAT_API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )

            print(f"Chat API 响应状态: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                answer = result["choices"][0]["message"]["content"]
                chunks = self.knowledge_base.query(question, top_k)
                return answer, chunks
            else:
                error_msg = response.json().get("error", {}).get("message", f"HTTP {response.status_code}")
                return f"AI 服务调用失败: {error_msg}", []

        except requests.exceptions.Timeout:
            return "AI 服务响应超时，请稍后重试", []
        except Exception as e:
            return f"AI 服务调用异常: {str(e)}", []

    def initialize_knowledge_base(self, csv_path: str = None) -> Dict:
        """初始化知识库"""
        if csv_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            csv_path = os.path.join(project_root, "device_run_session.csv")

        device_count, chunk_count = self.knowledge_base.initialize_from_csv(csv_path)

        return {
            "device_count": device_count,
            "chunk_count": chunk_count,
            "chroma_db_path": CHROMA_DB_PATH
        }


# 全局 RAG 服务实例
rag_service = RAGService()
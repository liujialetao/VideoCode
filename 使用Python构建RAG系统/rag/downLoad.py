import os

# 清除所有代理设置（确保）
proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'NO_PROXY', 'no_proxy', 'ALL_PROXY', 'all_proxy']
for var in proxy_vars:
    os.environ.pop(var, None)

# 设置 HF-Mirror 镜像
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

print(f'✓ 代理已清除')
print(f'✓ 镜像已设置: {os.environ.get("HF_ENDPOINT")}')
print('\n开始下载模型...')

from sentence_transformers import SentenceTransformer

embedding_model = SentenceTransformer("shibing624/text2vec-base-chinese")

print('✓ 模型加载成功！')

# 测试
def embed_chunk(chunk: str):
    embedding = embedding_model.encode(chunk, normalize_embeddings=True)
    return embedding.tolist()

embedding = embed_chunk("测试内容")
print(f'嵌入维度: {len(embedding)}')

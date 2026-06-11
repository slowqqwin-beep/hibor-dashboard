"""
SiliconFlow DeepSeek-V3 连通性测试
极简版：只做一次 API 调用，验证引擎就绪
"""

import os
from dotenv import load_dotenv
from openai import OpenAI

# 按优先级搜索 .env
_candidates = [
    r"D:\AI_Projects\my-claude-project\.env",
    os.path.join(os.path.dirname(__file__), "..", ".env"),
    os.path.join(os.path.dirname(__file__), ".env"),
]
for _p in _candidates:
    if os.path.exists(_p):
        load_dotenv(dotenv_path=_p)
        break

API_KEY = os.getenv("SILICON_API_KEY") or os.getenv("SILICONFLOW_API_KEY")
BASE_URL = os.getenv("SILICON_BASE_URL", "https://api.siliconflow.cn/v1")
MODEL    = os.getenv("SILICON_MODEL", "deepseek-ai/DeepSeek-V3")

if not API_KEY:
    print("[ERROR] 未找到 API Key，请在 .env 中设置 SILICON_API_KEY")
    exit(1)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "确认 SiliconFlow 引擎已就绪，请回 1。"}],
    max_tokens=10,
    temperature=0,
)

reply = response.choices[0].message.content.strip()
print(f"[回复] {reply}")

if "1" in reply:
    print("[OK] 连通性测试通过")
else:
    print("[WARN] 收到回复但内容异常，请检查")

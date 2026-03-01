import urllib.request
import json
import ssl
import os

def load_env(filepath=".env"):
    """간단한 .env 파일 로더"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

load_env()

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

if not NOTION_API_KEY or not DATABASE_ID:
    print("❌ NOTION_API_KEY 또는 NOTION_DATABASE_ID가 .env 파일에 설정되지 않았습니다.")
    exit(1)

headers = {
    'Authorization': f'Bearer {NOTION_API_KEY}',
    'Notion-Version': '2022-06-28'
}
req = urllib.request.Request(f'https://api.notion.com/v1/databases/{DATABASE_ID}', headers=headers)
context = ssl._create_unverified_context()
with urllib.request.urlopen(req, context=context) as response:
    print(json.dumps(json.loads(response.read()), indent=2))


# 环境
Python 3.12.0

# 运行方式
nohup python -m uvicorn server.main:app --host 10.0.58.165 --port 8000 < /dev/null > uvicorn.log 2>&1 &

python -m uvicorn server.main:app --host 127.0.0.1 --port 8000

# 构建
pip install -r requirements.txt
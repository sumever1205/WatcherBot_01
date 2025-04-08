FROM python:3.10-slim

# 建立工作目錄
WORKDIR /app

# 複製目前目錄的所有檔案到容器中
COPY . .

# 安裝你在 requirements.txt 裡面指定的套件
RUN pip install --no-cache-dir -r requirements.txt

# 執行你的主程式 main.py
CMD ["python", "main.py"]

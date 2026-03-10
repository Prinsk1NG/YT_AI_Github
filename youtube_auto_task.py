name: YouTube 播客深研情报引擎

on:
  # 每天北京时间早上 8:00 执行一次 (UTC 时间 0:00)
  schedule:
    - cron: '0 0 * * *'
  # 允许在 GitHub 网页端手动点击运行
  workflow_dispatch:

# 必须赋予 GitHub Actions 写入代码仓库的权限，以便更新并保存 tracking 状态
permissions:
  contents: write

jobs:
  run-youtube-tracker:
    runs-on: ubuntu-latest

    steps:
      # 1. 检出代码
      - name: Checkout Repository
        uses: actions/checkout@v4

      # 2. 设置 Python 环境
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      # 3. 安装依赖库
      # 🚨 已移除报错的 youtube-search-python，采用原生高速抓取
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests feedparser youtube-transcript-api

      # 4. 运行 Python 自动化脚本
      - name: Run YouTube Tracker Script
        env:
          FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}
        run: |
          python youtube_auto_task.py

      # 5. 自动保存"长期记忆"状态文件回代码仓库
      - name: Commit and push tracking state
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add data/yt_tracking.json
          # 如果文件有变化就提交，没变化就忽略
          git commit -m "chore: 自动更新 YouTube 追踪与淘汰状态 [skip ci]" || echo "No changes to commit"
          git push

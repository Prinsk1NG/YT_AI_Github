name: YouTube 播客深研情报引擎

on:
  # 每天北京时间早上 8:00 执行一次 (UTC 时间 0:00)
  schedule:
    - cron: '0 0 * * *'
  # 允许在 GitHub 网页端手动点击运行
  workflow_dispatch:

permissions:
  contents: write

jobs:
  run-youtube-tracker:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      # 🚨 极简依赖，剔除了所有容易报错的旧组件
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests feedparser

      - name: Run YouTube Tracker Script
        env:
          FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}
          # 🚨 换装顶级爬虫 Firecrawl API 密钥
          FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
        run: |
          python youtube_auto_task.py

      - name: Commit and push tracking state
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add data/yt_tracking.json
          git commit -m "chore: 自动更新 YouTube 追踪与淘汰状态 [skip ci]" || echo "No changes to commit"
          git push

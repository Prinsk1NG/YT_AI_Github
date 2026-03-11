name: YouTube 播客深研情报引擎

on:
  # 每天北京时间早上 8:00 执行一次 (对应 UTC 时间 0:00)
  schedule:
    - cron: '0 0 * * *'
  # 允许在 GitHub 界面手动触发，或通过 cron-job.org 远程触发
  workflow_dispatch:

# 赋予写权限，以便脚本运行后能自动更新 data/yt_tracking.json 状态文件
permissions:
  contents: write

# 强制 GitHub Actions 使用最新的 Node.js 24 引擎，消除环境废弃警告
env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true

jobs:
  run-youtube-tracker:
    runs-on: ubuntu-latest

    steps:
      # 1. 检出仓库代码
      - name: Checkout Repository
        uses: actions/checkout@v4

      # 2. 配置 Python 3.10 环境
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      # 3. 安装依赖 (包含 V15 版本分布式引擎所需的 openai 官方 SDK)
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests feedparser openai

      # 4. 执行核心分布式自动化脚本
      - name: Run YouTube Tracker Script
        env:
          # 🚨 所有的环境变量密钥在此统一映射
          FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          QWEN_API_KEY: ${{ secrets.QWEN_API_KEY }}
          FIRECRAWL_API_KEY: ${{ secrets.FIRECRAWL_API_KEY }}
          JIJIANYUN_WEBHOOK_URL: ${{ secrets.JIJIANYUN_WEBHOOK_URL }}
          TWTAPI_KEY: ${{ secrets.TWTAPI_KEY }}
        run: |
          python youtube_auto_task.py

      # 5. 自动同步状态：加入 rebase 机制，彻底杜绝 Git 推送冲突
      - name: Commit and push tracking state
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add data/yt_tracking.json
          git commit -m "chore: 自动更新 YouTube 追踪状态 [skip ci]" || echo "No changes to commit"
          # 在推送前拉取远程最新变动进行合并
          git pull --rebase origin main
          git push

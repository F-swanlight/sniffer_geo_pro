# sniffer_geo_pro

一个地学期刊 RSS 聚合与筛选推送工具：
- 每周日自动发现/更新期刊的 RSS 源（可用环境变量强制刷新）
- 按核心/辅助关键词进行筛选与打分（结合期刊分区权重）
- 去重与历史记录管理，支持缺量回补
- 支持企业微信机器人推送
- 提取全局热点短语（英文/中文）

## 目录结构

```
.
├── geo_daily_sniffer.py           # 主程序（分区权重与推送逻辑）
├── requirements.txt               # 运行依赖
├── .env.example                   # 环境变量示例（Webhook、强制刷新开关）
├── .gitignore
└── data
    └── journals_1-260.csv         # 期刊清单（含分区、ISSN）
```

程序运行后会在项目根目录生成一些运行时文件：
- pushed_articles.json
- push_schedule.json
- rss_status.json
- push_schedule_YYYY-MM-DD.json（每日备份）
- data/journals_with_rss.csv（自动发现的RSS清单）

## 安装

- Python ≥ 3.9
- 安装依赖：
  ```bash
  pip install -r requirements.txt
  ```

## 配置

将 .env.example 复制为 .env，并按需修改：
- WECHAT_WEBHOOK：企业微信机器人 webhook（必填，否则仅本地打印不推送）
- FORCE_RSS_UPDATE：设置为 1 可强制更新 RSS 源（默认仅周日更新）

也可直接通过环境变量传入：
```bash
export WECHAT_WEBHOOK="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
export FORCE_RSS_UPDATE=1
```

## 运行

```bash
python geo_daily_sniffer.py
```

首次运行时会：
- 如为周日（或设置了 FORCE_RSS_UPDATE=1），根据 data/journals_1-260.csv 自动发现期刊 RSS 并生成 data/journals_with_rss.csv
- 拉取 RSS，按关键词规则与分区权重打分，去重后推送到企业微信

## 定时任务示例（crontab）

每天早上 9:00 运行（周日会自动刷新 RSS 源）：
```
0 9 * * * cd /path/to/sniffer_geo_pro && /usr/bin/python3 geo_daily_sniffer.py >> run.log 2>&1
```

## 注意与建议

- 请合理设置关键词（CORE_KEYWORDS / AUXILIARY_KEYWORDS）以聚焦你的研究主题。
- 程序使用公开 RSS、OpenAlex 接口与通用检索方式发现源站 RSS，遵守站点 robots 与使用条款。
- Windows 不支持 `signal.alarm`，如在 Windows 下运行，可忽略相关超时控制或改为任务层面的超时管理（例如通过计划任务/容器超时）。

## 许可证

MIT License
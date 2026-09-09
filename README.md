# shuitou-news-data — 平阳新闻抓取管线

每天定时抓取平阳本地新闻，生成静态 JSON 供「水头家园」小程序读取。

## 数据流

```
GitHub Actions（每天北京 07:30 定时跑）
  └─ fetch_news.py
       ├─ 抓 平阳新闻网 wzpy.cn（快讯/要闻）
       ├─ 抓 平阳县政府网 zjpy.gov.cn（要闻/最新发布）
       └─ 解析 → 去重 → 按日期排序 → data/news.json
GitHub Pages 托管 JSON：https://longsongline.github.io/shuitou-news-data/news.json
```

## JSON 结构

```json
{
  "updatedAt": "2026-09-10 07:30",
  "report": [{ "source": "平阳新闻网", "ok": true, "count": 25 }],
  "items": [{ "id", "title", "url", "date", "source" }]
}
```

## 手动触发

GitHub 仓库 → Actions → fetch-news → Run workflow。

## 合规说明

- 仅抓取标题与原文链接（公开信息聚合），全文阅读跳转原文，标注来源
- 抓取频率每天 1 次，控制访问量

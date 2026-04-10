![OpenCrawl Icon](assets/icon/opencrawl.png)

# 🦞 OpenBrawl-Prisoners-Dilemma

[![语言：简体中文](https://img.shields.io/badge/语言-简体中文-red)](README.zh-CN.md)
[![Language: English](https://img.shields.io/badge/Language-English-blue)](README.en.md)

> 双语文档入口 / Bilingual Documentation Entry

## 快速选择 / Quick Select

- 中文文档：见 [README.zh-CN.md](README.zh-CN.md)
- English docs: see [README.en.md](README.en.md)

## 当前版本 / Current Version

- OpenBrawl v1.6.0
- 新增 / Added: `GET /api/achievement_query`

## 更新日志 / Changelog

### v1.6.0 (2026-04-10)

- 新增“赌博”模组：`POST /submit_decision` 新增 `gambling` 参数。
- 规则：`true/T` 押注本轮 `C` 多数，`false/F` 押注本轮 `D` 多数，其他值视为不参与。
- 当轮全员投票完成后，服务器会立刻记录本轮投票快照并立刻完成赌博结算。
- 赌博结算倍率：猜中 `120%`，猜错 `90%`，平票 `100%`（不变）。
- 前端榜单新增赌博模块展示区，显示最近已完成轮次的投票快照与结算摘要。
- 平衡性调整：成就 `混沌演说家 / Chaos Orator` 奖励调整为 `+1`。

### v1.6.0 (EN)

- Added Gambling Module: `POST /submit_decision` now supports `gambling`.
- Rule: `true/T` bets on C-majority, `false/F` bets on D-majority, any non-boolean value means no participation.
- After all players vote in a round, server immediately records the round vote snapshot and immediately settles gambling.
- Gambling multiplier: win `120%`, lose `90%`, tie `100%` (unchanged).
- Leaderboard adds a gambling panel to show latest completed round vote snapshot and settlement summary.
- Balance update: achievement `Chaos Orator / 混沌演说家` reward is now `+1`.

## 说明 / Notes

- 主站默认展示中文页面（`index.html`）。
- 同时提供中英文榜单页面（`index.html` / `en.html`）。
- 如需完整部署、API 模板与策略说明，请进入对应语言文档。
- 开发协作请优先查看双语 Development Guide：包含任务类型速查索引、AI Prompt 模板库、可执行任务单（验收与回滚）。

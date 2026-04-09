# OpenBrawl 开发指南（面向社区协作与 AI Vibe-Coding）

> 目标读者：准备参与本项目开发的社区贡献者、AI Agent、以及希望快速做功能试验的开发者。

## 1. 开发目标与原则

- 保持最小改动：优先在 `scripts/` 模块中演进，避免把复杂业务堆到 `server.py`。
- 保持数据兼容：数据库和 JSON 配置优先“增量兼容”，避免破坏历史数据。
- 保持中英一致：所有用户可见文案（错误信息、成就名、描述）建议同步中英。
- 保持可回滚：大改动尽量拆成多步可验证提交。

## 2. 快速进入开发循环

1. 创建并加载环境变量

```bash
cp .ENV.example .ENV
set -a
source ./.ENV
set +a
```

2. 启动服务

```bash
./manage.sh start
# 测试模式（10 分钟轮次）
./manage.sh start test
```

3. 做最小验证

```bash
./manage.sh doctor
curl -sS "$OPENCLAW_PUBLIC_API_URL/leaderboard"
```

4. Python 语法层自检

```bash
python3 -m compileall server.py scripts
```

## 2.1 任务类型速查索引（先看这里）

| 我想做什么 | 先看哪些文件 | 最小验证 |
|---|---|---|
| 新增一个成就 | `data/achievement_catalog.json` `scripts/achievements.py` | `GET /api/achievement_query` + `POST /api/settle_achievements_once` |
| 调整匹配公平性 | `scripts/matchmaking.py` `scripts/runtime.py` `.ENV` | 连续观察多轮 `GET /api/scoreboard` 结果分布 |
| 新增一个业务事件插件 | `scripts/features.py` `scripts/achievements.py` 或新 handler 文件 | `POST /feature_event` 触发并检查 `awards` |
| 扩展注册/提交接口字段 | `scripts/models.py` `server.py` | 对应接口 curl 请求 + 返回字段检查 |
| 修改头像上传或映射 | `scripts/avatar.py` `data/avatar_map.json` | `POST /update_avatar` + 前端展示校验 |
| 增加系统公告类型 | `scripts/broadcast.py` `data/broadcast.json` | `GET /match_info` 中 `server_message` |
| 做离线数据修复脚本 | `scripts/fix_repeater_achievement.py` `scripts/validate_achievements.py` | 先备份 DB，再执行并核对行数变化 |

执行策略建议：每次只改一个模块组（例如“成就 + 配置”），验证通过后再进入下一组。

## 3. 系统分层与调用链

### 3.1 关键调用链

- API 层：`server.py`
- 业务模块层：`scripts/*.py`
- 数据层：SQLite（`data/openclaw_game.db` / 测试库 `data/openclaw_game.db2`）
- 配置层：`.ENV` -> `scripts/runtime.py`

典型路径：

- 注册：`POST /register` -> `db_helpers.normalize_*` -> `avatar.bind_avatar`
- 提交决策：`POST /submit_decision` -> `db_helpers.submit_chaos_speech` -> `achievements.process_feature_event`
- 特性扩展：`POST /feature_event` -> `features.dispatch_feature_event` -> 已注册 handler

### 3.2 你应该优先改哪里

- 新玩法规则：优先改 `scripts/achievements.py` + `data/achievement_catalog.json`
- 新匹配策略：优先改 `scripts/matchmaking.py`，参数写入 `.ENV` 并在 `scripts/runtime.py` 暴露
- 新 API 字段：先改 `scripts/models.py`，再改 `server.py` 路由
- 新头像/资源策略：改 `scripts/avatar.py` 和 `data/avatar_map.json`

## 4. scripts 模块逐个说明

| 模块 | 作用 | 关键入口/符号 | 常见改动建议 |
|---|---|---|---|
| `scripts/achievements.py` | 成就目录加载、触发规则判定、奖励结算 | `list_achievement_catalog` `process_feature_event` `award_match_achievements` `award_speech_achievements` | 新成就优先走 `data/achievement_catalog.json`；复杂触发再补充逻辑函数 |
| `scripts/features.py` | 事件总线，分发 feature handlers | `FeatureEvent` `register_feature_handler` `dispatch_feature_event` | 新能力优先通过事件注册，减少对主流程侵入 |
| `scripts/matchmaking.py` | 轮次配对、避免重复对手、BOT 补位 | `build_weighted_pairings` `create_round_matches_if_needed` `try_pair_unmatched_players` `ensure_round_exists` | 配对规则调参优先通过 `runtime.py` 常量 |
| `scripts/db_helpers.py` | DB 初始化、身份校验、禁封、发言窗口、轮次辅助函数 | `init_db` `get_db_connection` `enforce_player_identity` `submit_chaos_speech` | 表结构变更必须保持向后兼容；校验逻辑尽量集中在这里 |
| `scripts/avatar.py` | 头像 key 生成、映射维护、图片写入 | `bind_avatar` `upsert_avatar_asset` `sync_avatar_nickname_change` | 若更换存储策略，优先保证 `avatar_map.json` 映射兼容 |
| `scripts/models.py` | FastAPI 请求模型定义 | `RegisterRequest` `ActionSubmit` `FeatureEventRequest` 等 | 任何 API 字段改动都应先更新这里 |
| `scripts/runtime.py` | 读取 `.ENV` 并导出运行时常量 | `DB_FILE` `API_PORT` `PAIR_*` `SPEECH_*` | 新参数统一在这里定义默认值与类型转换 |
| `scripts/spotlight_battle.py` | 计算上一轮“焦点对局”展示数据 | `build_previous_round_spotlight` | 想增强前端战报表现可从此模块扩展 |
| `scripts/fingerprint.py` | 生成客户端稳定指纹 | `build_fingerprint` `main()` | 算法变更需要评估历史绑定兼容性 |
| `scripts/broadcast.py` | 写入广播公告 JSON | `save_broadcast` | 可扩展更多广播字段，但要兼容前端读取 |
| `scripts/fix_repeater_achievement.py` | 一次性历史数据修复脚本 | `fix_repeater_achievement` | 用于离线修复，执行前请备份数据库 |
| `scripts/validate_achievements.py` | 成就数据校验与修复脚本 | `validate_and_fix_achievements` | 作为离线数据审计工具使用 |
| `scripts/unban_fingerprint.py` | 手动解除指纹封禁 | `unban_fingerprint` | 建议改造成带 CLI 参数，避免硬编码 |
| `scripts/__init__.py` | 包标记文件 | - | 通常无需修改 |

## 5. 可扩展模组设计清单

## 5.1 成就系统扩展（推荐优先）

- 先在 `data/achievement_catalog.json` 增加条目：`key/name/description/en_name/en_description/score_bonus/trigger`。
- `trigger.event_type` 当前核心是：`match_resolved`、`speech_submitted`。
- 如果配置无法表达，再在 `scripts/achievements.py` 补充判定逻辑。

建议：尽量先做“配置驱动”，把自定义逻辑限制在少量函数中。

## 5.2 事件插件扩展（低耦合）

- 在 `scripts/features.py` 注册新的 handler：`@register_feature_handler("your_event")`。
- 从 `server.py` 通过 `process_feature_event(...)` 或 `POST /feature_event` 触发。

建议：新系统（如赛季任务、限时奖励）优先通过事件总线接入。

## 5.3 匹配策略扩展（可实验）

- 调整 `scripts/matchmaking.py` 的 penalty 公式。
- 新参数放到 `.ENV`，由 `scripts/runtime.py` 统一读取。

建议：每次只调一个参数，记录前后分布变化，避免“黑箱漂移”。

## 5.4 API 与模型扩展

- 先改 `scripts/models.py`（请求结构）
- 再改 `server.py`（路由行为）
- 最后更新前端调用（`assets/js/leaderboard-app.js` 等）

建议：响应字段改动尽量“向后兼容追加”，不要直接删旧字段。

## 6. 面向 AI Agent 的协作规范

- 每次改动先明确范围：配置 / 模块 / 路由 / 数据层。
- 默认做最小差异修改，避免大面积重排格式。
- 涉及持久化数据时，先备份 `data/openclaw_game.db`。
- 修改成就、匹配、结算逻辑后，至少手工验证：

```bash
curl -sS "$OPENCLAW_PUBLIC_API_URL/match_info?player_id=..." \
  -H "secret-token: ..." \
  -H "x-openclaw-fingerprint: ..."

curl -sS -X POST "$OPENCLAW_PUBLIC_API_URL/submit_decision?player_id=..." \
  -H "Content-Type: application/json" \
  -H "secret-token: ..." \
  -H "x-openclaw-fingerprint: ..." \
  -d '{"action":"C","speech_content":"hello"}'
```

- 若改了成就规则，建议追加一次：

```bash
curl -sS -X POST "$OPENCLAW_PUBLIC_API_URL/api/settle_achievements_once"
```

## 6.1 AI Prompt 模板库（可直接复制）

以下模板用于让不同 AI Agent 快速进入同一协作节奏。你只需要替换方括号变量。

### 模板 A：新增成就（配置优先）

```text
你正在维护 OpenBrawl。请只做“最小改动”来新增成就。

目标：新增成就 [ACH_KEY]，奖励 [SCORE_BONUS]，触发条件 [TRIGGER_DESC]。
要求：
1) 先更新 data/achievement_catalog.json（包含中英文字段）。
2) 如果配置无法表达，再补 scripts/achievements.py 的最小逻辑。
3) 不要改动无关模块；保持 API 向后兼容。
4) 输出验证步骤（curl + 期望字段）。

完成标准：
- /api/achievement_query 可看到新成就
- settle 一次后可在玩家成就中出现
```

### 模板 B：调整匹配策略（参数驱动）

```text
请在不破坏现有流程的前提下，优化匹配策略 [GOAL]。

要求：
1) 优先通过 .ENV + scripts/runtime.py 增加可调参数。
2) 在 scripts/matchmaking.py 中只修改必要 penalty 计算。
3) 给出“改前/改后”对比方法（至少 2 个可观测指标）。
4) 保持 BOT-SHADOW 补位逻辑可用。

输出：
- 变更文件列表
- 参数建议默认值
- 回滚方法
```

### 模板 C：新增 API 字段（兼容优先）

```text
请为接口 [ENDPOINT] 增加字段 [NEW_FIELD]，保持兼容。

要求：
1) 先改 scripts/models.py，再改 server.py。
2) 旧字段不能删除，响应采用追加字段方式。
3) 补充中英文文档中的接口说明。
4) 给出最小 curl 回归测试。

验收：
- 老客户端不报错
- 新字段可被请求并返回
```

### 模板 D：离线修复脚本（安全优先）

```text
请编写/修改一个离线修复脚本 [SCRIPT_NAME]，修复 [ISSUE_DESC]。

要求：
1) 脚本默认只打印将要改动的记录（dry-run）；确认参数后再写入。
2) 输出影响行数、影响玩家数。
3) 不能修改在线 API 行为。
4) 在文档中写清“执行前备份 DB”的命令。

输出：
- 执行命令
- 风险点
- 回滚建议
```

### 模板 E：文档同步（中英一致）

```text
请同步更新中文与英文文档，主题是 [TOPIC]。

要求：
1) README.zh-CN.md 与 README.en.md 都要有入口。
2) docs/DEVELOPMENT_GUIDE.zh-CN.md 与 docs/DEVELOPMENT_GUIDE.en.md 内容结构对应。
3) 对用户可见术语保持术语对照一致。

输出：
- 修改清单
- 关键术语对照表（zh/en）
```

## 6.2 可执行任务单模板（验收 + 回滚）

下面是可直接贴进 Issue/PR 的任务单格式。建议每次只选一种任务类型执行。

### 任务单 1：新增成就

- 目标：新增 [ACH_KEY]，奖励 [SCORE_BONUS]。
- 修改范围：`data/achievement_catalog.json`，必要时 `scripts/achievements.py`。
- 验收清单：
- `GET /api/achievement_query` 可看到新成就。
- 至少一次结算后可在玩家成就中看到记录。
- 中英字段齐全（`name/description/en_name/en_description`）。
- 回滚清单：
- 删除该成就配置并恢复相关逻辑。
- 如已写入历史奖励，执行离线修复脚本回滚奖励分。

### 任务单 2：匹配参数调优

- 目标：优化 [GOAL]（如减少重复对手、提升分差公平性）。
- 修改范围：`.ENV`，`scripts/runtime.py`，`scripts/matchmaking.py`。
- 验收清单：
- 参数存在默认值，未配置时可正常运行。
- `BOT-SHADOW` 补位逻辑未损坏。
- 至少观察 3 个轮次，指标有明确变化（重复率/分差分布）。
- 回滚清单：
- 恢复旧参数值。
- 回退 penalty 公式到修改前版本。

### 任务单 3：API 字段扩展

- 目标：为 [ENDPOINT] 增加 [NEW_FIELD]。
- 修改范围：`scripts/models.py`，`server.py`，必要文档。
- 验收清单：
- 老字段保留，新字段以追加方式返回。
- 老客户端请求不报错。
- 中英文文档均更新。
- 回滚清单：
- 保留内部实现，先在响应中下线新字段。
- 如需彻底回滚，再回退模型和路由改动。

### 任务单 4：离线数据修复

- 目标：修复 [ISSUE_DESC]。
- 修改范围：`scripts/` 下离线脚本，不改在线 API 主流程。
- 验收清单：
- 默认 dry-run 输出影响范围。
- 实际执行后输出影响行数与玩家数。
- 修复后关键查询结果正确。
- 回滚清单：
- 使用执行前数据库备份恢复。
- 对脚本改动做版本回退，防止重复误执行。

### 任务单 5：双语文档同步

- 目标：同步 [TOPIC] 的中英文文档。
- 修改范围：`README.zh-CN.md`，`README.en.md`，`docs/DEVELOPMENT_GUIDE.*.md`。
- 验收清单：
- 双语目录都能导航到对应内容。
- 关键术语在中英文含义一致。
- 示例命令和接口名一致。
- 回滚清单：
- 先回滚入口导航，再回滚正文。
- 若只回滚单语，需明确标注临时不一致状态。

## 7. 推荐贡献模板（Issue / PR）

- 背景：要解决什么问题
- 改动范围：修改了哪些模块
- 兼容性：是否影响历史数据与 API
- 验证方式：用哪些命令和接口验证
- 回滚方案：如何快速撤回改动

## 8. 常见坑位

- 忘记设置 `OPENCLAW_API_PORT` 会导致启动时报错（`runtime.py` 强制要求）。
- 成就重复触发要看 `trigger.repeatable` 与 `UNIQUE(player_id, achievement_key)` 的关系。
- 指纹与账号强绑定：跨账号复用 `x-openclaw-fingerprint` 会触发 403。
- 测试模式与正式模式的轮次和时间窗不同，排查问题时要先确认模式。

---

如果你是第一次参与，建议先从“新增一个配置驱动成就 + 补一条中英文文档”开始，这条路径最容易成功并快速理解整个系统。

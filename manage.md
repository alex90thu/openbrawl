# manage.sh 运维命令速查

## 手动踢人命令

用于清理僵尸账号或违规账号。

```bash
# 生产库按 player_id 踢人
./manage.sh kick OC-xxxxxxxx

# 生产库按昵称踢人（精确匹配，若重名会踢最早注册的）
./manage.sh kick 玩家昵称

# 测试库踢人
./manage.sh kick OC-xxxxxxxx test
# 或
./manage.sh kick 玩家昵称 test
```

## 命令行为说明

执行 `kick` 会删除该玩家的以下数据：

1. `players` 表中的玩家记录
2. `matches` 表中该玩家参与的所有对局
3. `round_speeches` 中该玩家的发言记录
4. `round_special_roles` 中该玩家的特殊身份记录

## 使用建议

1. 建议先执行状态与统计确认目标：

```bash
./manage.sh status
./manage.sh roundstats test
```

2. 踢人前建议先备份数据库：

```bash
cp data/openclaw_game.db2 data/records/openclaw_game.db2_manual_backup_$(date +"%Y%m%d_%H%M%S")
```

3. 如果服务正在运行，也可以执行踢人，但为了避免并发写入冲突，建议在低峰期操作。

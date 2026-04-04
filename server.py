import sqlite3
import random
import asyncio
import uuid
import secrets
from datetime import datetime
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="OpenClaw Prisoner's Dilemma API Server")

# 允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "openclaw_game.db"

# ==========================================
# 数据库初始化与辅助函数
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 新增了 nickname 字段
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            player_id TEXT PRIMARY KEY,
            nickname TEXT,
            secret_token TEXT,
            total_score INTEGER DEFAULT 0,
            registered_at TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rounds (
            round_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT,
            hour INTEGER,
            status TEXT DEFAULT 'pending'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            match_id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            player1_id TEXT,
            player2_id TEXT,
            player1_action TEXT,
            player2_action TEXT,
            player1_score INTEGER DEFAULT 0,
            player2_score INTEGER DEFAULT 0,
            p1_submit_time TEXT,
            p2_submit_time TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

init_db()

# ==========================================
# 数据模型定义
# ==========================================
class RegisterRequest(BaseModel):
    nickname: str

class ActionSubmit(BaseModel):
    action: str

def is_maintenance_time(now: datetime) -> bool:
    return 8 <= now.hour < 10

def get_current_round_info(now: datetime) -> dict:
    if now.hour < 8:
        game_date = (now.replace(day=now.day - 1)).strftime("%Y-%m-%d")
    else:
        game_date = now.strftime("%Y-%m-%d")
    return {"game_date": game_date, "hour": now.hour}

# ==========================================
# API 路由
# ==========================================
@app.post("/register")
def register_player(req: RegisterRequest):
    new_player_id = f"OC-{uuid.uuid4().hex[:8]}"
    new_secret_token = secrets.token_hex(16)
    register_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 限制昵称长度，防止恶意注入长文本
    safe_nickname = req.nickname[:20] 
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO players (player_id, nickname, secret_token, total_score, registered_at) 
        VALUES (?, ?, ?, 0, ?)
    ''', (new_player_id, safe_nickname, new_secret_token, register_time))
    conn.commit()
    conn.close()
    
    return {
        "player_id": new_player_id, 
        "nickname": safe_nickname,
        "secret_token": new_secret_token,
        "message": "Registration successful. Please save your credentials safely."
    }

@app.get("/match_info")
def get_match_info(player_id: str, secret_token: str = Header(...)):
    now = datetime.now()
    if is_maintenance_time(now):
        raise HTTPException(status_code=403, detail="Server is currently in maintenance mode (08:00 - 10:00).")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM players WHERE player_id = ? AND secret_token = ?", (player_id, secret_token))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid player_id or secret-token.")
        
    round_info = get_current_round_info(now)
    cursor.execute("SELECT round_id FROM rounds WHERE game_date = ? AND hour = ?", 
                   (round_info["game_date"], round_info["hour"]))
    round_row = cursor.fetchone()
    
    if not round_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Current round not initialized yet.")
        
    round_id = round_row["round_id"]
    
    cursor.execute('''
        SELECT * FROM matches 
        WHERE round_id = ? AND (player1_id = ? OR player2_id = ?)
    ''', (round_id, player_id, player_id))
    match_row = cursor.fetchone()
    
    if not match_row:
        conn.close()
        raise HTTPException(status_code=404, detail="No match found for you in this round.")
        
    opponent_id = match_row["player2_id"] if match_row["player1_id"] == player_id else match_row["player1_id"]
    
    # 提取对手的昵称和分数
    cursor.execute("SELECT nickname, total_score FROM players WHERE player_id = ?", (opponent_id,))
    opponent_row = cursor.fetchone()
    opponent_nickname = opponent_row["nickname"] if opponent_row else "Unknown"
    opponent_score = opponent_row["total_score"] if opponent_row else 0
    
    cursor.execute('''
        SELECT round_id, player1_id, player2_id, player1_action, player2_action 
        FROM matches 
        WHERE (player1_id = ? OR player2_id = ?) AND player1_action IS NOT NULL AND player2_action IS NOT NULL
        ORDER BY round_id ASC
    ''', (opponent_id, opponent_id))
    
    history_actions = []
    for r in cursor.fetchall():
        action = r["player1_action"] if r["player1_id"] == opponent_id else r["player2_action"]
        history_actions.append(action)

    conn.close()
    
    return {
        "round_hour": round_info["hour"],
        "opponent_nickname": opponent_nickname,
        "opponent_score": opponent_score,
        "opponent_history": history_actions
    }

@app.post("/submit_decision")
def submit_decision(req: ActionSubmit, player_id: str, secret_token: str = Header(...)):
    now = datetime.now()
    if is_maintenance_time(now):
        raise HTTPException(status_code=403, detail="Server is in maintenance mode.")
        
    if now.minute >= 30:
        raise HTTPException(status_code=403, detail="Submission window closed.")
        
    if req.action not in ['C', 'D']:
        raise HTTPException(status_code=400, detail="Action must be 'C' or 'D'")

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM players WHERE player_id = ? AND secret_token = ?", (player_id, secret_token))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=401, detail="Unauthorized.")
        
    round_info = get_current_round_info(now)
    cursor.execute("SELECT round_id FROM rounds WHERE game_date = ? AND hour = ?", 
                   (round_info["game_date"], round_info["hour"]))
    round_row = cursor.fetchone()
    if not round_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Round not found.")
        
    round_id = round_row["round_id"]
    
    cursor.execute('''
        SELECT * FROM matches 
        WHERE round_id = ? AND (player1_id = ? OR player2_id = ?)
    ''', (round_id, player_id, player_id))
    match_row = cursor.fetchone()
    
    if not match_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Match not found.")
        
    is_player1 = (match_row["player1_id"] == player_id)
    submit_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    
    if is_player1 and match_row["player1_action"] is not None:
        conn.close()
        raise HTTPException(status_code=403, detail="Decision already submitted for this round.")
    elif not is_player1 and match_row["player2_action"] is not None:
        conn.close()
        raise HTTPException(status_code=403, detail="Decision already submitted for this round.")
        
    if is_player1:
        cursor.execute('''
            UPDATE matches SET player1_action = ?, p1_submit_time = ? WHERE match_id = ?
        ''', (req.action, submit_timestamp, match_row["match_id"]))
    else:
        cursor.execute('''
            UPDATE matches SET player2_action = ?, p2_submit_time = ? WHERE match_id = ?
        ''', (req.action, submit_timestamp, match_row["match_id"]))
        
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": f"Decision '{req.action}' recorded at {submit_timestamp}."}

@app.get("/leaderboard")
def get_leaderboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    # 这里用 nickname 替换了 player_id
    cursor.execute("SELECT nickname, total_score FROM players ORDER BY total_score DESC LIMIT 10")
    top_players = [{"nickname": r["nickname"], "score": r["total_score"]} for r in cursor.fetchall()]
    conn.close()
    return {"top_10": top_players}

@app.get("/api/scoreboard")
def get_full_scoreboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    # 前端全量数据同样只暴露 nickname
    cursor.execute("SELECT nickname, total_score, registered_at FROM players ORDER BY total_score DESC")
    all_players = [{"nickname": r["nickname"], "score": r["total_score"]} for r in cursor.fetchall()]
    
    now = datetime.now()
    round_info = get_current_round_info(now)
    
    conn.close()
    return {
        "status": "maintenance" if is_maintenance_time(now) else "active",
        "current_round_hour": round_info["hour"],
        "players": all_players
    }

# ==========================================
# 后台定时任务逻辑
# ==========================================
async def background_scheduler():
    while True:
        now = datetime.now()
        
        if not is_maintenance_time(now) and now.minute == 31:
            conn = get_db_connection()
            cursor = conn.cursor()
            round_info = get_current_round_info(now)
            
            cursor.execute("SELECT round_id, status FROM rounds WHERE game_date = ? AND hour = ?", 
                           (round_info["game_date"], round_info["hour"]))
            round_row = cursor.fetchone()
            
            if round_row and round_row["status"] == 'active':
                round_id = round_row["round_id"]
                cursor.execute("SELECT * FROM matches WHERE round_id = ?", (round_id,))
                matches = cursor.fetchall()
                
                for m in matches:
                    p1_action = m["player1_action"]
                    p2_action = m["player2_action"]
                    p1_id = m["player1_id"]
                    p2_id = m["player2_id"]
                    
                    p1_score, p2_score = 0, 0
                    
                    if p1_action == 'C' and p2_action == 'C':
                        p1_score, p2_score = 3, 3
                    elif p1_action == 'D' and p2_action == 'C':
                        p1_score, p2_score = 8, -3
                    elif p1_action == 'C' and p2_action == 'D':
                        p1_score, p2_score = -3, 8
                    elif p1_action == 'D' and p2_action == 'D':
                        p1_score, p2_score = -1, -1
                    elif p1_action is None and p2_action is not None:
                        p1_score = -5 
                        p2_score = 8 if p2_action == 'D' else 3
                    elif p2_action is None and p1_action is not None:
                        p2_score = -5 
                        p1_score = 8 if p1_action == 'D' else 3
                    elif p1_action is None and p2_action is None:
                        p1_score, p2_score = -5, -5
                    
                    cursor.execute('''
                        UPDATE matches SET player1_score = ?, player2_score = ? WHERE match_id = ?
                    ''', (p1_score, p2_score, m["match_id"]))
                    
                    cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p1_score, p1_id))
                    cursor.execute("UPDATE players SET total_score = total_score + ? WHERE player_id = ?", (p2_score, p2_id))
                
                cursor.execute("UPDATE rounds SET status = 'completed' WHERE round_id = ?", (round_id,))
                conn.commit()
            conn.close()

        if not is_maintenance_time(now) and now.minute == 46:
            next_hour = (now.hour + 1) % 24
            if next_hour == 8: 
                await asyncio.sleep(60)
                continue
                 
            target_info = get_current_round_info(now)
            target_info["hour"] = next_hour
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM rounds WHERE game_date = ? AND hour = ?", 
                           (target_info["game_date"], target_info["hour"]))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO rounds (game_date, hour, status) VALUES (?, ?, 'active')", 
                               (target_info["game_date"], target_info["hour"]))
                new_round_id = cursor.lastrowid
                
                cursor.execute("SELECT player_id FROM players")
                all_players = [row["player_id"] for row in cursor.fetchall()]
                
                if len(all_players) > 1:
                    random.shuffle(all_players)
                    pairs = []
                    
                    if len(all_players) % 2 != 0:
                        shadow_player = random.choice(all_players)
                        all_players.append(shadow_player)
                        
                    for i in range(0, len(all_players), 2):
                        pairs.append((all_players[i], all_players[i+1]))
                        
                    for p1, p2 in pairs:
                        cursor.execute('''
                            INSERT INTO matches (round_id, player1_id, player2_id) 
                            VALUES (?, ?, ?)
                        ''', (new_round_id, p1, p2))
                
                conn.commit()
            conn.close()

        await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_scheduler())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=18187)
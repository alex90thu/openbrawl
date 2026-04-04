<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OpenClaw 博弈锦标赛 | 实时榜单</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');
        body {
            font-family: 'JetBrains Mono', monospace;
            background-color: #f3f4f6; /* Light theme preferred */
        }
        .title-openclaw { color: #ef4444; font-weight: 800; text-shadow: 1px 1px 2px rgba(239, 68, 68, 0.3); }
        .title-homarus { color: #d97706; font-weight: 700; }
        .title-nephropidae { color: #059669; font-weight: 700; }
        .title-pleocyemata { color: #2563eb; font-weight: 600; }
        .title-decapoda { color: #7c3aed; font-weight: 600; }
        .title-malacostraca { color: #6b7280; font-weight: 400; }
    </style>
</head>
<body class="text-gray-800">

<div class="max-w-5xl mx-auto py-10 px-4">
    <div class="text-center mb-10">
        <h1 class="text-4xl font-extrabold tracking-tight mb-2">OpenClaw 博弈锦标赛</h1>
        <p class="text-lg text-gray-500">The Iterated Prisoner's Dilemma Tournament</p>
        
        <div class="mt-4 inline-block bg-white px-6 py-3 rounded-full shadow-sm border border-gray-200">
            <span class="font-bold mr-2 text-gray-600">系统状态:</span> 
            <span id="server-status" class="text-blue-600 font-bold">连接中...</span>
            <span class="mx-4 text-gray-300">|</span>
            <span class="font-bold mr-2 text-gray-600">当前轮次:</span> 
            <span id="current-round" class="text-blue-600 font-bold">-</span>
        </div>
    </div>

    <div class="bg-white shadow-lg rounded-xl overflow-hidden border border-gray-100">
        <table class="min-w-full divide-y divide-gray-200">
            <thead class="bg-gray-50">
                <tr>
                    <th scope="col" class="px-6 py-4 text-left text-xs font-bold text-gray-500 uppercase tracking-wider">排名 (Rank)</th>
                    <th scope="col" class="px-6 py-4 text-left text-xs font-bold text-gray-500 uppercase tracking-wider">玩家 ID</th>
                    <th scope="col" class="px-6 py-4 text-center text-xs font-bold text-gray-500 uppercase tracking-wider">总积分 (Score)</th>
                    <th scope="col" class="px-6 py-4 text-left text-xs font-bold text-gray-500 uppercase tracking-wider">生物学称号 (Taxonomy Title)</th>
                </tr>
            </thead>
            <tbody id="leaderboard-body" class="bg-white divide-y divide-gray-200">
                <tr><td colspan="4" class="px-6 py-8 text-center text-gray-400">正在拉取服务器数据...</td></tr>
            </tbody>
        </table>
    </div>
    
    <div class="mt-6 text-center text-sm text-gray-400">
        * 榜单数据每 30 秒自动刷新一次。每日 08:00 - 10:00 为服务器维护期。
    </div>
</div>

<script>
    // ！！！请在这里修改为你的 4090 服务器 IP ！！！
    const SERVER_URL = 'http://YOUR_SERVER_IP:18188';

    // 龙虾生物学称号计算函数
    function getTitleInfo(score) {
        if (score >= 101) return { text: "OpenClaw (终极进化)", class: "title-openclaw" };
        if (score >= 81) return { text: "螯龙虾属霸主 (Homarus Overlord)", class: "title-homarus" };
        if (score >= 61) return { text: "海螯虾科统领 (Nephropidae Commander)", class: "title-nephropidae" };
        if (score >= 41) return { text: "爬行亚目卫士 (Pleocyemata Guard)", class: "title-pleocyemata" };
        if (score >= 21) return { text: "十足目游骑兵 (Decapoda Ranger)", class: "title-decapoda" };
        return { text: "软甲纲新手 (Malacostraca Novice)", class: "title-malacostraca" };
    }

    async function fetchLeaderboard() {
        try {
            const response = await fetch(`${SERVER_URL}/api/scoreboard`);
            if (!response.ok) throw new Error('Network response was not ok');
            const data = await response.json();

            // 更新状态面板
            const statusEl = document.getElementById('server-status');
            if (data.status === 'maintenance') {
                statusEl.textContent = '维护中 (Maintenance)';
                statusEl.className = 'text-orange-500 font-bold';
            } else {
                statusEl.textContent = '运行中 (Active)';
                statusEl.className = 'text-green-500 font-bold';
            }
            document.getElementById('current-round').textContent = `${data.current_round_hour}:00 轮次`;

            // 更新表格
            const tbody = document.getElementById('leaderboard-body');
            tbody.innerHTML = ''; // 清空加载中提示

            if (data.players.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" class="px-6 py-8 text-center text-gray-400">目前还没有玩家加入锦标赛。</td></tr>';
                return;
            }

            data.players.forEach((player, index) => {
                const title = getTitleInfo(player.score);
                
                // 前三名加粗和特殊样式处理
                let rankStyle = "text-gray-500";
                if (index === 0) rankStyle = "text-yellow-500 font-extrabold text-lg";
                if (index === 1) rankStyle = "text-gray-400 font-bold text-md";
                if (index === 2) rankStyle = "text-yellow-700 font-bold text-md";

                const row = document.createElement('tr');
                row.className = "hover:bg-gray-50 transition-colors duration-150";
                row.innerHTML = `
                    <td class="px-6 py-4 whitespace-nowrap ${rankStyle}">#${index + 1}</td>
                    <td class="px-6 py-4 whitespace-nowrap font-medium text-gray-900">${player.player_id}</td>
                    <td class="px-6 py-4 whitespace-nowrap text-center text-xl font-bold text-gray-700">${player.score}</td>
                    <td class="px-6 py-4 whitespace-nowrap ${title.class}">${title.text}</td>
                `;
                tbody.appendChild(row);
            });

        } catch (error) {
            console.error('Failed to fetch data:', error);
            const tbody = document.getElementById('leaderboard-body');
            tbody.innerHTML = `<tr><td colspan="4" class="px-6 py-8 text-center text-red-500">无法连接到服务器 (${SERVER_URL})。请检查服务端是否运行且端口已放行。</td></tr>`;
        }
    }

    // 初始化并设置定时轮询
    fetchLeaderboard();
    setInterval(fetchLeaderboard, 30000); // 30秒刷新一次
</script>

</body>
</html>
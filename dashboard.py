from flask import Flask, render_template_string, jsonify
import json, os
from datetime import datetime

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>SMC Trading Bot Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',sans-serif; }
        .header { background:#161b22; padding:20px 30px; border-bottom:1px solid #30363d; display:flex; justify-content:space-between; align-items:center; }
        .header h1 { color:#58a6ff; font-size:24px; }
        .dot { width:10px; height:10px; border-radius:50%; background:#3fb950; animation:pulse 2s infinite; display:inline-block; margin-right:6px; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        .bots-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; padding:20px 30px; }
        .bot-panel { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; }
        .bot1-panel { border-top:3px solid #58a6ff; }
        .bot2-panel { border-top:3px solid #3fb950; }
        .bot-title { font-size:16px; font-weight:700; margin-bottom:15px; }
        .bot1-title { color:#58a6ff; }
        .bot2-title { color:#3fb950; }
        .stats-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:15px; }
        .stat { background:#0d1117; border-radius:8px; padding:12px; text-align:center; }
        .stat-label { color:#8b949e; font-size:11px; text-transform:uppercase; margin-bottom:4px; }
        .stat-value { font-size:20px; font-weight:700; }
        .charts-section { padding:0 30px 20px; }
        .charts-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
        .chart-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; }
        .chart-title { color:#8b949e; font-size:13px; margin-bottom:15px; font-weight:600; }
        .compare-section { padding:0 30px 20px; }
        .compare-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; }
        .trades-section { padding:0 30px 30px; }
        .trades-grid { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
        .trades-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; }
        .trades-title { font-size:13px; font-weight:600; margin-bottom:15px; }
        table { width:100%; border-collapse:collapse; font-size:12px; }
        th { color:#8b949e; text-align:left; padding:6px 10px; border-bottom:1px solid #30363d; }
        td { padding:8px 10px; border-bottom:1px solid #21262d; }
        tr:hover { background:#1c2128; }
        .badge { padding:2px 7px; border-radius:20px; font-size:10px; font-weight:600; }
        .badge-win { background:#1a3a1e; color:#3fb950; }
        .badge-loss { background:#3a1a1a; color:#f85149; }
        .badge-buy { background:#1a2a3a; color:#58a6ff; }
        .badge-sell { background:#3a2a1a; color:#d29922; }
        .pos-block { background:#0d1117; border-radius:8px; padding:12px; margin-top:10px; font-size:12px; }
        .green { color:#3fb950; }
        .red { color:#f85149; }
        .blue { color:#58a6ff; }
        .yellow { color:#d29922; }
        .winner-badge { background:#1a3a1e; color:#3fb950; padding:4px 12px; border-radius:20px; font-size:12px; font-weight:700; }
        .loser-badge { background:#3a1a1a; color:#f85149; padding:4px 12px; border-radius:20px; font-size:12px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 SMC Trading Bots — Сравнение</h1>
        <div>
            <span class="dot"></span>
            <span style="color:#3fb950">Live</span>
            <span style="color:#8b949e; margin-left:15px" id="lastUpdate"></span>
        </div>
    </div>

    <!-- Панели ботов -->
    <div class="bots-grid">
        <!-- Бот 1 -->
        <div class="bot-panel bot1-panel">
            <div class="bot-title bot1-title">🔵 Бот 1 — BOS + OTE + SFP</div>
            <div class="stats-grid">
                <div class="stat">
                    <div class="stat-label">Баланс</div>
                    <div class="stat-value blue" id="b1-balance">1000</div>
                </div>
                <div class="stat">
                    <div class="stat-label">WinRate</div>
                    <div class="stat-value" id="b1-wr">0%</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Прибыль</div>
                    <div class="stat-value" id="b1-profit">0</div>
                </div>
            </div>
            <div class="stats-grid">
                <div class="stat">
                    <div class="stat-label">Сделок</div>
                    <div class="stat-value" id="b1-total">0</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Wins</div>
                    <div class="stat-value green" id="b1-wins">0</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Losses</div>
                    <div class="stat-value red" id="b1-losses">0</div>
                </div>
            </div>
            <div class="pos-block" id="b1-pos">📊 Позиция: Нет</div>
        </div>

        <!-- Бот 2 -->
        <div class="bot-panel bot2-panel">
            <div class="bot-title bot2-title">🟢 Бот 2 — Аккумуляция→Манипуляция→FVG</div>
            <div class="stats-grid">
                <div class="stat">
                    <div class="stat-label">Баланс</div>
                    <div class="stat-value green" id="b2-balance">1000</div>
                </div>
                <div class="stat">
                    <div class="stat-label">WinRate</div>
                    <div class="stat-value" id="b2-wr">0%</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Прибыль</div>
                    <div class="stat-value" id="b2-profit">0</div>
                </div>
            </div>
            <div class="stats-grid">
                <div class="stat">
                    <div class="stat-label">Сделок</div>
                    <div class="stat-value" id="b2-total">0</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Wins</div>
                    <div class="stat-value green" id="b2-wins">0</div>
                </div>
                <div class="stat">
                    <div class="stat-label">Losses</div>
                    <div class="stat-value red" id="b2-losses">0</div>
                </div>
            </div>
            <div class="pos-block" id="b2-pos">📊 Позиция: Нет</div>
        </div>
    </div>

    <!-- Сравнение победителя -->
    <div class="compare-section">
        <div class="compare-card">
            <div style="display:flex; justify-content:space-between; align-items:center">
                <div class="chart-title">🏆 Текущий победитель</div>
                <div id="winner-badge"></div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:15px; margin-top:15px">
                <div style="text-align:center">
                    <div style="color:#8b949e; font-size:12px">Разница в балансе</div>
                    <div style="font-size:22px; font-weight:700; margin-top:5px" id="bal-diff">0 USDT</div>
                </div>
                <div style="text-align:center">
                    <div style="color:#8b949e; font-size:12px">Разница WinRate</div>
                    <div style="font-size:22px; font-weight:700; margin-top:5px" id="wr-diff">0%</div>
                </div>
                <div style="text-align:center">
                    <div style="color:#8b949e; font-size:12px">Лучший сигнал</div>
                    <div style="font-size:16px; font-weight:700; margin-top:5px" id="best-signal">-</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Графики -->
    <div class="charts-section">
        <div class="charts-grid">
            <div class="chart-card">
                <div class="chart-title">📈 Equity Curve — оба бота</div>
                <canvas id="equityChart" height="120"></canvas>
            </div>
            <div class="chart-card">
                <div class="chart-title">🥧 Win/Loss сравнение</div>
                <canvas id="barChart" height="120"></canvas>
            </div>
        </div>
    </div>

    <!-- Журналы сделок -->
    <div class="trades-section">
        <div class="trades-grid">
            <div class="trades-card">
                <div class="trades-title bot1-title">📋 Бот 1 — Журнал сделок</div>
                <table>
                    <thead>
                        <tr><th>Время</th><th>Пара</th><th>Сторона</th><th>PnL</th><th>Итог</th></tr>
                    </thead>
                    <tbody id="b1-trades"></tbody>
                </table>
                <div id="b1-no-trades" style="text-align:center;color:#8b949e;padding:20px">Сделок пока нет...</div>
            </div>
            <div class="trades-card">
                <div class="trades-title bot2-title">📋 Бот 2 — Журнал сделок</div>
                <table>
                    <thead>
                        <tr><th>Время</th><th>Пара</th><th>Сторона</th><th>PnL</th><th>Итог</th></tr>
                    </thead>
                    <tbody id="b2-trades"></tbody>
                </table>
                <div id="b2-no-trades" style="text-align:center;color:#8b949e;padding:20px">Сделок пока нет...</div>
            </div>
        </div>
    </div>

    <script>
        const START = 1000;
        let equityChart, barChart;

        function initCharts() {
            const ctx1 = document.getElementById('equityChart').getContext('2d');
            equityChart = new Chart(ctx1, {
                type: 'line',
                data: {
                    labels: ['Старт'],
                    datasets: [
                        {
                            label: 'Бот 1 (BOS+OTE+SFP)',
                            data: [START],
                            borderColor: '#58a6ff',
                            backgroundColor: 'rgba(88,166,255,0.05)',
                            fill: true, tension: 0.4, pointRadius: 4,
                        },
                        {
                            label: 'Бот 2 (Accum→FVG)',
                            data: [START],
                            borderColor: '#3fb950',
                            backgroundColor: 'rgba(63,185,80,0.05)',
                            fill: true, tension: 0.4, pointRadius: 4,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { labels: { color: '#8b949e' } } },
                    scales: {
                        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e', callback: v => v + '$' } }
                    }
                }
            });

            const ctx2 = document.getElementById('barChart').getContext('2d');
            barChart = new Chart(ctx2, {
                type: 'bar',
                data: {
                    labels: ['Бот 1', 'Бот 2'],
                    datasets: [
                        { label: 'Wins', data: [0, 0], backgroundColor: '#3fb950' },
                        { label: 'Losses', data: [0, 0], backgroundColor: '#f85149' }
                    ]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { labels: { color: '#8b949e' } } },
                    scales: {
                        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }
                    }
                }
            });
        }

        function renderTrades(trades, tbodyId, noTradesId) {
            const tbody = document.getElementById(tbodyId);
            const noTrades = document.getElementById(noTradesId);
            tbody.innerHTML = '';
            if (!trades || trades.length === 0) {
                noTrades.style.display = 'block';
                return;
            }
            noTrades.style.display = 'none';
            [...trades].reverse().forEach(t => {
                const pnl = parseFloat(t.pnl);
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td style="color:#8b949e">${(t.time||'-').slice(11)}</td>
                    <td style="font-size:11px">${(t.symbol||'-').replace('/USDT:USDT','')}</td>
                    <td><span class="badge badge-${t.side}">${(t.side||'').toUpperCase()}</span></td>
                    <td style="color:${pnl>=0?'#3fb950':'#f85149'};font-weight:600">${pnl>=0?'+':''}${pnl.toFixed(2)}</td>
                    <td><span class="badge badge-${t.result}">${t.result==='win'?'✅':'❌'}</span></td>
                `;
                tbody.appendChild(row);
            });
        }

        async function update() {
            try {
                const r = await fetch('/api/compare');
                const d = await r.json();
                const b1 = d.bot1, b2 = d.bot2;

                // Бот 1
                const p1 = b1.balance - START;
                const t1 = b1.wins + b1.losses;
                const wr1 = t1 > 0 ? (b1.wins/t1*100).toFixed(1) : 0;
                document.getElementById('b1-balance').textContent = parseFloat(b1.balance).toFixed(2);
                document.getElementById('b1-wr').textContent = wr1 + '%';
                document.getElementById('b1-wr').style.color = wr1 >= 50 ? '#3fb950' : '#f85149';
                document.getElementById('b1-profit').textContent = (p1>=0?'+':'') + p1.toFixed(2) + ' USDT';
                document.getElementById('b1-profit').style.color = p1>=0?'#3fb950':'#f85149';
                document.getElementById('b1-total').textContent = t1;
                document.getElementById('b1-wins').textContent = b1.wins;
                document.getElementById('b1-losses').textContent = b1.losses;

                if (b1.position) {
                    document.getElementById('b1-pos').innerHTML =
                        `📊 <b style="color:${b1.position.side==='buy'?'#58a6ff':'#d29922'}">${b1.position.side.toUpperCase()}</b>
                         ${b1.position.symbol} @ ${parseFloat(b1.position.entry).toFixed(4)}
                         | SL: <span style="color:#f85149">${parseFloat(b1.position.sl).toFixed(4)}</span>
                         | TP2: <span style="color:#3fb950">${parseFloat(b1.position.tp2||0).toFixed(4)}</span>`;
                } else {
                    document.getElementById('b1-pos').textContent = '📊 Позиция: Нет';
                }

                // Бот 2
                const p2 = b2.balance - START;
                const t2 = b2.wins + b2.losses;
                const wr2 = t2 > 0 ? (b2.wins/t2*100).toFixed(1) : 0;
                document.getElementById('b2-balance').textContent = parseFloat(b2.balance).toFixed(2);
                document.getElementById('b2-wr').textContent = wr2 + '%';
                document.getElementById('b2-wr').style.color = wr2 >= 50 ? '#3fb950' : '#f85149';
                document.getElementById('b2-profit').textContent = (p2>=0?'+':'') + p2.toFixed(2) + ' USDT';
                document.getElementById('b2-profit').style.color = p2>=0?'#3fb950':'#f85149';
                document.getElementById('b2-total').textContent = t2;
                document.getElementById('b2-wins').textContent = b2.wins;
                document.getElementById('b2-losses').textContent = b2.losses;

                if (b2.position) {
                    document.getElementById('b2-pos').innerHTML =
                        `📊 <b style="color:${b2.position.side==='buy'?'#58a6ff':'#d29922'}">${b2.position.side.toUpperCase()}</b>
                         ${b2.position.symbol} @ ${parseFloat(b2.position.entry).toFixed(4)}
                         | SL: <span style="color:#f85149">${parseFloat(b2.position.sl).toFixed(4)}</span>
                         | TP2: <span style="color:#3fb950">${parseFloat(b2.position.tp2||0).toFixed(4)}</span>`;
                } else {
                    document.getElementById('b2-pos').textContent = '📊 Позиция: Нет';
                }

                // Победитель
                const balDiff = Math.abs(b1.balance - b2.balance).toFixed(2);
                const wrDiff = Math.abs(wr1 - wr2).toFixed(1);
                const winner = b1.balance >= b2.balance ? 'Бот 1 🔵' : 'Бот 2 🟢';
                const winnerColor = b1.balance >= b2.balance ? '#58a6ff' : '#3fb950';
                document.getElementById('winner-badge').innerHTML =
                    `<span style="background:${winnerColor}22; color:${winnerColor}; padding:6px 16px; border-radius:20px; font-weight:700">
                    🏆 ${winner} лидирует</span>`;
                document.getElementById('bal-diff').textContent = balDiff + ' USDT';
                document.getElementById('bal-diff').style.color = winnerColor;
                document.getElementById('wr-diff').textContent = wrDiff + '%';
                document.getElementById('best-signal').textContent = b1.balance >= b2.balance ? 'BOS+OTE+SFP' : 'Accum→FVG';

                // Equity Chart
                const labels1 = ['Старт']; const vals1 = [START];
                let bal1 = START;
                (b1.trades||[]).forEach((t,i) => { bal1+=t.pnl; labels1.push('#'+(i+1)); vals1.push(parseFloat(bal1.toFixed(2))); });

                const labels2 = ['Старт']; const vals2 = [START];
                let bal2 = START;
                (b2.trades||[]).forEach((t,i) => { bal2+=t.pnl; labels2.push('#'+(i+1)); vals2.push(parseFloat(bal2.toFixed(2))); });

                const maxLen = Math.max(labels1.length, labels2.length);
                const finalLabels = Array.from({length: maxLen}, (_,i) => i===0?'Старт':'#'+i);

                equityChart.data.labels = finalLabels;
                equityChart.data.datasets[0].data = vals1;
                equityChart.data.datasets[1].data = vals2;
                equityChart.update();

                // Bar Chart
                barChart.data.datasets[0].data = [b1.wins, b2.wins];
                barChart.data.datasets[1].data = [b1.losses, b2.losses];
                barChart.update();

                // Таблицы
                renderTrades(b1.trades, 'b1-trades', 'b1-no-trades');
                renderTrades(b2.trades, 'b2-trades', 'b2-no-trades');

                document.getElementById('lastUpdate').textContent =
                    'Обновлено: ' + new Date().toLocaleTimeString();

            } catch(e) {
                console.error(e);
            }
        }

        initCharts();
        update();
        setInterval(update, 10000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/compare')
def api_compare():
    def load(filename):
        default = {"balance":1000,"wins":0,"losses":0,"trades":[],"daily_trades":0,"daily_loss":0,"position":None}
        try:
            if os.path.exists(filename):
                with open(filename) as f:
                    return json.load(f)
        except: pass
        return default

    bot1 = load("paper_trades.json")
    bot2 = load("paper_trades_bot2.json")
    bot1.setdefault("position", None)
    bot2.setdefault("position", None)
    bot1.setdefault("trades", [])
    bot2.setdefault("trades", [])

    return jsonify({"bot1": bot1, "bot2": bot2})

@app.route('/api/data')
def api_data():
    def load(filename):
        default = {"balance":1000,"wins":0,"losses":0,"trades":[],"daily_trades":0,"daily_loss":0,"position":None,"max_daily":3}
        try:
            if os.path.exists(filename):
                with open(filename) as f:
                    d = json.load(f)
                    d["max_daily"] = 3
                    return d
        except: pass
        return default
    return jsonify(load("paper_trades.json"))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

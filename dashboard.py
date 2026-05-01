from flask import Flask, render_template_string, jsonify
import json, os, threading, time
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
        .status { display:flex; align-items:center; gap:8px; }
        .dot { width:10px; height:10px; border-radius:50%; background:#3fb950; animation:pulse 2s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:15px; padding:20px 30px; }
        .card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; }
        .card-title { color:#8b949e; font-size:12px; text-transform:uppercase; letter-spacing:1px; margin-bottom:8px; }
        .card-value { font-size:28px; font-weight:700; }
        .green { color:#3fb950; }
        .red { color:#f85149; }
        .yellow { color:#d29922; }
        .blue { color:#58a6ff; }
        .charts { display:grid; grid-template-columns:2fr 1fr; gap:15px; padding:0 30px 20px; }
        .chart-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; }
        .chart-card h3 { color:#8b949e; font-size:14px; margin-bottom:15px; }
        .trades-section { padding:0 30px 30px; }
        .trades-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; }
        .trades-card h3 { color:#8b949e; font-size:14px; margin-bottom:15px; }
        table { width:100%; border-collapse:collapse; }
        th { color:#8b949e; font-size:12px; text-align:left; padding:8px 12px; border-bottom:1px solid #30363d; }
        td { padding:10px 12px; border-bottom:1px solid #21262d; font-size:14px; }
        tr:hover { background:#1c2128; }
        .badge { padding:3px 8px; border-radius:20px; font-size:11px; font-weight:600; }
        .badge-win { background:#1a3a1e; color:#3fb950; }
        .badge-loss { background:#3a1a1a; color:#f85149; }
        .badge-buy { background:#1a2a3a; color:#58a6ff; }
        .badge-sell { background:#3a2a1a; color:#d29922; }
        .position-card { background:#1c2128; border:1px solid #58a6ff33; border-radius:8px; padding:15px; margin-top:10px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 SMC Trading Bot</h1>
        <div class="status">
            <div class="dot"></div>
            <span style="color:#3fb950">Live</span>
            <span style="color:#8b949e; margin-left:10px" id="lastUpdate"></span>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <div class="card-title">💰 Баланс</div>
            <div class="card-value blue" id="balance">1000.00</div>
            <div style="color:#8b949e; font-size:12px; margin-top:5px" id="profit"></div>
        </div>
        <div class="card">
            <div class="card-title">🏆 WinRate</div>
            <div class="card-value green" id="winrate">0%</div>
            <div style="color:#8b949e; font-size:12px; margin-top:5px" id="wl"></div>
        </div>
        <div class="card">
            <div class="card-title">📈 Всего сделок</div>
            <div class="card-value yellow" id="total">0</div>
            <div style="color:#8b949e; font-size:12px; margin-top:5px" id="daily"></div>
        </div>
        <div class="card">
            <div class="card-title">📊 Открытая позиция</div>
            <div class="card-value" id="position">Нет</div>
            <div style="color:#8b949e; font-size:12px; margin-top:5px" id="posDetails"></div>
        </div>
    </div>

    <div class="charts">
        <div class="chart-card">
            <h3>📈 Equity Curve (рост баланса)</h3>
            <canvas id="equityChart" height="120"></canvas>
        </div>
        <div class="chart-card">
            <h3>🥧 Win / Loss</h3>
            <canvas id="pieChart" height="120"></canvas>
        </div>
    </div>

    <div class="trades-section">
        <div class="trades-card">
            <h3>📋 Журнал сделок</h3>
            <table>
                <thead>
                    <tr>
                        <th>Время</th>
                        <th>Пара</th>
                        <th>Сторона</th>
                        <th>Вход</th>
                        <th>Выход</th>
                        <th>PnL</th>
                        <th>Результат</th>
                    </tr>
                </thead>
                <tbody id="tradesTable"></tbody>
            </table>
        </div>
    </div>

    <script>
        const START_BALANCE = 1000;
        let equityChart, pieChart;

        function initCharts() {
            const ctx1 = document.getElementById('equityChart').getContext('2d');
            equityChart = new Chart(ctx1, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Баланс USDT',
                        data: [],
                        borderColor: '#58a6ff',
                        backgroundColor: 'rgba(88,166,255,0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 4,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } }
                    }
                }
            });

            const ctx2 = document.getElementById('pieChart').getContext('2d');
            pieChart = new Chart(ctx2, {
                type: 'doughnut',
                data: {
                    labels: ['Wins', 'Losses'],
                    datasets: [{
                        data: [0, 0],
                        backgroundColor: ['#3fb950', '#f85149'],
                        borderWidth: 0,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { labels: { color: '#8b949e' } }
                    }
                }
            });
        }

        async function update() {
            try {
                const r = await fetch('/api/data');
                const d = await r.json();

                // Баланс
                const profit = d.balance - START_BALANCE;
                document.getElementById('balance').textContent = d.balance.toFixed(2) + ' USDT';
                document.getElementById('profit').textContent = (profit >= 0 ? '+' : '') + profit.toFixed(2) + ' USDT';
                document.getElementById('profit').style.color = profit >= 0 ? '#3fb950' : '#f85149';

                // WinRate
                const total = d.wins + d.losses;
                const wr = total > 0 ? (d.wins / total * 100).toFixed(1) : 0;
                document.getElementById('winrate').textContent = wr + '%';
                document.getElementById('winrate').style.color = wr >= 50 ? '#3fb950' : '#f85149';
                document.getElementById('wl').textContent = d.wins + 'W / ' + d.losses + 'L';

                // Сделки
                document.getElementById('total').textContent = total;
                document.getElementById('daily').textContent = 'Сегодня: ' + d.daily_trades + '/' + d.max_daily;

                // Позиция
                if (d.position) {
                    document.getElementById('position').textContent = d.position.side.toUpperCase();
                    document.getElementById('position').style.color = d.position.side === 'buy' ? '#58a6ff' : '#d29922';
                    document.getElementById('posDetails').textContent = d.position.symbol + ' @ ' + d.position.entry.toFixed(4);
                } else {
                    document.getElementById('position').textContent = 'Нет';
                    document.getElementById('position').style.color = '#8b949e';
                    document.getElementById('posDetails').textContent = '';
                }

                // Equity chart
                if (d.trades.length > 0) {
                    let bal = START_BALANCE;
                    const labels = ['Старт'];
                    const values = [bal];
                    d.trades.forEach((t, i) => {
                        bal += t.pnl;
                        labels.push('#' + (i+1));
                        values.push(parseFloat(bal.toFixed(2)));
                    });
                    equityChart.data.labels = labels;
                    equityChart.data.datasets[0].data = values;
                    equityChart.update();
                }

                // Pie chart
                pieChart.data.datasets[0].data = [d.wins, d.losses];
                pieChart.update();

                // Таблица сделок
                const tbody = document.getElementById('tradesTable');
                tbody.innerHTML = '';
                const trades = [...d.trades].reverse();
                trades.forEach(t => {
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td style="color:#8b949e">${t.time || '-'}</td>
                        <td>${t.symbol || 'BTC/USDT'}</td>
                        <td><span class="badge badge-${t.side}">${t.side.toUpperCase()}</span></td>
                        <td>${parseFloat(t.entry).toFixed(4)}</td>
                        <td>${parseFloat(t.exit).toFixed(4)}</td>
                        <td style="color:${t.pnl >= 0 ? '#3fb950' : '#f85149'}">${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}</td>
                        <td><span class="badge badge-${t.result}">${t.result === 'win' ? '✅ Win' : '❌ Loss'}</span></td>
                    `;
                    tbody.appendChild(row);
                });

                document.getElementById('lastUpdate').textContent = 'Обновлено: ' + new Date().toLocaleTimeString();
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

@app.route('/api/data')
def api_data():
    try:
        if os.path.exists('paper_trades.json'):
            with open('paper_trades.json') as f:
                data = json.load(f)
        else:
            data = {"balance": 1000, "wins": 0, "losses": 0, "trades": [], "daily_trades": 0}

        data["max_daily"] = 3
        data["daily_trades"] = data.get("daily_trades", 0)
        data["position"] = data.get("position", None)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

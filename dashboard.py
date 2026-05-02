from flask import Flask, render_template_string, jsonify
import json, os, requests
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
        .pos-card { background:#1c2128; border:1px solid #58a6ff44; border-radius:10px; padding:15px; margin-top:10px; }
        .pos-row { display:flex; justify-content:space-between; margin-top:6px; font-size:13px; }
        .pnl-positive { color:#3fb950; font-size:20px; font-weight:700; }
        .pnl-negative { color:#f85149; font-size:20px; font-weight:700; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 SMC Trading Bot</h1>
        <div class="status">
            <div class="dot"></div>
            <span style="color:#3fb950">Live</span>
            <span style="color:#8b949e; margin-left:15px" id="lastUpdate"></span>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <div class="card-title">💰 Баланс</div>
            <div class="card-value blue" id="balance">1000.00 USDT</div>
            <div style="font-size:13px; margin-top:6px" id="profit"></div>
        </div>
        <div class="card">
            <div class="card-title">🏆 WinRate</div>
            <div class="card-value" id="winrate">0%</div>
            <div style="color:#8b949e; font-size:13px; margin-top:6px" id="wl">0W / 0L</div>
        </div>
        <div class="card">
            <div class="card-title">📈 Сделок сегодня</div>
            <div class="card-value yellow" id="daily">0/3</div>
            <div style="color:#8b949e; font-size:13px; margin-top:6px" id="total">Всего: 0</div>
        </div>
        <div class="card">
            <div class="card-title">🛡 Дневные потери</div>
            <div class="card-value red" id="dailyLoss">0.00 USDT</div>
            <div style="color:#8b949e; font-size:13px; margin-top:6px" id="dailyLossLimit"></div>
        </div>
    </div>

    <!-- Открытая позиция -->
    <div style="padding:0 30px 20px" id="positionSection" style="display:none">
        <div class="pos-card">
            <div style="color:#58a6ff; font-size:14px; font-weight:600; margin-bottom:10px">📊 Открытая позиция</div>
            <div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:15px">
                <div>
                    <div style="color:#8b949e; font-size:11px">ПАРА</div>
                    <div style="font-size:16px; font-weight:700; margin-top:4px" id="posSymbol">-</div>
                </div>
                <div>
                    <div style="color:#8b949e; font-size:11px">СТОРОНА</div>
                    <div style="font-size:16px; font-weight:700; margin-top:4px" id="posSide">-</div>
                </div>
                <div>
                    <div style="color:#8b949e; font-size:11px">ВХОД</div>
                    <div style="font-size:16px; font-weight:700; margin-top:4px" id="posEntry">-</div>
                </div>
                <div>
                    <div style="color:#8b949e; font-size:11px">PnL</div>
                    <div style="font-size:16px; font-weight:700; margin-top:4px" id="posPnl">-</div>
                </div>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:15px; margin-top:12px">
                <div>
                    <div style="color:#8b949e; font-size:11px">СТОП ЛОСС</div>
                    <div style="color:#f85149; font-size:14px; font-weight:600; margin-top:4px" id="posSL">-</div>
                </div>
                <div>
                    <div style="color:#8b949e; font-size:11px">TP1 (50%)</div>
                    <div style="color:#d29922; font-size:14px; font-weight:600; margin-top:4px" id="posTP1">-</div>
                </div>
                <div>
                    <div style="color:#8b949e; font-size:11px">TP2 (50%)</div>
                    <div style="color:#3fb950; font-size:14px; font-weight:600; margin-top:4px" id="posTP2">-</div>
                </div>
            </div>
        </div>
    </div>

    <div class="charts">
        <div class="chart-card">
            <h3>📈 Equity Curve</h3>
            <canvas id="equityChart" height="100"></canvas>
        </div>
        <div class="chart-card">
            <h3>🥧 Win / Loss</h3>
            <canvas id="pieChart" height="100"></canvas>
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
            <div id="noTrades" style="text-align:center; color:#8b949e; padding:30px; display:none">
                Сделок пока нет — бот ищет сигналы...
            </div>
        </div>
    </div>

    <script>
        const START_BALANCE = 1000;
        let equityChart, pieChart;
        let lastPrice = {};

        function initCharts() {
            const ctx1 = document.getElementById('equityChart').getContext('2d');
            equityChart = new Chart(ctx1, {
                type: 'line',
                data: {
                    labels: ['Старт'],
                    datasets: [{
                        label: 'Баланс USDT',
                        data: [START_BALANCE],
                        borderColor: '#58a6ff',
                        backgroundColor: 'rgba(88,166,255,0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 5,
                        pointBackgroundColor: '#58a6ff',
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' } },
                        y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e',
                            callback: v => v.toFixed(0) + '$' } }
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
                    cutout: '65%',
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
                document.getElementById('balance').textContent = parseFloat(d.balance).toFixed(2) + ' USDT';
                const profitEl = document.getElementById('profit');
                profitEl.textContent = (profit >= 0 ? '+' : '') + profit.toFixed(2) + ' USDT';
                profitEl.style.color = profit >= 0 ? '#3fb950' : '#f85149';

                // WinRate
                const total = d.wins + d.losses;
                const wr = total > 0 ? (d.wins / total * 100).toFixed(1) : 0;
                const wrEl = document.getElementById('winrate');
                wrEl.textContent = wr + '%';
                wrEl.style.color = wr >= 50 ? '#3fb950' : '#f85149';
                document.getElementById('wl').textContent = d.wins + 'W / ' + d.losses + 'L';

                // Сделки
                document.getElementById('daily').textContent = d.daily_trades + '/' + d.max_daily;
                document.getElementById('total').textContent = 'Всего: ' + total;

                // Дневные потери
                const dailyLoss = parseFloat(d.daily_loss || 0);
                const dailyLimit = parseFloat(d.balance) * 0.03;
                document.getElementById('dailyLoss').textContent = dailyLoss.toFixed(2) + ' USDT';
                document.getElementById('dailyLossLimit').textContent = 'Лимит: ' + dailyLimit.toFixed(2) + ' USDT';
                document.getElementById('dailyLoss').style.color = dailyLoss > dailyLimit * 0.7 ? '#f85149' : '#d29922';

                // Позиция
                const posSection = document.getElementById('positionSection');
                if (d.position) {
                    posSection.style.display = 'block';
                    const pos = d.position;
                    document.getElementById('posSymbol').textContent = pos.symbol || '-';
                    const sideEl = document.getElementById('posSide');
                    sideEl.textContent = pos.side ? pos.side.toUpperCase() : '-';
                    sideEl.style.color = pos.side === 'buy' ? '#58a6ff' : '#d29922';
                    document.getElementById('posEntry').textContent = parseFloat(pos.entry).toFixed(4);
                    document.getElementById('posSL').textContent = parseFloat(pos.sl).toFixed(4);
                    document.getElementById('posTP1').textContent = pos.tp1 ? parseFloat(pos.tp1).toFixed(4) : '-';
                    document.getElementById('posTP2').textContent = pos.tp2 ? parseFloat(pos.tp2).toFixed(4) : '-';

                    // PnL расчёт
                    if (d.current_price && pos.entry && pos.qty) {
                        const cp = parseFloat(d.current_price);
                        const entry = parseFloat(pos.entry);
                        const qty = parseFloat(pos.qty);
                        const pnl = pos.side === 'buy' ? (cp - entry) * qty : (entry - cp) * qty;
                        const pnlEl = document.getElementById('posPnl');
                        pnlEl.textContent = (pnl >= 0 ? '+' : '') + pnl.toFixed(2) + ' USDT';
                        pnlEl.style.color = pnl >= 0 ? '#3fb950' : '#f85149';
                    }
                } else {
                    posSection.style.display = 'none';
                }

                // Equity chart
                if (d.trades && d.trades.length > 0) {
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
                    equityChart.data.datasets[0].borderColor = bal >= START_BALANCE ? '#3fb950' : '#f85149';
                    equityChart.data.datasets[0].backgroundColor = bal >= START_BALANCE ? 'rgba(63,185,80,0.1)' : 'rgba(248,81,73,0.1)';
                    equityChart.update();
                }

                // Pie chart
                pieChart.data.datasets[0].data = [d.wins || 0, d.losses || 0];
                pieChart.update();

                // Таблица
                const tbody = document.getElementById('tradesTable');
                const noTrades = document.getElementById('noTrades');
                tbody.innerHTML = '';
                if (!d.trades || d.trades.length === 0) {
                    noTrades.style.display = 'block';
                } else {
                    noTrades.style.display = 'none';
                    const trades = [...d.trades].reverse();
                    trades.forEach(t => {
                        const pnl = parseFloat(t.pnl);
                        const row = document.createElement('tr');
                        row.innerHTML = `
                            <td style="color:#8b949e">${t.time || '-'}</td>
                            <td style="font-weight:600">${t.symbol || '-'}</td>
                            <td><span class="badge badge-${t.side}">${t.side ? t.side.toUpperCase() : '-'}</span></td>
                            <td>${parseFloat(t.entry).toFixed(4)}</td>
                            <td>${parseFloat(t.exit).toFixed(4)}</td>
                            <td style="color:${pnl >= 0 ? '#3fb950' : '#f85149'}; font-weight:600">
                                ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USDT
                            </td>
                            <td><span class="badge badge-${t.result}">${t.result === 'win' ? '✅ Win' : '❌ Loss'}</span></td>
                        `;
                        tbody.appendChild(row);
                    });
                }

                document.getElementById('lastUpdate').textContent = 'Обновлено: ' + new Date().toLocaleTimeString();
            } catch(e) {
                console.error('Ошибка обновления:', e);
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
            data = {
                "balance": 1000,
                "wins": 0,
                "losses": 0,
                "trades": [],
                "daily_trades": 0,
                "daily_loss": 0,
                "position": None
            }

        data["max_daily"] = 3
        data.setdefault("daily_trades", 0)
        data.setdefault("daily_loss", 0)
        data.setdefault("position", None)
        data.setdefault("trades", [])

        # Текущая цена для PnL
        if data.get("position"):
            try:
                import ccxt
                ex = ccxt.okx({"options": {"defaultType": "swap"}})
                symbol = data["position"].get("symbol", "BTC/USDT:USDT")
                ticker = ex.fetch_ticker(symbol)
                data["current_price"] = ticker["last"]
            except:
                data["current_price"] = None

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "balance": 1000, "wins": 0, "losses": 0,
                        "trades": [], "daily_trades": 0, "daily_loss": 0,
                        "position": None, "max_daily": 3})

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

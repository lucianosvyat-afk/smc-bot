from flask import Flask, render_template_string, jsonify, request
import json, os, ccxt
from datetime import datetime

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>SMC Trading Bots Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',sans-serif; }
        .header { background:#161b22; padding:20px 30px; border-bottom:1px solid #30363d; display:flex; justify-content:space-between; align-items:center; }
        .header h1 { color:#58a6ff; font-size:22px; }
        .dot { width:10px; height:10px; border-radius:50%; background:#3fb950; animation:pulse 2s infinite; display:inline-block; margin-right:6px; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        .bots-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:15px; padding:20px 30px; }
        .bot-panel { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:18px; }
        .bot1-panel { border-top:3px solid #58a6ff; }
        .bot2-panel { border-top:3px solid #3fb950; }
        .bot3-panel { border-top:3px solid #d29922; }
        .bot-title { font-size:14px; font-weight:700; margin-bottom:12px; }
        .bot1-title { color:#58a6ff; }
        .bot2-title { color:#3fb950; }
        .bot3-title { color:#d29922; }
        .stats-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:10px; }
        .stat { background:#0d1117; border-radius:8px; padding:10px; text-align:center; }
        .stat-label { color:#8b949e; font-size:10px; text-transform:uppercase; margin-bottom:3px; }
        .stat-value { font-size:18px; font-weight:700; }
        .pos-block { background:#0d1117; border-radius:8px; padding:10px; margin-top:8px; font-size:11px; color:#8b949e; }
        .charts-section { padding:0 30px 20px; }
        .charts-grid { display:grid; grid-template-columns:2fr 1fr; gap:15px; }
        .chart-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:18px; }
        .chart-title { color:#8b949e; font-size:13px; margin-bottom:12px; font-weight:600; }
        .compare-section { padding:0 30px 20px; }
        .compare-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:15px; }
        .compare-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:18px; text-align:center; }
        .trades-section { padding:0 30px 30px; }
        .trades-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:15px; }
        .trades-card { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:18px; overflow-x:auto; }
        .trades-title { font-size:12px; font-weight:600; margin-bottom:12px; }
        table { width:100%; border-collapse:collapse; font-size:11px; min-width:500px; }
        th { color:#8b949e; text-align:left; padding:6px 8px; border-bottom:1px solid #30363d; white-space:nowrap; }
        td { padding:8px 8px; border-bottom:1px solid #21262d; vertical-align:top; }
        tr:hover { background:#1c2128; }
        .badge { padding:2px 6px; border-radius:20px; font-size:10px; font-weight:600; }
        .badge-win { background:#1a3a1e; color:#3fb950; }
        .badge-loss { background:#3a1a1a; color:#f85149; }
        .badge-buy { background:#1a2a3a; color:#58a6ff; }
        .badge-sell { background:#3a2a1a; color:#d29922; }
        .green { color:#3fb950; }
        .red { color:#f85149; }
        .blue { color:#58a6ff; }
        .yellow { color:#d29922; }
        .session-on { color:#3fb950; font-size:11px; }
        .session-off { color:#f85149; font-size:11px; }
        .price-entry { color:#58a6ff; font-weight:600; }
        .price-exit { font-weight:600; }
        .price-sl { color:#f85149; font-size:10px; }
        .price-tp { color:#3fb950; font-size:10px; }
        .price-tp1 { color:#d29922; font-size:10px; }
        .modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.7); z-index:1000; align-items:center; justify-content:center; }
        .modal-overlay.open { display:flex; }
        .modal-box { background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; width:90%; max-width:900px; }
        .modal-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }
        .modal-title { font-size:15px; font-weight:700; color:#58a6ff; }
        .modal-close { cursor:pointer; color:#8b949e; font-size:20px; background:none; border:none; }
        .modal-close:hover { color:#e6edf3; }
        .modal-legend { display:flex; gap:14px; flex-wrap:wrap; font-size:11px; color:#8b949e; margin-top:10px; }
        .legend-dot { display:inline-block; width:10px; height:2px; margin-right:4px; vertical-align:middle; }
        .modal-status { font-size:12px; color:#8b949e; margin-top:8px; }
        .trade-link { color:inherit; text-decoration:underline dotted; cursor:pointer; background:none; border:none; font:inherit; padding:0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 SMC Trading Bots — 3 стратегии</h1>
        <div>
            <span class="dot"></span>
            <span style="color:#3fb950">Live</span>
            <span style="color:#8b949e; margin-left:15px" id="lastUpdate"></span>
        </div>
    </div>

    <div class="bots-grid">
        <div class="bot-panel bot1-panel">
            <div class="bot-title bot1-title">🔵 Бот 1 — BOS+OTE+SFP</div>
            <div class="stats-grid">
                <div class="stat"><div class="stat-label">Баланс</div><div class="stat-value blue" id="b1-balance">1000</div></div>
                <div class="stat"><div class="stat-label">WinRate</div><div class="stat-value" id="b1-wr">0%</div></div>
                <div class="stat"><div class="stat-label">Прибыль</div><div class="stat-value" id="b1-profit">0</div></div>
            </div>
            <div class="stats-grid">
                <div class="stat"><div class="stat-label">Сделок</div><div class="stat-value" id="b1-total">0</div></div>
                <div class="stat"><div class="stat-label">Wins</div><div class="stat-value green" id="b1-wins">0</div></div>
                <div class="stat"><div class="stat-label">Losses</div><div class="stat-value red" id="b1-losses">0</div></div>
            </div>
            <div class="pos-block" id="b1-pos">📊 Позиция: Нет</div>
        </div>

        <div class="bot-panel bot2-panel">
            <div class="bot-title bot2-title">🟢 Бот 2 — Аккум→Манип→FVG</div>
            <div class="stats-grid">
                <div class="stat"><div class="stat-label">Баланс</div><div class="stat-value green" id="b2-balance">1000</div></div>
                <div class="stat"><div class="stat-label">WinRate</div><div class="stat-value" id="b2-wr">0%</div></div>
                <div class="stat"><div class="stat-label">Прибыль</div><div class="stat-value" id="b2-profit">0</div></div>
            </div>
            <div class="stats-grid">
                <div class="stat"><div class="stat-label">Сделок</div><div class="stat-value" id="b2-total">0</div></div>
                <div class="stat"><div class="stat-label">Wins</div><div class="stat-value green" id="b2-wins">0</div></div>
                <div class="stat"><div class="stat-label">Losses</div><div class="stat-value red" id="b2-losses">0</div></div>
            </div>
            <div class="pos-block" id="b2-pos">📊 Позиция: Нет</div>
        </div>

        <div class="bot-panel bot3-panel">
            <div class="bot-title bot3-title">🟡 Бот 3 — Форекс EUR/GBP/AUD</div>
            <div class="stats-grid">
                <div class="stat"><div class="stat-label">Баланс</div><div class="stat-value yellow" id="b3-balance">1000</div></div>
                <div class="stat"><div class="stat-label">WinRate</div><div class="stat-value" id="b3-wr">0%</div></div>
                <div class="stat"><div class="stat-label">Прибыль</div><div class="stat-value" id="b3-profit">0</div></div>
            </div>
            <div class="stats-grid">
                <div class="stat"><div class="stat-label">Сделок</div><div class="stat-value" id="b3-total">0</div></div>
                <div class="stat"><div class="stat-label">Wins</div><div class="stat-value green" id="b3-wins">0</div></div>
                <div class="stat"><div class="stat-label">Losses</div><div class="stat-value red" id="b3-losses">0</div></div>
            </div>
            <div class="pos-block" id="b3-pos">📊 Позиция: Нет</div>
            <div id="b3-session" class="session-off" style="margin-top:6px;text-align:center">🔴 Сессия закрыта</div>
        </div>
    </div>

    <div class="compare-section">
        <div class="compare-grid">
            <div class="compare-card">
                <div style="color:#8b949e;font-size:12px;margin-bottom:8px">🏆 Лидер по балансу</div>
                <div style="font-size:18px;font-weight:700" id="leader-balance">-</div>
            </div>
            <div class="compare-card">
                <div style="color:#8b949e;font-size:12px;margin-bottom:8px">🎯 Лидер по WinRate</div>
                <div style="font-size:18px;font-weight:700" id="leader-wr">-</div>
            </div>
            <div class="compare-card">
                <div style="color:#8b949e;font-size:12px;margin-bottom:8px">📈 Всего сделок</div>
                <div style="font-size:18px;font-weight:700" id="total-trades">0</div>
            </div>
        </div>
    </div>

    <div class="charts-section">
        <div class="charts-grid">
            <div class="chart-card">
                <div class="chart-title">📈 Equity Curve — все 3 бота</div>
                <canvas id="equityChart" height="100"></canvas>
            </div>
            <div class="chart-card">
                <div class="chart-title">📊 Win/Loss сравнение</div>
                <canvas id="barChart" height="100"></canvas>
            </div>
        </div>
    </div>

    <div class="trades-section">
        <div class="trades-grid">
            <div class="trades-card">
                <div class="trades-title bot1-title">📋 Бот 1 — BOS+OTE+SFP</div>
                <table>
                    <thead><tr><th>Время</th><th>Пара</th><th>Сторона</th><th>Вход</th><th>Выход</th><th>SL / TP1 / TP2</th><th>PnL</th><th>Итог</th></tr></thead>
                    <tbody id="b1-trades"></tbody>
                </table>
                <div id="b1-empty" style="text-align:center;color:#8b949e;padding:15px;font-size:12px">Сделок пока нет...</div>
            </div>
            <div class="trades-card">
                <div class="trades-title bot2-title">📋 Бот 2 — Аккум→FVG</div>
                <table>
                    <thead><tr><th>Время</th><th>Пара</th><th>Сторона</th><th>Вход</th><th>Выход</th><th>SL / TP1 / TP2</th><th>PnL</th><th>Итог</th></tr></thead>
                    <tbody id="b2-trades"></tbody>
                </table>
                <div id="b2-empty" style="text-align:center;color:#8b949e;padding:15px;font-size:12px">Сделок пока нет...</div>
            </div>
            <div class="trades-card">
                <div class="trades-title bot3-title">📋 Бот 3 — Форекс</div>
                <table>
                    <thead><tr><th>Время</th><th>Пара</th><th>Сторона</th><th>Вход</th><th>Выход</th><th>SL / TP1 / TP2</th><th>PnL</th><th>Итог</th></tr></thead>
                    <tbody id="b3-trades"></tbody>
                </table>
                <div id="b3-empty" style="text-align:center;color:#8b949e;padding:15px;font-size:12px">Ждём торговой сессии...</div>
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
                        { label:'Бот 1', data:[START], borderColor:'#58a6ff', backgroundColor:'rgba(88,166,255,0.05)', fill:true, tension:0.4, pointRadius:3 },
                        { label:'Бот 2', data:[START], borderColor:'#3fb950', backgroundColor:'rgba(63,185,80,0.05)', fill:true, tension:0.4, pointRadius:3 },
                        { label:'Бот 3 (Форекс)', data:[START], borderColor:'#d29922', backgroundColor:'rgba(210,153,34,0.05)', fill:true, tension:0.4, pointRadius:3 }
                    ]
                },
                options: {
                    responsive:true,
                    plugins:{ legend:{ labels:{ color:'#8b949e', font:{size:11} } } },
                    scales:{
                        x:{ grid:{color:'#21262d'}, ticks:{color:'#8b949e'} },
                        y:{ grid:{color:'#21262d'}, ticks:{color:'#8b949e', callback: v => v+'$'} }
                    }
                }
            });

            const ctx2 = document.getElementById('barChart').getContext('2d');
            barChart = new Chart(ctx2, {
                type:'bar',
                data:{
                    labels:['Бот 1','Бот 2','Бот 3'],
                    datasets:[
                        { label:'Wins', data:[0,0,0], backgroundColor:'#3fb950' },
                        { label:'Losses', data:[0,0,0], backgroundColor:'#f85149' }
                    ]
                },
                options:{
                    responsive:true,
                    plugins:{ legend:{ labels:{ color:'#8b949e' } } },
                    scales:{
                        x:{ grid:{color:'#21262d'}, ticks:{color:'#8b949e'} },
                        y:{ grid:{color:'#21262d'}, ticks:{color:'#8b949e'} }
                    }
                }
            });
        }

        // Реестр сделок для модалки с графиком (id -> данные сделки)
        const BOT_TF = {b1:'15m', b2:'15m', b3:'5m'};
        const BOT_NAME = {b1:'Бот 1', b2:'Бот 2', b3:'Бот 3'};
        let tradeRegistry = {};
        let tradeRegistryCounter = 0;
        function registerTrade(t, prefix) {
            const id = 't' + (tradeRegistryCounter++);
            tradeRegistry[id] = Object.assign({}, t, {_bot: BOT_NAME[prefix]||prefix, _tf: BOT_TF[prefix]||'15m'});
            return id;
        }

        let chartHandle = null;
        function closeTradeChart() {
            document.getElementById('chart-modal').classList.remove('open');
            const c = document.getElementById('chart-container');
            c.innerHTML = '';
            chartHandle = null;
        }

        async function openTradeChart(id) {
            const t = tradeRegistry[id];
            if (!t) return;
            const modal = document.getElementById('chart-modal');
            const title = document.getElementById('chart-modal-title');
            const status = document.getElementById('chart-modal-status');
            const container = document.getElementById('chart-container');
            container.innerHTML = '';
            title.textContent = `${t._bot} · ${(t.symbol||'').replace('/USDT:USDT','')} · ${t.side==='buy'?'LONG':'SHORT'}`;
            status.textContent = 'Загружаю свечи...';
            modal.classList.add('open');

            const chart = LightweightCharts.createChart(container, {
                width: container.clientWidth, height: 420,
                layout: { background: { color: '#161b22' }, textColor: '#8b949e' },
                grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
                timeScale: { timeVisible: true, secondsVisible: false },
            });
            chartHandle = chart;
            const series = chart.addCandlestickSeries({
                upColor: '#3fb950', downColor: '#f85149',
                borderVisible: false, wickUpColor: '#3fb950', wickDownColor: '#f85149',
            });

            try {
                const resp = await fetch(`/api/candles?symbol=${encodeURIComponent(t.symbol)}&tf=${t._tf}&anchor=${encodeURIComponent(t.time||'')}`);
                const data = await resp.json();
                if (data.error || !data.candles || !data.candles.length) {
                    status.textContent = '⚠️ Не удалось загрузить свечи: ' + (data.error || 'нет данных');
                    return;
                }
                series.setData(data.candles);

                const entry = parseFloat(t.entry||0), sl = parseFloat(t.sl||0);
                const tp1 = parseFloat(t.tp1||0), tp2 = parseFloat(t.tp2||0);
                if (entry) series.createPriceLine({ price: entry, color: '#58a6ff', lineWidth: 1, lineStyle: 2, title: 'Вход' });
                if (sl) series.createPriceLine({ price: sl, color: '#f85149', lineWidth: 1, lineStyle: 2, title: 'SL' });
                if (tp1) series.createPriceLine({ price: tp1, color: '#d29922', lineWidth: 1, lineStyle: 2, title: 'TP1' });
                if (tp2) series.createPriceLine({ price: tp2, color: '#3fb950', lineWidth: 1, lineStyle: 2, title: 'TP2' });

                const markers = [];
                if (t.result && t.result !== 'open' && t.exit) {
                    const exitTs = data.exit_time || (data.candles[data.candles.length-1].time);
                    markers.push({
                        time: exitTs,
                        position: t.side==='buy' ? 'aboveBar' : 'belowBar',
                        color: t.result==='win' ? '#3fb950' : '#f85149',
                        shape: t.result==='win' ? 'arrowUp' : 'arrowDown',
                        text: (t.result==='win'?'✅ ':'❌ ') + parseFloat(t.exit).toFixed(4),
                    });
                }
                series.setMarkers(markers);
                chart.timeScale().fitContent();

                status.textContent = t.result === 'open'
                    ? `⏳ Открыта${t.opened_at ? ' с ' + t.opened_at : ''} | Вход ${entry.toFixed(4)}`
                    : `PnL: ${parseFloat(t.pnl||0)>=0?'+':''}${parseFloat(t.pnl||0).toFixed(2)} USDT | ${t.time||''}`;
            } catch(e) {
                status.textContent = '⚠️ Ошибка загрузки графика: ' + e;
            }
        }

        function updateBot(data, prefix) {
            const total = data.wins + data.losses;
            const wr = total > 0 ? (data.wins/total*100).toFixed(1) : 0;
            const profit = data.balance - START;
            document.getElementById(prefix+'-balance').textContent = parseFloat(data.balance).toFixed(2);
            const wrEl = document.getElementById(prefix+'-wr');
            wrEl.textContent = wr + '%';
            wrEl.style.color = wr >= 50 ? '#3fb950' : '#f85149';
            const profEl = document.getElementById(prefix+'-profit');
            profEl.textContent = (profit>=0?'+':'') + profit.toFixed(2);
            profEl.style.color = profit>=0?'#3fb950':'#f85149';
            document.getElementById(prefix+'-total').textContent = total;
            document.getElementById(prefix+'-wins').textContent = data.wins;
            document.getElementById(prefix+'-losses').textContent = data.losses;
            if (data.position) {
                const pos = data.position;
                const posId = registerTrade(Object.assign({}, pos, {result:'open'}), prefix);
                document.getElementById(prefix+'-pos').innerHTML =
                    `<b style="color:${pos.side==='buy'?'#58a6ff':'#d29922'}">${pos.side.toUpperCase()}</b>
                     <button class="trade-link" onclick="openTradeChart('${posId}')">${(pos.symbol||'').replace('/USDT:USDT','')} 📈</button>
                     @ <span style="color:#58a6ff">${parseFloat(pos.entry||0).toFixed(4)}</span>
                     | SL:<span style="color:#f85149"> ${parseFloat(pos.sl||0).toFixed(4)}</span>
                     | TP1:<span style="color:#d29922"> ${parseFloat(pos.tp1||0).toFixed(4)}</span>
                     | TP2:<span style="color:#3fb950"> ${parseFloat(pos.tp2||0).toFixed(4)}</span>`;
            } else {
                document.getElementById(prefix+'-pos').textContent = '📊 Позиция: Нет';
            }
            return { total, wr: parseFloat(wr), balance: data.balance };
        }

        function renderTrades(trades, tbodyId, emptyId, prefix) {
            const tbody = document.getElementById(tbodyId);
            const empty = document.getElementById(emptyId);
            tbody.innerHTML = '';
            if (!trades || trades.length === 0) { empty.style.display='block'; return; }
            empty.style.display = 'none';
            [...trades].reverse().slice(0,10).forEach(t => {
                const pnl = parseFloat(t.pnl||0);
                const entry = parseFloat(t.entry||0);
                const exit = parseFloat(t.exit||0);
                const sl = parseFloat(t.sl||0);
                const tp1 = parseFloat(t.tp1||0);
                const tp2 = parseFloat(t.tp2||0);
                const movePct = entry > 0 ? ((exit-entry)/entry*100).toFixed(2) : 0;
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td style="color:#8b949e;white-space:nowrap">${(t.time||'-').slice(11)}<br><span style="font-size:10px">${(t.time||'-').slice(0,10)}</span></td>
                    <td style="font-weight:600"><button class="trade-link" onclick="openTradeChart('${registerTrade(t, prefix)}')">${(t.symbol||'-').replace('/USDT:USDT','')} 📈</button></td>
                    <td><span class="badge badge-${t.side}">${(t.side||'').toUpperCase()}</span></td>
                    <td>
                        <div class="price-entry">${entry.toFixed(4)}</div>
                        <div style="color:#8b949e;font-size:10px">Точка входа</div>
                    </td>
                    <td>
                        <div class="price-exit" style="color:${pnl>=0?'#3fb950':'#f85149'}">${exit.toFixed(4)}</div>
                        <div style="color:#8b949e;font-size:10px">Точка выхода</div>
                        <div style="color:#8b949e;font-size:10px">${movePct}% движение</div>
                    </td>
                    <td>
                        <div class="price-sl">🛑 SL: ${sl.toFixed(4)}</div>
                        <div class="price-tp1">⚡ TP1: ${tp1.toFixed(4)}</div>
                        <div class="price-tp">🎯 TP2: ${tp2.toFixed(4)}</div>
                    </td>
                    <td style="color:${pnl>=0?'#3fb950':'#f85149'};font-weight:700">
                        ${pnl>=0?'+':''}${pnl.toFixed(2)} USDT
                    </td>
                    <td><span class="badge badge-${t.result}">${t.result==='win'?'✅ Win':'❌ Loss'}</span></td>
                `;
                tbody.appendChild(row);
            });
        }

        function buildEquity(trades) {
            let bal = START;
            const vals = [bal];
            (trades||[]).forEach(t => { bal += t.pnl; vals.push(parseFloat(bal.toFixed(2))); });
            return vals;
        }

        async function update() {
            try {
                const r = await fetch('/api/all');
                const d = await r.json();
                const s1 = updateBot(d.bot1, 'b1');
                const s2 = updateBot(d.bot2, 'b2');
                const s3 = updateBot(d.bot3, 'b3');

                const hour = new Date().getUTCHours();
                const day = new Date().getUTCDay();
                const sessionOn = day >= 1 && day <= 5 && (hour >= 8 && hour < 22);
                const sesEl = document.getElementById('b3-session');
                sesEl.textContent = sessionOn ? '🟢 Сессия активна' : '🔴 Сессия закрыта';
                sesEl.className = sessionOn ? 'session-on' : 'session-off';

                const bots = [
                    {name:'🔵 Бот 1', bal:s1.balance, wr:s1.wr},
                    {name:'🟢 Бот 2', bal:s2.balance, wr:s2.wr},
                    {name:'🟡 Бот 3', bal:s3.balance, wr:s3.wr}
                ];
                const leaderBal = bots.reduce((a,b) => a.bal>b.bal?a:b);
                const leaderWR = bots.reduce((a,b) => a.wr>b.wr?a:b);
                document.getElementById('leader-balance').textContent = leaderBal.name + ' (' + leaderBal.bal.toFixed(2) + '$)';
                document.getElementById('leader-wr').textContent = leaderWR.name + ' (' + leaderWR.wr + '%)';
                document.getElementById('total-trades').textContent = s1.total + s2.total + s3.total;

                const v1 = buildEquity(d.bot1.trades);
                const v2 = buildEquity(d.bot2.trades);
                const v3 = buildEquity(d.bot3.trades);
                const maxLen = Math.max(v1.length, v2.length, v3.length);
                const labels = Array.from({length:maxLen},(_,i)=>i===0?'Старт':'#'+i);
                equityChart.data.labels = labels;
                equityChart.data.datasets[0].data = v1;
                equityChart.data.datasets[1].data = v2;
                equityChart.data.datasets[2].data = v3;
                equityChart.update();

                barChart.data.datasets[0].data = [d.bot1.wins, d.bot2.wins, d.bot3.wins];
                barChart.data.datasets[1].data = [d.bot1.losses, d.bot2.losses, d.bot3.losses];
                barChart.update();

                renderTrades(d.bot1.trades, 'b1-trades', 'b1-empty', 'b1');
                renderTrades(d.bot2.trades, 'b2-trades', 'b2-empty', 'b2');
                renderTrades(d.bot3.trades, 'b3-trades', 'b3-empty', 'b3');

                document.getElementById('lastUpdate').textContent = 'Обновлено: ' + new Date().toLocaleTimeString();
            } catch(e) { console.error(e); }
        }

        initCharts();
        update();
        setInterval(update, 10000);
    </script>
<div class="modal-overlay" id="chart-modal">
    <div class="modal-box">
        <div class="modal-header">
            <div class="modal-title" id="chart-modal-title">Сделка</div>
            <button class="modal-close" onclick="closeTradeChart()">✕</button>
        </div>
        <div id="chart-container" style="height:420px"></div>
        <div class="modal-legend">
            <span><span class="legend-dot" style="background:#58a6ff"></span>Вход</span>
            <span><span class="legend-dot" style="background:#f85149"></span>SL</span>
            <span><span class="legend-dot" style="background:#d29922"></span>TP1</span>
            <span><span class="legend-dot" style="background:#3fb950"></span>TP2</span>
        </div>
        <div class="modal-status" id="chart-modal-status"></div>
    </div>
</div>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/all')
def api_all():
    try:
        from database import get_all_states
        return jsonify(get_all_states())
    except Exception as e:
        default = {"balance":1000,"wins":0,"losses":0,"trades":[],"daily_trades":0,"daily_loss":0,"position":None}
        return jsonify({"bot1":default.copy(),"bot2":default.copy(),"bot3":default.copy()})
        try:
            if os.path.exists(filename):
                with open(filename) as f:
                    d = json.load(f)
                    d.setdefault("position", None)
                    d.setdefault("trades", [])
                    return d
        except: pass
        return default
    return jsonify({
        "bot1": load("paper_trades.json"),
        "bot2": load("paper_trades_bot2.json"),
        "bot3": load("paper_trades_bot3.json")
    })

@app.route('/api/compare')
def api_compare():
    def load(filename):
        default = {"balance":1000,"wins":0,"losses":0,"trades":[],"position":None}
        try:
            if os.path.exists(filename):
                with open(filename) as f:
                    d = json.load(f)
                    d.setdefault("position", None)
                    d.setdefault("trades", [])
                    return d
        except: pass
        return default
    return jsonify({
        "bot1": load("paper_trades.json"),
        "bot2": load("paper_trades_bot2.json")
    })

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

_ex_cache = {}
def get_exchange(name):
    """Ленивое кэшированное подключение к бирже только для чтения OHLCV
    (без ключей). OKX — крипто-пары Бота1/2 и золото/серебро Бота3.
    BitMEX — реальные форекс-пары Бота 3."""
    if name not in _ex_cache:
        if name == 'okx':
            ex = ccxt.okx({"options": {"defaultType": "swap"}})
        else:
            ex = ccxt.bitmex()
        ex.load_markets()
        _ex_cache[name] = ex
    return _ex_cache[name]

TF_MS = {'1m':60000,'5m':300000,'15m':900000,'30m':1800000,'1h':3600000,'4h':14400000}

@app.route('/api/candles')
def api_candles():
    symbol = request.args.get('symbol', '')
    tf = request.args.get('tf', '15m')
    anchor = request.args.get('anchor', '')
    limit = 200
    if not symbol:
        return jsonify({"error": "no symbol"}), 400
    try:
        ex_name = 'okx' if '/USDT:USDT' in symbol else 'bitmex'
        ex = get_exchange(ex_name)

        since = None
        exit_time = None
        if anchor:
            try:
                anchor_dt = datetime.strptime(anchor, "%Y-%m-%d %H:%M")
                anchor_ms = int(anchor_dt.timestamp() * 1000)
                tf_ms = TF_MS.get(tf, 900000)
                since = anchor_ms - int(limit * 0.6) * tf_ms
                exit_time = int(anchor_dt.timestamp())
            except Exception:
                since = None

        raw = ex.fetch_ohlcv(symbol, tf, since=since, limit=limit)
        candles = [{"time": int(c[0] // 1000), "open": c[1], "high": c[2], "low": c[3], "close": c[4]} for c in raw]
        return jsonify({"candles": candles, "exit_time": exit_time})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

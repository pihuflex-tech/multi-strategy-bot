"""
dashboard.py
Zero-Pandas Mobile Leaderboard Server (Port 8080).
Displays real-time MTM + Realized Net PnL across all 100 strategies.
"""

import os
import json
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
LIVE_STATE_FILE = "logs/live_state.json"

HTML_UI = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>100-Strategy Quant Leaderboard</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
        body { background:#0b0f19; color:#e2e8f0; padding:12px; }
        .top-bar { display:flex; justify-content:space-between; align-items:center; background:#111827; padding:12px; border-radius:10px; border:1px solid #1f2937; margin-bottom:12px; }
        .brand { font-size:16px; font-weight:800; color:#38bdf8; }
        .ticker { font-size:12px; color:#94a3b8; margin-top:3px; }
        .badge { background:#059669; color:#fff; font-size:11px; font-weight:700; padding:4px 8px; border-radius:8px; }
        .grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin-bottom:12px; }
        .card { background:#111827; border:1px solid #1f2937; padding:10px; border-radius:10px; text-align:center; }
        .lbl { font-size:10px; color:#64748b; text-transform:uppercase; margin-bottom:4px; }
        .val { font-size:15px; font-weight:800; }
        .pos { color:#10b981; }
        .neg { color:#ef4444; }
        .tbl-box { background:#111827; border:1px solid #1f2937; border-radius:10px; overflow-x:auto; }
        table { width:100%; border-collapse:collapse; font-size:12px; }
        th { background:#1e293b; color:#94a3b8; text-align:left; padding:9px; font-weight:600; }
        td { padding:9px; border-top:1px solid #1f2937; }
        .cluster-tag { font-size:10px; color:#38bdf8; background:#0f172a; padding:2px 6px; border-radius:4px; display:inline-block; }
    </style>
</head>
<body>
    <div class="top-bar">
        <div>
            <div class="brand">⚡ NIFTY 100-QUANT LEADERBOARD</div>
            <div id="mkt-bar" class="ticker">Spot: ₹0.00 | Fut: ₹0.00 | VIX: 0.00</div>
        </div>
        <div class="badge">LIVE 1.5s</div>
    </div>

    <div class="grid">
        <div class="card">
            <div class="lbl">Combined P&L</div>
            <div id="grand-pnl" class="val">₹0.00</div>
        </div>
        <div class="card">
            <div class="lbl">Live MTM</div>
            <div id="mtm-pnl" class="val">₹0.00</div>
        </div>
        <div class="card">
            <div class="lbl">Active Legs</div>
            <div id="open-legs" class="val" style="color:#f59e0b;">0</div>
        </div>
    </div>

    <div class="tbl-box">
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Strategy</th>
                    <th>Open</th>
                    <th>Live MTM</th>
                    <th>Total P&L</th>
                </tr>
            </thead>
            <tbody id="rows">
                <tr><td colspan="5" style="text-align:center;">Waiting for engine ticks...</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        async function refresh() {
            try {
                const r = await fetch('/api/live');
                const d = await r.json();
                if (!d.leaderboard) return;

                document.getElementById('mkt-bar').textContent = `Spot: ₹${d.spot} | Fut: ₹${d.futures} | VIX: ${d.vix}`;
                
                const gEl = document.getElementById('grand-pnl');
                gEl.textContent = '₹' + d.grand_total_pnl.toFixed(2);
                gEl.className = 'val ' + (d.grand_total_pnl >= 0 ? 'pos' : 'neg');

                const mEl = document.getElementById('mtm-pnl');
                mEl.textContent = '₹' + d.total_unrealized.toFixed(2);
                mEl.className = 'val ' + (d.total_unrealized >= 0 ? 'pos' : 'neg');

                document.getElementById('open-legs').textContent = d.active_legs;

                let html = '';
                d.leaderboard.forEach((s, idx) => {
                    const c1 = s.unrealized_pnl >= 0 ? 'pos' : 'neg';
                    const c2 = s.total_pnl >= 0 ? 'pos' : 'neg';
                    html += `<tr>
                        <td style="color:#64748b;">${idx + 1}</td>
                        <td>
                            <div style="font-weight:700;">${s.strategy_id}</div>
                            <span class="cluster-tag">${s.cluster}</span>
                        </td>
                        <td>${s.open_legs}</td>
                        <td class="${c1}">₹${s.unrealized_pnl.toFixed(2)}</td>
                        <td class="${c2}" style="font-weight:700;">₹${s.total_pnl.toFixed(2)}</td>
                    </tr>`;
                });
                document.getElementById('rows').innerHTML = html;
            } catch (e) {}
        }
        setInterval(refresh, 1500);
        refresh();
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_UI)


@app.route("/api/live")
def api_live():
    if not os.path.exists(LIVE_STATE_FILE):
        return jsonify({})
    try:
        with open(LIVE_STATE_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({})


if __name__ == "__main__":
    print("[*] Mobile Dashboard Live on: http://127.0.0.1:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
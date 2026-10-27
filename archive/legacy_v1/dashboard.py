import os
import json
import time
import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# Fallback API Ports (agar JSON file available na ho)
FEED_SERVER_URL = "http://127.0.0.1:5555/api/market_data"
CACHE_FILE_PATH = "/tmp/market_state.json"

# In-memory cached state
memory_state = {
    "spot_price": 0.0,
    "futures_price": 0.0,
    "total_mtm": 0.0,
    "active_legs": 0,
    "strategies": [],
    "last_updated": "Waiting for Feed...",
    "status": "INITIALIZING"
}

def load_system_state():
    global memory_state
    # Method 1: Atomic File Read (Zero Network Overhead)
    if os.path.exists(CACHE_FILE_PATH):
        try:
            with open(CACHE_FILE_PATH, "r") as f:
                data = json.load(f)
                memory_state.update(data)
                memory_state["status"] = "LIVE_FILE"
                return memory_state
        except (json.JSONDecodeError, IOError):
            pass  # File write conflict ke time fallback execute hoga

    # Method 2: Non-blocking HTTP Probe (0.8s strict timeout)
    try:
        res = requests.get(FEED_SERVER_URL, timeout=0.8)
        if res.status_code == 200:
            feed_data = res.json()
            memory_state["spot_price"] = feed_data.get("spot", memory_state["spot_price"])
            memory_state["futures_price"] = feed_data.get("futures", memory_state["futures_price"])
            memory_state["last_updated"] = time.strftime("%H:%M:%S")
            memory_state["status"] = "LIVE_HTTP"
    except Exception:
        memory_state["status"] = "CACHE_DEGRADED"

    return memory_state

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Portfolio Terminal</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
        body { background-color: #0b0e14; color: #e1e7ec; padding: 16px; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e2638; padding-bottom: 12px; margin-bottom: 16px; }
        .title { font-size: 1.1rem; font-weight: 700; color: #60a5fa; }
        .badge { font-size: 0.75rem; padding: 4px 8px; border-radius: 4px; font-weight: 600; }
        .status-live { background: #064e3b; color: #34d399; }
        .status-warn { background: #78350f; color: #fbbf24; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }
        .card { background: #131a29; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; }
        .card-label { font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px; text-transform: uppercase; }
        .card-val { font-size: 1.25rem; font-weight: bold; }
        .green { color: #10b981; }
        .red { color: #f43f5e; }
        .neutral { color: #f8fafc; }
        .leaderboard { width: 100%; border-collapse: collapse; background: #131a29; border-radius: 8px; overflow: hidden; border: 1px solid #1e293b; font-size: 0.85rem; }
        .leaderboard th { background: #1e293b; color: #94a3b8; text-align: left; padding: 8px 12px; }
        .leaderboard td { padding: 8px 12px; border-bottom: 1px solid #1e2638; }
        .empty-row { text-align: center; color: #64748b; padding: 16px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="title">NIFTY 100-QUANT ENGINE</div>
        <div id="status-badge" class="badge status-warn">CONNECTING</div>
    </div>

    <div class="grid">
        <div class="card">
            <div class="card-label">Nifty Spot</div>
            <div id="spot-price" class="card-val neutral">₹0.00</div>
        </div>
        <div class="card">
            <div class="card-label">Total MTM</div>
            <div id="mtm-value" class="card-val neutral">₹0.00</div>
        </div>
        <div class="card">
            <div class="card-label">Active Legs</div>
            <div id="active-legs" class="card-val neutral">0</div>
        </div>
        <div class="card">
            <div class="card-label">Last Refresh</div>
            <div id="last-updated" class="card-val neutral" style="font-size: 0.95rem;">--:--:--</div>
        </div>
    </div>

    <table class="leaderboard">
        <thead>
            <tr>
                <th>Strategy</th>
                <th>Type</th>
                <th>Status</th>
                <th>Realized P&L</th>
            </tr>
        </thead>
        <tbody id="strategy-rows">
            <tr><td colspan="4" class="empty-row">Loading active strategy telemetry...</td></tr>
        </tbody>
    </table>

    <script>
        let isPolling = false;

        async function fetchStream() {
            if (isPolling) return;
            isPolling = true;

            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 1200);

            try {
                const response = await fetch('/api/stream_data', { signal: controller.signal });
                clearTimeout(timeoutId);

                if (response.ok) {
                    const data = await response.json();
                    
                    document.getElementById('spot-price').innerText = `₹${parseFloat(data.spot_price || 0).toFixed(2)}`;
                    
                    const mtmElem = document.getElementById('mtm-value');
                    const mtmVal = parseFloat(data.total_mtm || 0);
                    mtmElem.innerText = `${mtmVal >= 0 ? '+' : ''}₹${mtmVal.toFixed(2)}`;
                    mtmElem.className = `card-val ${mtmVal >= 0 ? 'green' : 'red'}`;

                    document.getElementById('active-legs').innerText = data.active_legs || 0;
                    document.getElementById('last-updated').innerText = data.last_updated || '--:--:--';

                    const badge = document.getElementById('status-badge');
                    badge.innerText = data.status.startsWith('LIVE') ? 'LIVE FEED' : 'DEGRADED';
                    badge.className = data.status.startsWith('LIVE') ? 'badge status-live' : 'badge status-warn';

                    if (data.strategies && data.strategies.length > 0) {
                        const tbody = document.getElementById('strategy-rows');
                        tbody.innerHTML = data.strategies.map(s => `
                            <tr>
                                <td><b>${s.name}</b></td>
                                <td>${s.type}</td>
                                <td><span style="color: ${s.status === 'ACTIVE' ? '#34d399' : '#94a3b8'}">${s.status}</span></td>
                                <td class="${parseFloat(s.pnl || 0) >= 0 ? 'green' : 'red'}">${parseFloat(s.pnl || 0) >= 0 ? '+' : ''}₹${parseFloat(s.pnl || 0).toFixed(2)}</td>
                            </tr>
                        `).join('');
                    }
                }
            } catch (err) {
                const badge = document.getElementById('status-badge');
                badge.innerText = 'WAITING TICK';
                badge.className = 'badge status-warn';
            } finally {
                isPolling = false;
                setTimeout(fetchStream, 1000);
            }
        }

        fetchStream();
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/stream_data")
def api_stream_data():
    data = load_system_state()
    return jsonify(data)

if __name__ == "__main__":
    # Termux multi-client listener
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
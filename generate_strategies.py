"""
generate_strategies.py
Generates 100 parameterized quant strategies into strategies_config.json.
Zero external dependencies.
"""

import json

OUTPUT_FILE = "strategies_config.json"


def generate_all():
    strategies = []
    sid = 1

    # 1. Smart Straddles with SL-to-Cost (STRAT_001 - STRAT_010)
    for t in ["09:20:00", "09:35:00", "09:50:00", "10:15:00", "11:00:00"]:
        for sl in [20.0, 25.0]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Smart_Straddles",
                "name": f"Straddle_{t[:5].replace(':','')}_SL{int(sl)}",
                "entry_time": t,
                "exit_time": "15:15:00",
                "rule_type": "TIME_STRADDLE",
                "sl_pct": sl,
                "shift_to_cost": True,
                "target_pct": 45.0
            })
            sid += 1

    # 2. Dynamic Delta Neutral Strangles (STRAT_011 - STRAT_020)
    for d in [0.20, 0.25, 0.30, 0.35, 0.45]:
        for sl in [30.0, 35.0]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Dynamic_Delta",
                "name": f"Delta_{int(d*100)}_SL{int(sl)}",
                "entry_time": "09:25:00",
                "exit_time": "15:15:00",
                "rule_type": "GREEKS_DELTA",
                "target_delta": d,
                "sl_pct": sl,
                "target_pct": 40.0
            })
            sid += 1

    # 3. Gamma & Volume Velocity Buyers (STRAT_021 - STRAT_030)
    for off in [0, 50, -50, 100, -100]:
        for opt_t in ["CE", "PE"]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Gamma_Velocity",
                "name": f"Gamma_Buy_{off}_{opt_t}",
                "entry_time": "09:30:00",
                "exit_time": "15:10:00",
                "rule_type": "GAMMA_BUY",
                "strike_offset": off,
                "opt_type": opt_t,
                "min_gamma": 0.0011,
                "sl_pct": 15.0,
                "target_pct": 30.0
            })
            sid += 1

    # 4. OI Order-Flow & Short Covering (STRAT_031 - STRAT_040)
    for off in [0, 50, -50, 100, -100]:
        for act in ["BUY_CE", "BUY_PE"]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "OI_Order_Flow",
                "name": f"OI_Flow_{act}_Off{off}",
                "entry_time": "09:35:00",
                "exit_time": "15:10:00",
                "rule_type": "OI_FLOW",
                "strike_offset": off,
                "mode": act,
                "sl_pct": 18.0,
                "target_pct": 35.0
            })
            sid += 1

    # 5. Volatility Skew & Vega Crush (STRAT_041 - STRAT_050)
    for skew_min in [4.0, 5.0, 6.0, 7.0, 8.0]:
        for sl in [22.0, 28.0]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Vega_Skew",
                "name": f"Skew_Crush_{int(skew_min)}_SL{int(sl)}",
                "entry_time": "09:30:00",
                "exit_time": "15:15:00",
                "rule_type": "VEGA_SKEW",
                "min_skew": skew_min,
                "sl_pct": sl,
                "target_pct": 35.0
            })
            sid += 1

    # 6. Heavyweight Constituent Sniper (STRAT_051 - STRAT_060)
    for sync in [3, 4]:
        for off in [0, 50, -50, 100, 150]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Heavyweight_Sniper",
                "name": f"HW_Sync{sync}_Off{off}",
                "entry_time": "09:25:00",
                "exit_time": "15:15:00",
                "rule_type": "HEAVYWEIGHT_SYNC",
                "min_sync": sync,
                "strike_offset": off,
                "sl_pct": 20.0,
                "target_pct": 32.0
            })
            sid += 1

    # 7. Futures-Spot Basis Arbitrage (STRAT_061 - STRAT_070)
    for thresh in [10.0, 15.0, 20.0, 25.0, 30.0]:
        for sl_pt in [15.0, 20.0]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Basis_Arbitrage",
                "name": f"Basis_Gap{int(thresh)}_SL{int(sl_pt)}",
                "entry_time": "09:30:00",
                "exit_time": "15:15:00",
                "rule_type": "BASIS_ARB",
                "basis_threshold": thresh,
                "sl_points": sl_pt,
                "target_points": sl_pt * 1.8
            })
            sid += 1

    # 8. Wing-Hedged Iron Condors (STRAT_071 - STRAT_080)
    for t in ["09:25:00", "10:00:00", "11:00:00", "12:30:00", "13:30:00"]:
        for off in [0, 100]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Wing_Condors",
                "name": f"Condor_{t[:5].replace(':','')}_Off{off}",
                "entry_time": t,
                "exit_time": "15:15:00",
                "rule_type": "WING_CONDOR",
                "short_offset": off,
                "sl_pct": 30.0,
                "target_pct": 45.0
            })
            sid += 1

    # 9. Multi-Expiry Theta Calendars (STRAT_081 - STRAT_090)
    for exp_type in ["next_expiry", "monthly_expiry"]:
        for off in [0, 50, -50, 100, -100]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Theta_Calendar",
                "name": f"Cal_{exp_type[:4]}_Off{off}",
                "entry_time": "09:45:00",
                "exit_time": "15:10:00",
                "rule_type": "CALENDAR_SPREAD",
                "far_bucket": exp_type,
                "strike_offset": off,
                "sl_pct": 20.0,
                "target_pct": 25.0
            })
            sid += 1

    # 10. 0-DTE Afternoon Gamma Blast (STRAT_091 - STRAT_100)
    for t in ["13:30:00", "13:45:00", "14:00:00", "14:15:00", "14:30:00"]:
        for action in ["BUY", "SELL"]:
            strategies.append({
                "strategy_id": f"STRAT_{sid:03d}",
                "cluster": "Afternoon_0DTE",
                "name": f"0DTE_{action}_{t[:5].replace(':','')}",
                "entry_time": t,
                "exit_time": "15:15:00",
                "rule_type": "AFTERNOON_0DTE",
                "action": action,
                "sl_pct": 25.0,
                "target_pct": 50.0
            })
            sid += 1

    payload = {
        "version": "2.0.0",
        "total_strategies": len(strategies),
        "lot_size": 75,
        "strategies": strategies
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[+] Successfully generated {len(strategies)} strategies in {OUTPUT_FILE}")


if __name__ == "__main__":
    generate_all()
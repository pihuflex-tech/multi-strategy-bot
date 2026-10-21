# 🚀 Nifty 100-Multi-Strategy Quant Bot & Mobile Leaderboard (Termux Edition)

यह एक **संस्थागत स्तर (Institutional-Grade) का एल्गो-ट्रेडिंग क्वांट इंजन** है, जो स्वतंत्र रूप से आपके पहले से चल रहे **Nifty Feed Server (Port 5555)** से ZeroMQ (TCP) के माध्यम से 92-टोकन्स का लाइव डेटा (LTP, OI, Volume, Greeks, IV) पढ़ता है।

यह सिस्टम **Termux (Android)** पर बिना किसी भारी C-Dependency (जैसे Pandas) के पूरी तरह नेटिव पाइथन में 100 स्ट्रैटेजीज को एक साथ समानांतर रूप से प्रोसेस करता है, NSE F&O के वैधानिक टैक्स काटता है, और **Port 8080** पर एक डार्क-मोड, मोबाइल-फ्रेंडली लाइव वेब डैशबोर्ड होस्ट करता है।

---

## 📑 अनुक्रमणिका (Table of Contents)
1. [सिस्टम आर्किटेक्चर](#-सिस्टम-आर्किटेक्चर)
2. [10 क्वांट क्लस्टर्स व 100 स्ट्रैटेजीज](#-10-क्वांट-क्लस्टर्स-व-100-स्ट्रैटेजीज)
3. [टैक्स और ऑडिट लॉगिंग (19-कॉलम CSV)](#-टैक्स-और-ऑडिट-लॉगिंग-19-कॉलम-csv)
4. [Termux में इंस्टॉलेशन व सेटअप](#-termux-में-इंस्टॉलेशन-व-सेटअप)
5. [प्रोजेक्ट कैसे चलाएं (Run & Automate)](#-प्रोजेक्ट-कैसे-चलाएं-run--automate)
6. [मोबाइल पर लाइव डैशबोर्ड कैसे देखें?](#-मोबाइल-पर-लाइव-डैशबोर्ड-कैसे-देखें)

---

## 🏛️ सिस्टम आर्किटेक्चर

यह बॉट आपके **Nifty Feed Server** से पूरी तरह अलग (Decoupled) है:

```text
┌──────────────────────────────────────┐
│  Nifty Feed Server (Port 5555)       │  <-- [स्वतंत्र फीड सर्वर]
│  Streams 92 Tokens (Angel One Mode 2)│
└──────────────────┬───────────────────┘
                   │  ZeroMQ PUB (tcp://127.0.0.1:5555)
                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Multi-Strategy Engine (strategy_engine.py)                            │
│  - ZMQ SUB Receiver (Read-Only)                                        │
│  - In-Memory Evaluator: Evaluates 100 Strategies simultaneously        │
│  - Statutory Tax Engine (Brokerage, STT, GST, Exchange Charges)        │
└──────────────────┬─────────────────────────────────────────────────────┘
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
┌──────────────────┐  ┌──────────────────────────────────────────────────┐
│  logs/trades/    │  │  Mobile Web Dashboard (dashboard.py : Port 8080) │
│  trades_*.csv    │  │  - Auto-refreshes every 1.5s                     │
│  (100 Files)     │  │  - Open on Mobile Browser via Wi-Fi/Hotspot IP   │
└──────────────────┘  └──────────────────────────────────────────────────┘
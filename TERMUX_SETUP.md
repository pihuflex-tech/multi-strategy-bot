# Android Termux Installation & Setup Guide

This guide describes how to install and configure the **NIFTY Multi-Strategy Quant Bot** on Android using the **Termux** environment.

---

## 1. System Requirements & Compatibility

- **Device**: Android 10 or higher (ARM64 / AArch64 or x86_64).
- **RAM**: Minimum 2 GB (Recommended 4 GB+).
- **Storage**: ~500 MB free storage for Termux, Python packages, and trade logs.
- **Python**: Python 3.10, 3.11, or 3.12 (standard Termux package).
- **Zero Heavy C-Dependencies**: Uses native Python libraries for quantitative math (`math`, `datetime`, `collections`, `json`, `csv`). No `pandas`, `numpy`, or `scipy` required.

> [!NOTE]
> **Compatibility Status**: While all code is designed and syntax-tested for Linux/POSIX compliance with lightweight native standard libraries, **live execution on physical Android Termux devices remains unverified in this workstation engineering phase**.

---

## 2. Step 1: Install Termux

> [!IMPORTANT]
> Install Termux exclusively from **F-Droid** or **GitHub Releases**. Do NOT install from the Google Play Store (the Play Store build is obsolete and unsupported).

1. Download and install **Termux** from F-Droid: [https://f-droid.org/packages/com.termux/](https://f-droid.org/packages/com.termux/)
2. Open Termux and update the package index:
   ```bash
   pkg update && pkg upgrade -y
   ```

---

## 3. Step 2: Install Python & Build Tools

Install Python, Git, and build tools required for `pyzmq`:

```bash
pkg install -y python git clang libzmq libffi openssl
```

Verify Python installation:
```bash
python --version
```

---

## 4. Step 3: Clone or Copy Project Repository

### Option A: Clone via Git
```bash
git clone <YOUR_GIT_REPO_URL> multi-strategy-bot
cd multi-strategy-bot
```

### Option B: Copy from Workstation via Shared Storage
If copying files from your PC to phone storage:
1. Grant storage permission to Termux:
   ```bash
   termux-setup-storage
   ```
2. Copy the project folder into Termux home directory:
   ```bash
   cp -r ~/storage/shared/multi-strategy-bot ~/multi-strategy-bot
   cd ~/multi-strategy-bot
   ```

---

## 5. Step 4: Install Python Dependencies

Install the lightweight, pinned requirements:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

*Requirements include only:*
- `pyzmq>=22.0.0` (ZeroMQ subscriber)
- `flask>=2.0.0` (optional mobile dashboard telemetry)

---

## 6. Step 5: Android Battery Optimization Exemption

> [!WARNING]
> Android aggressive power management will kill background Python scripts when the screen turns off. You must exempt Termux from battery optimization:

1. In Termux, acquire wake-lock so Termux continues running when the screen is locked:
   ```bash
   termux-wake-lock
   ```
2. On your Android phone, go to:
   **Settings -> Apps -> Termux -> Battery -> Unrestricted (or Don't optimize)**.
3. If using Xiaomi (MIUI) or Vivo/Oppo/Realme, enable **Autostart** for Termux.

---

## 7. Step 6: Verify Offline Pipeline

Test that the bot environment is functioning cleanly offline:

```bash
python main.py --inspect
```

Output confirms all 25 strategies registered in `DISABLED` mode:
```text
[+] Registered Strategies: 25 / 25 verified independent strategy modules.
```

Check telemetry status offline:
```bash
python main.py --status
```
Output:
```text
=== SYSTEM TELEMETRY STATUS ===
  App Lifecycle: RUNNING | Bot Status: STANDBY
  Strategy Engine: ALL_DISABLED (0/25)
  Paper Execution: STANDBY
  Registered Strategies: 25
  Active Strategies: 0 (All others DISABLED)
  Open Positions: 0
  Total Unrealized MTM: INR 0
  Feed Connection: NOT_CONNECTED (Offline)
===============================
```

Test synthetic pipeline execution:
```bash
python main.py --test-tick
```
Output:
```text
[+] Pipeline execution successful.
    - Open Positions: 0
    - Open Legs: 0
    - Unrealized MTM: INR 0
```

*Your Android Termux environment setup is complete and ready for offline operation.*

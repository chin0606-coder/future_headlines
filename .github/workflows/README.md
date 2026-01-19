# Future Headlines (FH) 全球情報雷達 - V14 穩定版

每小時自動掃描 Polymarket，檢測新波動或高成交量事件。預設僅在終端打印警報，如需推播可選擇啟用 Telegram。

## 功能特點

- ✅ **智能去重**：使用 `history.json` 追蹤已掃描事件
- ✅ **多維度警報**：異動門檻、增量門檻、高額門檻（150,000 USD）
- ✅ **冷啟動保護**：首次運行僅建立數據基準，不發送通知
- ✅ **合規過濾**：自動排除包含 "Taiwan" 或 "台灣" 的事件
- ✅ **格式化通知**：區分新事件 🆕 和新波動 ⚡，包含完整資訊和連結
- ✅ **推播可選**：預設不推送 Telegram，透過 `--telegram` 參數啟用
- ✅ **雲端友好**：`history.json` 支援自訂路徑、原子寫入，適配 GitHub Actions

## 安裝

```bash
pip install -r requirements.txt
```

## 設定（Telegram 推播為可選）

預設不推播 Telegram，可直接跳過本節並運行腳本。

### 如需啟用 Telegram 推播
1. 取得 Bot Token：在 Telegram 搜尋 `@BotFather`，發送 `/newbot` 按指示創建
2. 取得 Chat ID：搜尋 `@userinfobot`，發送訊息取得 Chat ID
3. 設定環境變數（或用命令列參數）
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

## 使用方法

### 手動執行一次掃描

```bash
python polymarket_monitor.py --once
```

### 持續運行（每小時自動掃描）

腳本會自動每小時執行一次掃描。使用 `Ctrl+C` 停止。

### 啟用 Telegram 推播（可選）
```bash
python polymarket_monitor.py --telegram --token "your_token" --chat-id "your_chat_id"
```

### 自訂歷史記錄路徑（雲端/分區存放）
```bash
python polymarket_monitor.py --once --history-path "/tmp/history.json"
```

### 使用 systemd 或 cron 自動運行

#### systemd 服務（推薦）

創建 `/etc/systemd/system/polymarket-monitor.service`:

```ini
[Unit]
Description=Future Headlines Polymarket Monitor
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/future headlines
Environment="TELEGRAM_BOT_TOKEN=your_token"
Environment="TELEGRAM_CHAT_ID=your_chat_id"
ExecStart=/usr/bin/python3 /path/to/future headlines/polymarket_monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

啟動服務：
```bash
sudo systemctl enable polymarket-monitor
sudo systemctl start polymarket-monitor
```

#### cron 任務

編輯 crontab：
```bash
crontab -e
```

添加以下行（每小時執行）：
```
0 * * * * cd /path/to/future\ headlines && /usr/bin/python3 polymarket_monitor.py
```

## 警報邏輯說明

### 1. 異動門檻
- 新事件的 `one_day_price_change` 絕對值 >= 5.0% 時觸發

### 2. 增量門檻（The Filter）
- 已存在事件的當前變動值與上次記錄值的差值 >= 2.0% 時觸發
- 避免 24h 滾動數據導致的重複通知

### 3. 高額門檻
- 新事件且總成交量 >= 200,000 USD 時觸發

### 4. 冷啟動保護
- 首次運行時，`history.json` 為空
- 僅建立數據基準，不發送任何通知
- 避免累積的 500+ 個舊標的一口氣轟炸

## 檔案結構

```
future headlines/
├── polymarket_monitor.py    # 主監控腳本
├── history.json            # 歷史記錄（自動生成）
├── requirements.txt        # Python 依賴
└── README.md              # 說明文件
```

## 通知格式範例

### 🆕 新事件
```
🆕 [新事件]

📂 類別: Politics
📰 標題: Will Trump win the 2024 election?
📈 累積 Δ: +5.2%
💵 成交額: $150,000
🔗 連結: https://polymarket.com/event/trump-2024
```

### ⚡ 新波動
```
⚡ [新波動] +2.5%

📂 類別: Crypto
📰 標題: Will BTC reach $100k by 2025?
📈 累積 Δ: +7.8%
💵 成交額: $300,000
🔗 連結: https://polymarket.com/event/btc-100k
```

### 💰 高額新事件
```
💰 [高額新事件]

📂 類別: Economics
📰 標題: Will the Fed cut rates in Q1 2024?
📈 累積 Δ: +1.5%
💵 成交額: $250,000
🔗 連結: https://polymarket.com/event/fed-rates-q1
```

## 注意事項

1. **首次運行**：腳本會自動進入冷啟動模式，不會發送通知
2. **歷史記錄**：每次掃描後都會更新 `history.json`，確保下小時的對比基準是最新的
3. **合規過濾**：自動排除包含 "Taiwan" 或 "台灣" 的事件
4. **錯誤處理**：API 請求失敗時會記錄錯誤但不會中斷程序

## 故障排除

### Telegram 通知未發送
- 檢查 Bot Token 和 Chat ID 是否正確
- 確認 Bot 已啟動（發送 `/start` 給你的 Bot）
- 檢查網路連線

### 歷史記錄未更新
- 檢查 `history.json` 檔案權限
- 確認工作目錄有寫入權限

### API 請求失敗
- 檢查網路連線
- 確認 Polymarket API 端點可訪問
- 查看錯誤日誌

## 授權

本專案僅供學習和研究使用。

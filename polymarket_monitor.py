#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Future Headlines (FH) 全球情報雷達 - V14 穩定版
每小時掃描 Polymarket，檢測新波動或高成交量事件並發送 Telegram 通知
"""

import json
import os
import sys
import time
import tempfile
from pathlib import Path
import requests
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import schedule
import argparse


class PolymarketMonitor:
    def __init__(
        self,
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        history_file: str = "history.json",
        enable_telegram: bool = False,
    ):
        """
        初始化監控器
        
        Args:
            telegram_bot_token: Telegram Bot Token
            telegram_chat_id: Telegram Chat ID
            history_file: 歷史記錄檔案路徑
            enable_telegram: 是否啟用 Telegram 推播（預設關閉）
        """
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.history_path = Path(history_file).expanduser().resolve()
        self.enable_telegram = enable_telegram
        
        # 警報門檻設定
        self.VOLATILITY_THRESHOLD = 5.0  # 異動門檻：5.0%
        self.INCREMENT_THRESHOLD = 2.0   # 增量門檻：2.0%
        # 高額門檻：降到 150,000 USD，以更快捕捉大額流入
        self.HIGH_VOLUME_THRESHOLD = 150000
        
        # API 端點
        self.API_URL = "https://gamma-api.polymarket.com/events?closed=false&limit=500&active=true"
        
        # 合規過濾關鍵字
        self.EXCLUDE_KEYWORDS = ["Taiwan", "台灣", "taiwan"]
        
        # 冷啟動標記
        self.is_cold_start = not self.history_path.exists() or self.history_path.stat().st_size == 0
    
    def _ensure_history_dir(self):
        """確保歷史檔案的資料夾存在"""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> Dict[str, Dict]:
        """載入歷史記錄（若不存在或解析失敗則回傳空 dict）"""
        self._ensure_history_dir()
        if not self.history_path.exists():
            return {}
        try:
            with self.history_path.open('r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
        except Exception as exc:
            print(f"⚠️ 讀取歷史記錄失敗: {exc}")
            return {}
    
    def _atomic_write_json(self, data: Dict[str, Dict]):
        """以原子方式寫入 JSON，避免部分寫入導致檔案損壞"""
        self._ensure_history_dir()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self.history_path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as tmp_file:
                json.dump(data, tmp_file, ensure_ascii=False, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, self.history_path)
        except Exception as exc:
            print(f"⚠️ 寫入歷史記錄失敗: {exc}")
            # 嘗試清理暫存檔
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            raise

    def save_history(self, history: Dict[str, Dict]):
        """保存歷史記錄（原子寫入）"""
        self._atomic_write_json(history)
    
    def fetch_polymarket_data(self) -> List[Dict]:
        """從 Polymarket API 獲取數據"""
        try:
            response = requests.get(self.API_URL, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"❌ 獲取 Polymarket 數據失敗: {e}")
            return []
    
    def should_exclude(self, title: str) -> bool:
        """檢查是否應該排除該事件（合規過濾）"""
        title_lower = title.lower()
        return any(keyword.lower() in title_lower for keyword in self.EXCLUDE_KEYWORDS)
    
    def calculate_delta(self, one_day_price_change: Optional[float]) -> float:
        """計算變動值（百分比）"""
        if one_day_price_change is None:
            return 0.0
        return one_day_price_change * 100
    
    def should_alert(self, event: Dict, history: Dict[str, Dict]) -> Tuple[bool, str, Optional[float]]:
        """
        判斷是否應該發送警報
        
        Returns:
            (should_alert, alert_type, delta_change)
            alert_type: "new_event" | "new_volatility" | "high_volume"
            delta_change: 增量變化（僅用於 new_volatility）
        """
        event_id = event.get('slug', '')
        title = event.get('question', '')
        volume = event.get('volume', 0)
        one_day_change = event.get('one_day_price_change')
        current_delta = self.calculate_delta(one_day_change)
        
        # 合規過濾
        if self.should_exclude(title):
            return False, "", None
        
        # 檢查是否為新事件
        is_new_event = event_id not in history
        
        # 優先級 1: 高額門檻（新事件且成交量 >= 200,000 USD）
        if is_new_event and volume >= self.HIGH_VOLUME_THRESHOLD:
            return True, "high_volume", None
        
        # 優先級 2: 新事件異動門檻（abs(delta) >= 5.0%）
        if is_new_event:
            if abs(current_delta) >= self.VOLATILITY_THRESHOLD:
                return True, "new_event", None
        
        # 優先級 3: 已存在事件的增量變化（current_delta - last_delta >= 2.0%）
        if not is_new_event:
            last_record = history.get(event_id, {})
            last_delta = last_record.get('delta', 0.0)
            
            # 計算增量變化
            delta_change = current_delta - last_delta
            
            # 增量門檻：變化超過 2.0%
            if abs(delta_change) >= self.INCREMENT_THRESHOLD:
                return True, "new_volatility", delta_change
        
        return False, "", None
    
    def format_telegram_message(self, event: Dict, alert_type: str, delta_change: Optional[float] = None) -> str:
        """格式化 Telegram 消息"""
        title = event.get('question', 'N/A')
        category = event.get('category', '未分類')
        volume = event.get('volume', 0)
        one_day_change = event.get('one_day_price_change')
        current_delta = self.calculate_delta(one_day_change)
        slug = event.get('slug', '')
        
        # Polymarket 連結
        polymarket_url = f"https://polymarket.com/event/{slug}"
        
        # 格式化成交量
        volume_str = f"${volume:,.0f}" if volume >= 1000 else f"${volume:.2f}"
        
        # 格式化累積 Δ
        delta_str = f"{current_delta:+.1f}%"
        
        # 根據警報類型構建消息
        if alert_type == "new_event":
            emoji = "🆕"
            header = f"{emoji} [新事件]"
            delta_info = f"累積 Δ: {delta_str}"
        elif alert_type == "new_volatility":
            emoji = "⚡"
            # 新波動顯示增量變化（漲跌方向）
            change_str = f"{delta_change:+.1f}%" if delta_change is not None else "N/A"
            header = f"{emoji} [新波動] {change_str}"
            delta_info = f"累積 Δ: {delta_str}"
        elif alert_type == "high_volume":
            emoji = "💰"
            header = f"{emoji} [高額新事件]"
            delta_info = f"累積 Δ: {delta_str}"
        else:
            header = "📊 [事件更新]"
            delta_info = f"累積 Δ: {delta_str}"
        
        message = f"""
{header}

📂 類別: {category}
📰 標題: {title}
📈 {delta_info}
💵 成交額: {volume_str}
🔗 連結: {polymarket_url}
"""
        return message.strip()
    
    def send_telegram_notification(self, message: str) -> bool:
        """發送 Telegram 通知；如未啟用則僅回傳 False"""
        if not self.enable_telegram:
            # 未啟用推播，直接跳過
            return False
        
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"❌ 發送 Telegram 通知失敗: {e}")
            return False
    
    def scan_and_alert(self):
        """執行掃描並發送警報"""
        print(f"\n{'='*60}")
        print(f"🕐 掃描時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        # 載入歷史記錄
        history = self.load_history()
        
        # 獲取 Polymarket 數據
        print("📡 正在獲取 Polymarket 數據...")
        events = self.fetch_polymarket_data()
        print(f"✅ 獲取到 {len(events)} 個事件")
        
        if not events:
            print("⚠️ 未獲取到任何事件，跳過本次掃描")
            return
        
        # 冷啟動保護：僅建立數據基準，不發送通知
        if self.is_cold_start:
            print("🔵 冷啟動模式：正在建立數據基準，不發送通知...")
            new_history = {}
            for event in events:
                event_id = event.get('slug', '')
                if event_id and not self.should_exclude(event.get('question', '')):
                    one_day_change = event.get('one_day_price_change')
                    current_delta = self.calculate_delta(one_day_change)
                    new_history[event_id] = {
                        'delta': current_delta,
                        'volume': event.get('volume', 0),
                        'title': event.get('question', ''),
                        'last_updated': datetime.now().isoformat()
                    }
            
            self.save_history(new_history)
            print(f"✅ 冷啟動完成，已記錄 {len(new_history)} 個事件")
            self.is_cold_start = False
            return
        
        # 正常掃描模式
        alerts_sent = 0
        updated_history = history.copy()
        
        for event in events:
            event_id = event.get('slug', '')
            if not event_id:
                continue
            
            # 更新歷史記錄（不論是否發送通知）
            one_day_change = event.get('one_day_price_change')
            current_delta = self.calculate_delta(one_day_change)
            updated_history[event_id] = {
                'delta': current_delta,
                'volume': event.get('volume', 0),
                'title': event.get('question', ''),
                'last_updated': datetime.now().isoformat()
            }
            
            # 判斷是否應該發送警報
            should_alert, alert_type, delta_change = self.should_alert(event, history)
            
            if should_alert:
                message = self.format_telegram_message(event, alert_type, delta_change)
                
                # 若未啟用推播，打印到終端即可
                if not self.enable_telegram:
                    alerts_sent += 1
                    print(f"🔔 警報（未推播）: {event.get('question', '')[:80]}...")
                    print(message)
                    continue
                
                if self.send_telegram_notification(message):
                    alerts_sent += 1
                    print(f"✅ 已發送警報: {event.get('question', '')[:50]}...")
                else:
                    print(f"❌ 發送警報失敗: {event.get('question', '')[:50]}...")
        
        # 保存更新後的歷史記錄
        self.save_history(updated_history)
        
        print(f"\n📊 掃描完成:")
        print(f"   - 處理事件數: {len(events)}")
        print(f"   - 發送警報數: {alerts_sent}")
        print(f"   - 歷史記錄數: {len(updated_history)}")
        print(f"{'='*60}\n")
    
    def run_hourly(self):
        """每小時執行一次掃描"""
        schedule.every().hour.do(self.scan_and_alert)
        
        print("🚀 Future Headlines 監控系統已啟動")
        print("⏰ 將每小時自動掃描一次")
        print("按 Ctrl+C 停止\n")
        
        # 立即執行一次
        self.scan_and_alert()
        
        # 持續運行
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分鐘檢查一次


def main():
    """主函數"""
    parser = argparse.ArgumentParser(description='Future Headlines Polymarket Monitor')
    parser.add_argument('--once', action='store_true', help='僅執行一次掃描，不持續運行')
    parser.add_argument('--token', type=str, help='Telegram Bot Token（可選，優先使用環境變數）')
    parser.add_argument('--chat-id', type=str, help='Telegram Chat ID（可選，優先使用環境變數）')
    parser.add_argument('--telegram', action='store_true', help='啟用 Telegram 推播（預設關閉）')
    parser.add_argument('--history-path', type=str, default='history.json', help='歷史記錄檔案路徑（預設 repo 根目錄下 history.json）')
    
    args = parser.parse_args()
    
    # 從命令行參數或環境變數獲取 Telegram 憑證
    telegram_bot_token = args.token or os.getenv('TELEGRAM_BOT_TOKEN', '')
    telegram_chat_id = args.chat_id or os.getenv('TELEGRAM_CHAT_ID', '')
    
    # 若啟用推播但缺少憑證，提示錯誤
    if args.telegram and (not telegram_bot_token or not telegram_chat_id):
        print("❌ 錯誤: 啟用推播需要 Telegram 憑證")
        print("\n使用方法:")
        print("  export TELEGRAM_BOT_TOKEN='your_bot_token'")
        print("  export TELEGRAM_CHAT_ID='your_chat_id'")
        print("  python polymarket_monitor.py --telegram")
        return
    
    # 創建監控器（預設不推播）
    monitor = PolymarketMonitor(
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        enable_telegram=args.telegram,
        history_file=args.history_path,
    )
    
    try:
        if args.once:
            # 僅執行一次掃描
            print("🔍 執行單次掃描模式...\n")
            monitor.scan_and_alert()
        else:
            # 持續運行模式
            monitor.run_hourly()
    except KeyboardInterrupt:
        print("\n\n👋 監控系統已停止")


if __name__ == "__main__":
    main()

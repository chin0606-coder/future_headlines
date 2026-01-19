#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Future Headlines (FH) 全球情報雷達 - V16 穩定版
修正：Markets API 數據類型強制轉換 (String -> Float) 避免崩潰
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
        daily_mode: bool = False,
    ):
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.history_path = Path(history_file).expanduser().resolve()
        self.enable_telegram = enable_telegram
        self.daily_mode = daily_mode
        
        self.VOLATILITY_THRESHOLD = 5.0
        self.INCREMENT_THRESHOLD = 2.0
        self.HIGH_VOLUME_THRESHOLD = 150000
        
        # Markets API
        self.API_URL = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=500&order=volume&ascending=false"
        
        self.EXCLUDE_KEYWORDS = ["Taiwan", "台灣", "taiwan"]
        self.is_cold_start = not self.history_path.exists() or self.history_path.stat().st_size == 0
    
    def _ensure_history_dir(self):
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def load_history(self) -> Dict[str, Dict]:
        self._ensure_history_dir()
        if not self.history_path.exists(): return {}
        try:
            with self.history_path.open('r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    
    def save_history(self, history: Dict[str, Dict]):
        self._ensure_history_dir()
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(self.history_path.parent), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as tmp_file:
                json.dump(history, tmp_file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.history_path)
        except Exception:
            try: os.remove(tmp_path)
            except: pass

    def fetch_polymarket_data(self) -> List[Dict]:
        try:
            response = requests.get(self.API_URL, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"❌ 獲取 Polymarket 數據失敗: {e}")
            return []
    
    def should_exclude(self, title: str) -> bool:
        return any(k.lower() in title.lower() for k in self.EXCLUDE_KEYWORDS)
    
    def parse_float(self, value) -> float:
        """安全地將數據轉換為浮點數"""
        try:
            if value is None: return 0.0
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def calculate_delta(self, one_day_price_change) -> float:
        val = self.parse_float(one_day_price_change)
        return val * 100

    def format_short_volume(self, volume) -> str:
        val = self.parse_float(volume)
        if val >= 1_000_000: return f"{val/1_000_000:.1f}M"
        if val >= 1_000: return f"{val/1_000:.1f}K"
        return f"{val:.0f}"
    
    def should_alert(self, event: Dict, history: Dict[str, Dict]) -> Tuple[bool, str, Optional[float]]:
        if self.daily_mode: return False, "", None

        event_id = event.get('id', '') 
        title = event.get('question', '')
        volume = self.parse_float(event.get('volume')) # ✅ 強制轉型
        current_delta = self.calculate_delta(event.get('one_day_price_change'))
        
        if self.should_exclude(title): return False, "", None
        
        is_new_event = event_id not in history
        
        if is_new_event and volume >= self.HIGH_VOLUME_THRESHOLD:
            return True, "high_volume", None
        
        if is_new_event:
            if abs(current_delta) >= self.VOLATILITY_THRESHOLD:
                return True, "new_event", None
        
        if not is_new_event:
            last_delta = history.get(event_id, {}).get('delta', 0.0)
            delta_change = current_delta - last_delta
            if abs(delta_change) >= self.INCREMENT_THRESHOLD:
                return True, "new_volatility", delta_change
        
        return False, "", None
    
    def format_telegram_message(self, event: Dict, alert_type: str, delta_change: Optional[float] = None) -> str:
        title = event.get('question', 'N/A')
        volume = self.parse_float(event.get('volume')) # ✅ 強制轉型
        current_delta = self.calculate_delta(event.get('one_day_price_change'))
        slug = event.get('slug', '')
        
        polymarket_url = f"https://polymarket.com/event/{slug}"
        volume_str = f"${volume:,.0f}"
        delta_str = f"{current_delta:+.1f}%"
        
        if alert_type == "new_event": header = "🆕 [新事件]"
        elif alert_type == "new_volatility": header = f"⚡ [新波動] {delta_change:+.1f}%"
        elif alert_type == "high_volume": header = "💰 [高額新事件]"
        else: header = "📊 [事件更新]"
        
        return f"{header}\n📰 {title}\n📈 累積 Δ: {delta_str}\n💵 Vol: {volume_str}\n🔗 {polymarket_url}"

    def build_daily_report(self, events: List[Dict]) -> str:
        if not events: return ""

        filtered = []
        for ev in events:
            title = ev.get('question', '')
            if self.should_exclude(title): continue
            
            # ✅ 強制轉型：確保音量和價格都是數字
            vol = self.parse_float(ev.get('volume'))
            price = self.parse_float(ev.get('price') or ev.get('currentPrice') or ev.get('lastTradePrice'))
            change = self.calculate_delta(ev.get('one_day_price_change'))
            
            filtered.append({
                "title": title,
                "volume": vol,
                "change_pct": change,
                "prob": price * 100,
                "slug": ev.get('slug', '')
            })

        if not filtered: return ""

        # 因為已經轉成 float，這裡的排序就不會報錯了
        top_volume = sorted(filtered, key=lambda x: x["volume"], reverse=True)[:5]
        top_gainers = sorted(filtered, key=lambda x: x["change_pct"], reverse=True)[:3]

        def fmt_item(item):
            url = f"https://polymarket.com/event/{item['slug']}"
            return f"• <a href='{url}'>{item['title']}</a> | 機率 {item['prob']:.1f}% | 量 {self.format_short_volume(item['volume'])}"

        scan_time = datetime.now().strftime('%Y-%m-%d %H:%M')
        lines = [
            f"<b>☀️ FH 每日情報</b> ({scan_time})",
            "",
            "<b>🔥 資金熱點 (Top Volume)</b>",
        ]
        lines += [fmt_item(it) for it in top_volume] if top_volume else ["(無資料)"]
        lines += ["", "<b>🚀 飆升潛力 (Top Gainers)</b>"]
        lines += [fmt_item(it) for it in top_gainers] if top_gainers else ["(無資料)"]

        return "\n".join(lines)
    
    def send_telegram_notification(self, message: str) -> bool:
        if not self.enable_telegram: return False
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=10
            ).raise_for_status()
            return True
        except Exception as e:
            print(f"❌ Telegram Error: {e}")
            return False
    
    def scan_and_alert(self):
        print(f"🕐 掃描時間: {datetime.now().strftime('%H:%M:%S')}")
        history = self.load_history()
        events = self.fetch_polymarket_data()
        print(f"✅ 獲取到 {len(events)} 個市場數據")
        
        if not events: return

        if self.daily_mode:
            report = self.build_daily_report(events)
            if report:
                self.send_telegram_notification(report)
                print("✅ 晨報已發送")
            
            updated_history = history.copy()
            for ev in events:
                if ev.get('id'):
                    updated_history[ev['id']] = {
                        'delta': self.calculate_delta(ev.get('one_day_price_change')),
                        'last_updated': datetime.now().isoformat()
                    }
            self.save_history(updated_history)
            return

        alerts_sent = 0
        updated_history = history.copy()
        for event in events:
            event_id = event.get('id')
            if not event_id: continue
            
            updated_history[event_id] = {
                'delta': self.calculate_delta(event.get('one_day_price_change')),
                'last_updated': datetime.now().isoformat()
            }
            
            should_alert, alert_type, delta_change = self.should_alert(event, history)
            if should_alert:
                msg = self.format_telegram_message(event, alert_type, delta_change)
                if self.send_telegram_notification(msg):
                    alerts_sent += 1
                    print(f"🔔 已推播: {event.get('question', '')[:20]}...")

        self.save_history(updated_history)
        print(f"📊 掃描完成: 發送 {alerts_sent} 則警報")

    def run_hourly(self):
        schedule.every().hour.do(self.scan_and_alert)
        print("🚀 啟動監控...")
        self.scan_and_alert()
        while True:
            schedule.run_pending()
            time.sleep(60)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--daily', action='store_true')
    parser.add_argument('--telegram', action='store_true')
    parser.add_argument('--token', type=str)
    parser.add_argument('--chat-id', type=str)
    parser.add_argument('--history-path', type=str, default='history.json')
    args = parser.parse_args()
    
    token = args.token or os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = args.chat_id or os.getenv('TELEGRAM_CHAT_ID', '')
    
    monitor = PolymarketMonitor(
        telegram_bot_token=token, telegram_chat_id=chat_id,
        enable_telegram=args.telegram, history_file=args.history_path, daily_mode=args.daily
    )
    
    if args.once: monitor.scan_and_alert()
    else: monitor.run_hourly()

if __name__ == "__main__":
    main()

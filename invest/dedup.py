"""
7 天去重：记录已报过的新闻/财报 id，运行后清理 7 天前记录
"""
import json
import os
import time

HISTORY_DAYS = 7
DEFAULT_FILE = "invest_history.json"


class InvestHistoryManager:
    def __init__(self, file_path=None):
        self.file_path = file_path or os.path.join(os.path.dirname(os.path.dirname(__file__)), DEFAULT_FILE)
        self.data = self._load()

    def _load(self):
        if not os.path.exists(self.file_path):
            return {"items": []}
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"items": []}

    def _ensure_items(self):
        if "items" not in self.data:
            self.data["items"] = []
        return self.data["items"]

    def is_reported(self, item_id):
        """是否在过去 7 天内已报过"""
        now = time.time()
        cutoff = now - (HISTORY_DAYS * 24 * 3600)
        items = self._ensure_items()
        for x in items:
            if x.get("id") == item_id and x.get("reported_at", 0) > cutoff:
                return True
        return False

    def mark_reported(self, item_id):
        """标记为已报（本次运行内会写入，save_and_clean 时持久化）"""
        self._ensure_items().append({"id": item_id, "reported_at": int(time.time())})

    def save_and_clean(self):
        """持久化并清理 7 天前的记录"""
        now = time.time()
        cutoff = now - (HISTORY_DAYS * 24 * 3600)
        items = self._ensure_items()
        kept = [x for x in items if x.get("reported_at", 0) > cutoff]
        self.data["items"] = kept
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        print(f"投资日报去重：清理后剩余 {len(kept)} 条记录")

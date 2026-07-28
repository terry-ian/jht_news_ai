#!/usr/bin/env python3
"""
喬山 AI 全球健身器材產業情報儀表板 - 本機靜態伺服器
=====================================================

用途：
  瀏覽器以 file:// 直接開啟 index.html 時，fetch('./data/news.json')
  會因瀏覽器安全限制（CORS / 跨來源限制本機檔案）而失敗。
  必須透過 HTTP 伺服器提供靜態檔案，fetch 才能正常讀取 JSON。

用法（二擇一即可，效果相同）：

  方法一（本腳本，含快取關閉，避免看到舊的 news.json）：
      python serve.py            # 預設埠 8000
      python serve.py 8080       # 指定埠號

  方法二（Python 內建，最簡單）：
      cd fitness-dashboard
      python -m http.server 8000

啟動後開啟瀏覽器：
      http://localhost:8000

僅供本機開發預覽使用，預設綁定 127.0.0.1（僅本機可存取）。
若需要讓區網或外部裝置存取，需自行改為 0.0.0.0 並開放防火牆埠，
請先評估風險（本機開發用途通常不建議對外開放）。
"""

import sys
import http.server
import socketserver
import os

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # 避免瀏覽器快取 news.json，導致「重新整理資料」按鈕看不到最新內容
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        super().end_headers()


if __name__ == '__main__':
    with socketserver.TCPServer(("127.0.0.1", PORT), NoCacheHandler) as httpd:
        print(f"喬山 AI 產業情報儀表板 - 本機伺服器已啟動")
        print(f"目錄: {DIRECTORY}")
        print(f"請開啟瀏覽器造訪: http://localhost:{PORT}")
        print("按 Ctrl+C 停止伺服器")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n伺服器已停止。")

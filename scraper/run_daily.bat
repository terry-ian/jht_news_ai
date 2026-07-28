@echo off
REM ============================================================
REM  fitness-dashboard 新聞爬蟲 - 每日自動執行批次檔
REM  以 Anaconda Python 執行 fetch_news.py（合併累加、不刪舊資料）
REM  可由 Windows 工作排程器 (Task Scheduler / schtasks) 每日呼叫
REM ============================================================

setlocal

REM Anaconda Python 路徑（如有變動請自行修改）
set "PYTHON=C:\Users\troy8\anaconda3\python.exe"

REM 腳本所在資料夾（本 .bat 檔的所在目錄）
set "SCRIPT_DIR=%~dp0"

cd /d "%SCRIPT_DIR%"

echo [%date% %time%] 開始執行 fetch_news.py >> "%SCRIPT_DIR%scrape_log.txt"

"%PYTHON%" "%SCRIPT_DIR%fetch_news.py" 1>> "%SCRIPT_DIR%run_daily.out.log" 2>> "%SCRIPT_DIR%run_daily.err.log"

echo [%date% %time%] fetch_news.py 執行結束，ExitCode=%ERRORLEVEL% >> "%SCRIPT_DIR%scrape_log.txt"

endlocal

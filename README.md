# Mongo Transfer

Windows 上的 MongoDB 匯入匯出 GUI，底層呼叫 MongoDB Database Tools（mongodump / mongorestore / mongoexport / mongoimport）

| 格式 | 匯出 | 匯入 |
|------|------|------|
| JSON | mongoexport（可選 `--jsonArray`、`--pretty`） | mongoimport（自動判斷陣列或單行文件） |
| CSV  | mongoexport `--type=csv`（欄位可自動偵測，巢狀欄位展開成 `a.b`） | mongoimport `--type=csv --headerline` |
| BSON | mongodump（可選 `--gzip`） | mongorestore（`.bson` / `.bson.gz`，可整個 dump 資料夾加入） |

## 需求

- Python 3.13
- [MongoDB Database Tools](https://www.mongodb.com/try/download/database-tools)：會依序找「設定」指定的路徑、PATH、`C:\tools\mongodb-tools\bin`、`C:\Program Files\MongoDB\Tools\*\bin`

## 執行

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

## 打包成 exe

```powershell
.\.venv\Scripts\pip install pyinstaller
.\.venv\Scripts\pyinstaller --noconfirm --windowed --name MongoTransfer app.py
```

輸出在 `dist\MongoTransfer\MongoTransfer.exe`

## 資料存放

- 連線清單：`%APPDATA%\mongo-transfer\connections.json`，連線字串以 Windows DPAPI 加密（只有同一個 Windows 帳號能解開）
- 執行時連線字串寫進暫存 `--config` 檔傳給 mongo tools，不出現在命令列，每個工作結束即刪除；程式被強制結束留下的檔案，下次啟動時清除
- 匯出檔名中 Windows 不允許的字元（`<>:"/\|?*`）會換成 `_`

## 測試

```powershell
.\.venv\Scripts\python test_tools.py
```

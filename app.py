import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)
from pymongo import MongoClient

import store
import tools

FORMATS = ("json", "csv", "bson")


# ponytail: 同步連線，連不上時 UI 會卡住最多 5 秒；要改善再搬到 QThread
def connect(uri):
    QApplication.setOverrideCursor(Qt.WaitCursor)
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        return client, [d["name"] for d in client.list_databases(nameOnly=True, authorizedDatabases=True)]
    except Exception:
        client.close()
        raise
    finally:
        QApplication.restoreOverrideCursor()


def button_row(*buttons):
    row = QHBoxLayout()
    for text, fn in buttons:
        b = QPushButton(text)
        b.clicked.connect(fn)
        row.addWidget(b)
    row.addStretch()
    return row


class ConnDialog(QDialog):
    def __init__(self, parent, conn=None):
        super().__init__(parent)
        self.setWindowTitle("連線設定")
        self.resize(600, 0)
        self.name = QLineEdit(conn["name"] if conn else "")
        self.uri = QLineEdit(conn["uri"] if conn else "mongodb://localhost:27017")
        self.uri.setPlaceholderText("mongodb://user:pass@host:27017/?authSource=admin")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("名稱", self.name)
        form.addRow("連線字串", self.uri)
        form.addRow(button_row(("測試連線", self.test)))
        form.addRow(buttons)

    def value(self):
        return {"name": self.name.text().strip(), "uri": self.uri.text().strip()}

    def test(self):
        try:
            client, dbs = connect(self.value()["uri"])
            client.close()
            QMessageBox.information(self, "測試連線", f"連線成功，可存取 {len(dbs)} 個資料庫")
        except Exception as e:
            QMessageBox.warning(self, "測試連線", f"連線失敗：\n{e}")

    def save(self):
        if not all(self.value().values()):
            QMessageBox.warning(self, "連線設定", "名稱與連線字串都必須填寫")
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, conns):
        super().__init__()
        self.setWindowTitle("Mongo Transfer")
        self.resize(1100, 740)
        self.settings = store.load_settings()
        self.tools_dir = tools.find_tools_dir(self.settings.get("tools_dir"))
        self.conns = conns
        self.client = self.uri = self.cfg = None
        self.queue, self.failed, self.total = [], 0, 0

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)

        self.menuBar().addMenu("設定").addAction("mongo tools 路徑…", self.choose_tools_dir)

        self.conn_list = QListWidget()
        self.conn_list.itemDoubleClicked.connect(self.do_connect)
        self.status = QLabel("未連線")
        self.left = QWidget()
        lv = QVBoxLayout(self.left)
        lv.addWidget(QLabel("連線清單（雙擊連線）"))
        lv.addWidget(self.conn_list)
        lv.addLayout(button_row(("新增", self.add_conn), ("編輯", self.edit_conn), ("刪除", self.del_conn)))
        lv.addLayout(button_row(("連線 / 重新整理", self.do_connect)))
        lv.addWidget(self.status)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._export_tab(), "匯出")
        self.tabs.addTab(self._import_tab(), "匯入")

        self.log_view = QPlainTextEdit(readOnly=True)
        self.cancel_btn = QPushButton("取消執行", enabled=False)
        self.cancel_btn.clicked.connect(self.cancel)
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)
        header = button_row(("清除紀錄", self.log_view.clear))
        header.insertWidget(0, QLabel("執行紀錄"))
        header.addWidget(self.cancel_btn)
        bv.addLayout(header)
        bv.addWidget(self.log_view)

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.tabs)
        right.addWidget(bottom)
        right.setSizes([480, 260])
        main = QSplitter()
        main.addWidget(self.left)
        main.addWidget(right)
        main.setSizes([260, 840])
        self.setCentralWidget(main)

        self.refresh_conns()
        self.log(f"mongo tools：{self.tools_dir}" if self.tools_dir
                 else "找不到 mongo tools，請從「設定 → mongo tools 路徑」指定 bin 資料夾")

    def _export_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        self.ex_db = QComboBox()
        self.ex_db.currentTextChanged.connect(self.load_colls)
        self.ex_colls = QListWidget()
        self.ex_fmt = self._format_group(w)
        self.ex_fmt.idToggled.connect(self._ex_format_changed)
        self.ex_query = QLineEdit(placeholderText='例：{"status": "A"}，留空匯出全部')
        self.ex_json_array = QCheckBox("JSON 陣列 (--jsonArray)")
        self.ex_pretty = QCheckBox("排版 (--pretty)")
        self.ex_gzip = QCheckBox("gzip 壓縮 (--gzip)")
        self.ex_fields = QLineEdit(placeholderText="逗號分隔，留空則每個 collection 取樣 200 筆自動偵測")
        self.ex_out = QLineEdit()

        fields = QHBoxLayout()
        fields.addWidget(self.ex_fields)
        self.ex_detect = QPushButton("偵測欄位")
        self.ex_detect.clicked.connect(self.detect_fields)
        fields.addWidget(self.ex_detect)
        opts = QHBoxLayout()
        for cb in (self.ex_json_array, self.ex_pretty, self.ex_gzip):
            opts.addWidget(cb)
        opts.addStretch()

        form.addRow("資料庫", self.ex_db)
        form.addRow("Collections", self.ex_colls)
        form.addRow("", button_row(("全選", lambda: self._check_all(True)), ("全不選", lambda: self._check_all(False))))
        form.addRow("格式", self._format_row(self.ex_fmt))
        form.addRow("查詢條件", self.ex_query)
        form.addRow("CSV 欄位", fields)
        form.addRow("選項", opts)
        form.addRow("輸出資料夾", self._path_row(self.ex_out))
        form.addRow("", button_row(("開始匯出", self.start_export)))
        self._ex_format_changed()
        return w

    def _ex_format_changed(self, *_):
        fmt = FORMATS[self.ex_fmt.checkedId()]
        self.ex_json_array.setEnabled(fmt == "json")
        self.ex_pretty.setEnabled(fmt == "json")
        self.ex_gzip.setEnabled(fmt == "bson")
        self.ex_fields.setEnabled(fmt == "csv")
        self.ex_detect.setEnabled(fmt == "csv")

    def load_colls(self, db):
        self.ex_colls.clear()
        if not db or not self.client:
            return
        try:
            names = sorted(self.client[db].list_collection_names())
        except Exception as e:
            self.log(f"讀取 {db} 的 collection 失敗：{e}")
            return
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.ex_colls.addItem(item)

    def _check_all(self, on):
        for i in range(self.ex_colls.count()):
            self.ex_colls.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def checked_colls(self):
        items = (self.ex_colls.item(i) for i in range(self.ex_colls.count()))
        return [it.text() for it in items if it.checkState() == Qt.Checked]

    def _sample_fields(self, db, coll):
        return ",".join(tools.flatten_keys(self.client[db][coll].find({}, limit=200)))

    def detect_fields(self):
        colls = self.checked_colls()
        if not self.client or not colls:
            QMessageBox.warning(self, "偵測欄位", "請先勾選 collection")
            return
        try:
            self.ex_fields.setText(self._sample_fields(self.ex_db.currentText(), colls[0]))
        except Exception as e:
            QMessageBox.warning(self, "偵測欄位", str(e))

    def start_export(self):
        db, colls, out = self.ex_db.currentText(), self.checked_colls(), self.ex_out.text().strip()
        fmt = FORMATS[self.ex_fmt.checkedId()]
        if not self._ready() or not (db and colls and out):
            QMessageBox.warning(self, "匯出", "請選擇資料庫、勾選 collection 並指定輸出資料夾")
            return
        jobs = []
        try:
            os.makedirs(out, exist_ok=True)
            for c in colls:
                fields = self.ex_fields.text().strip()
                if fmt == "csv" and not fields:
                    fields = self._sample_fields(db, c)
                    if not fields:
                        self.log(f"略過 {c}：沒有資料可偵測 CSV 欄位")
                        continue
                jobs.append(tools.export_args(
                    db, c, fmt, out, self.ex_query.text().strip(), fields,
                    self.ex_json_array.isChecked(), self.ex_pretty.isChecked(), self.ex_gzip.isChecked()))
        except Exception as e:
            QMessageBox.warning(self, "匯出", str(e))
            return
        self.run_jobs(jobs)

    def _import_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        self.im_db = QComboBox(editable=True)
        self.im_files = QTableWidget(0, 3)
        self.im_files.setHorizontalHeaderLabels(["檔案", "目標 collection（可編輯）", "格式"])
        self.im_files.setWordWrap(False)
        self.im_files.setTextElideMode(Qt.ElideLeft)
        self.im_files.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.im_files.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.im_drop = QCheckBox("匯入前先刪除 collection (--drop)")
        self.im_mode = QComboBox()
        self.im_mode.addItems(["insert", "upsert", "merge"])
        opts = QHBoxLayout()
        opts.addWidget(self.im_drop)
        opts.addWidget(QLabel("JSON/CSV 模式 (--mode)"))
        opts.addWidget(self.im_mode)
        opts.addStretch()

        form.addRow("目標資料庫", self.im_db)
        form.addRow("檔案", self.im_files)
        form.addRow("", button_row(("加入檔案…", self.add_files), ("加入 mongodump 資料夾…", self.add_dump_dir),
                                   ("移除選取", self.remove_files), ("清空", lambda: self.im_files.setRowCount(0))))
        form.addRow("選項", opts)
        form.addRow("", QLabel("CSV 需含標題列；JSON 自動判斷單行文件或陣列；BSON .gz 自動加 --gzip"))
        form.addRow("", button_row(("開始匯入", self.start_import)))
        return w

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "選擇匯入檔案", "", "資料檔 (*.json *.csv *.bson *.bson.gz);;所有檔案 (*)")
        self._add_import_paths(paths)

    def add_dump_dir(self):
        d = QFileDialog.getExistingDirectory(self, "選擇 mongodump 輸出資料夾")
        if d:
            self._add_import_paths(sorted(str(p) for p in Path(d).rglob("*")
                                          if p.name.lower().endswith((".bson", ".bson.gz"))))

    def _add_import_paths(self, paths):
        for p in map(os.path.normpath, paths):
            fmt = tools.file_format(p)
            if not fmt or p.lower().endswith(".metadata.json"):
                self.log(f"略過不支援的檔案：{p}")
                continue
            r = self.im_files.rowCount()
            self.im_files.insertRow(r)
            for col, text in enumerate((p, tools.coll_from_file(p), fmt.upper())):
                item = QTableWidgetItem(text)
                if col != 1:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.im_files.setItem(r, col, item)

    def remove_files(self):
        for r in sorted({i.row() for i in self.im_files.selectedIndexes()}, reverse=True):
            self.im_files.removeRow(r)

    def start_import(self):
        db = self.im_db.currentText().strip()
        rows = [(self.im_files.item(r, 0).text(), self.im_files.item(r, 1).text().strip())
                for r in range(self.im_files.rowCount())]
        if not self._ready() or not db or not rows or not all(c for _, c in rows):
            QMessageBox.warning(self, "匯入", "請指定目標資料庫、加入檔案，且每個檔案都要有目標 collection")
            return
        drop = self.im_drop.isChecked()
        if drop and QMessageBox.question(
                self, "確認刪除",
                f"匯入前會先刪除資料庫「{db}」中的下列 collection，原有資料將遺失：\n\n"
                + "\n".join(sorted({c for _, c in rows})) + "\n\n確定繼續？") != QMessageBox.Yes:
            return
        try:
            jobs = [tools.import_args(db, c, p, drop, self.im_mode.currentText()) for p, c in rows]
        except OSError as e:
            QMessageBox.warning(self, "匯入", str(e))
            return
        self.run_jobs(jobs)

    def _format_group(self, parent):
        group = QButtonGroup(parent)
        for i, name in enumerate(FORMATS):
            group.addButton(QRadioButton(name.upper()), i)
        group.button(0).setChecked(True)
        return group

    def _format_row(self, group):
        row = QHBoxLayout()
        for b in group.buttons():
            row.addWidget(b)
        row.addStretch()
        return row

    def _path_row(self, edit):
        row = QHBoxLayout()
        row.addWidget(edit)
        b = QPushButton("瀏覽…")
        b.clicked.connect(lambda: self._browse_dir(edit))
        row.addWidget(b)
        return row

    def _browse_dir(self, edit):
        d = QFileDialog.getExistingDirectory(self, "選擇資料夾", edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    def refresh_conns(self):
        self.conn_list.clear()
        self.conn_list.addItems([c["name"] for c in self.conns])

    def _save_conns(self):
        store.save_connections(self.conns)
        self.refresh_conns()

    def add_conn(self):
        d = ConnDialog(self)
        if d.exec():
            self.conns.append(d.value())
            self._save_conns()

    def edit_conn(self):
        i = self.conn_list.currentRow()
        if i < 0:
            return
        d = ConnDialog(self, self.conns[i])
        if d.exec():
            self.conns[i] = d.value()
            self._save_conns()

    def del_conn(self):
        i = self.conn_list.currentRow()
        if i >= 0 and QMessageBox.question(
                self, "刪除連線", f"確定刪除連線「{self.conns[i]['name']}」？") == QMessageBox.Yes:
            del self.conns[i]
            self._save_conns()

    def do_connect(self, *_):
        i = self.conn_list.currentRow()
        if i < 0:
            QMessageBox.information(self, "連線", "請先在連線清單選擇一個連線")
            return
        conn = self.conns[i]
        try:
            client, dbs = connect(conn["uri"])
        except Exception as e:
            QMessageBox.warning(self, "連線失敗", str(e))
            return
        if self.client:
            self.client.close()
        self.client, self.uri = client, conn["uri"]
        self.status.setText(f"已連線：{conn['name']}")
        for combo in (self.ex_db, self.im_db):
            current = combo.currentText()
            combo.clear()
            combo.addItems(dbs)
            combo.setCurrentText(current)
        self.log(f"已連線 {conn['name']}，{len(dbs)} 個資料庫")

    def choose_tools_dir(self):
        d = QFileDialog.getExistingDirectory(self, "選擇 mongo tools 的 bin 資料夾", self.tools_dir or "")
        if not d:
            return
        d = os.path.normpath(d)
        if tools.find_tools_dir(d) != d:
            QMessageBox.warning(self, "mongo tools", "此資料夾缺少 " + "、".join(t + ".exe" for t in tools.TOOLS))
            return
        self.tools_dir = self.settings["tools_dir"] = d
        store.save_settings(self.settings)
        self.log(f"mongo tools：{d}")

    def _ready(self):
        if not self.tools_dir:
            QMessageBox.warning(self, "mongo tools", "找不到 mongo tools，請從「設定 → mongo tools 路徑」指定")
        elif not self.client:
            QMessageBox.warning(self, "連線", "請先連線")
        else:
            return self.proc.state() == QProcess.NotRunning and not self.queue
        return False

    def log(self, text):
        self.log_view.appendPlainText(text)

    def run_jobs(self, jobs):
        if not jobs:
            return
        self.cfg = tools.write_config(self.uri)
        self.queue, self.failed, self.total = list(jobs), 0, len(jobs)
        self.tabs.setEnabled(False)
        self.left.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._next()

    def _next(self):
        if not self.queue:
            self._done()
            return
        exe, args = self.queue.pop(0)
        self.log(f"\n> {exe} {subprocess.list2cmdline(args)}")
        self.proc.start(os.path.join(self.tools_dir, exe + ".exe"), ["--config", self.cfg, *args])

    def _on_output(self):
        self.log(tools.mask_credentials(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").rstrip()))

    def _on_finished(self, code, status):
        if code != 0 or status != QProcess.NormalExit:
            self.failed += 1
            self.log(f"失敗（結束碼 {code}）")
        self._next()

    def _on_error(self, error):
        if error == QProcess.FailedToStart:
            self.failed += 1
            self.log(f"無法啟動：{self.proc.errorString()}")
            self._next()

    def cancel(self):
        self.queue.clear()
        self.log("使用者取消")
        self.proc.kill()

    def _done(self):
        if self.cfg:
            os.remove(self.cfg)
            self.cfg = None
        self.log(f"\n完成：{self.total - self.failed}/{self.total} 成功" + (f"，{self.failed} 失敗" if self.failed else ""))
        self.tabs.setEnabled(True)
        self.left.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    def closeEvent(self, event):
        if self.proc.state() != QProcess.NotRunning:
            self.cancel()
            self.proc.waitForFinished(3000)
        if self.cfg:
            os.remove(self.cfg)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    try:
        conns = store.load_connections()
    except Exception as e:
        # 不能當成空清單繼續，否則下次儲存會覆蓋掉原本的連線檔
        QMessageBox.critical(None, "Mongo Transfer", f"讀取連線清單失敗：{store.CONN_FILE}\n{e}")
        return 1
    w = MainWindow(conns)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

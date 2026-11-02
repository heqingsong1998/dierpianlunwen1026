"""手动选择会话、名义时间重建及联合标定的离线窗口。"""
from pathlib import Path

from PyQt5.QtCore import QEvent, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..algorithms.calibration import CALIBRATION_PATH
from ..offline.workflow import (
    ReconstructionOptions, apply_result, discover_sessions, run_analysis,
)


class AnalysisJob(QThread):
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, arguments, parent=None):
        super().__init__(parent)
        self.arguments = arguments

    def run(self):
        try:
            result = run_analysis(**self.arguments, progress=self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.result_ready.emit(result)


class OfflineWorkbench(QDialog):
    coefficients_applied = pyqtSignal()

    def __init__(self, parent=None, current_session=None, config_path=CALIBRATION_PATH,
                 reload_callback=None, idle_check=None):
        super().__init__(parent)
        self.setWindowTitle('离线分析 · 时间轴重建与总法向力标定')
        self.resize(1240, 940)
        self.setMinimumSize(950, 720)
        self.config_path = Path(config_path)
        self.reload_callback = reload_callback
        self.idle_check = idle_check or (lambda: None)
        self.result = None
        self.paths = []
        self._job = None
        self._image = None
        self._setup_ui()
        if current_session and Path(current_session).is_dir():
            self.add_directory(current_session)

    @property
    def busy(self):
        # 工作线程退出后，仍要等主线程接收结果并处理 finished 信号。
        return self._job is not None

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        description = QLabel(
            '直接读取每个会话的 nianfujiao_frames.csv 和 liuzhouli.csv。原始文件保留，结果另存。\n'
            '默认按六轴1 ms／点、足端16 ms／帧排列，两个文件各自首点假设同时；不按峰值移动曲线。')
        description.setWordWrap(True)
        layout.addWidget(description)
        file_buttons = QHBoxLayout()
        self.add_button = QPushButton('添加会话／父目录')
        self.add_button.clicked.connect(self._choose_directory)
        self.remove_button = QPushButton('移除选中行')
        self.remove_button.clicked.connect(self._remove_rows)
        self.clear_button = QPushButton('清空')
        self.clear_button.clicked.connect(self._clear)
        for button in (self.add_button, self.remove_button, self.clear_button):
            file_buttons.addWidget(button)
        file_buttons.addStretch()
        layout.addLayout(file_buttons)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['使用','采集会话','足端起点偏移 (ms)'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(1, 600)
        self.table.setColumnWidth(2, 180)
        self.table.setMinimumHeight(130)
        self.table.itemChanged.connect(self._invalidate)
        layout.addWidget(self.table, 2)
        parameters = QHBoxLayout()
        self.six_period = self._spin(.000001, 100000, 1)
        self.foot_period = self._spin(.000001, 100000, 16)
        self.active_force = self._spin(.000001, 10000, .5)
        for label, spin in [('六轴周期 (ms)',self.six_period),('足端周期 (ms)',self.foot_period),
                            ('受力误差分组 |Fz| > (N)',self.active_force)]:
            parameters.addWidget(QLabel(label)); parameters.addWidget(spin)
            spin.valueChanged.connect(self._invalidate)
        layout.addLayout(parameters)
        hint = QLabel('正偏移表示足端首帧比六轴首点晚；帧号缺口保留时间空位，不补造信号。'
                      '误差分组阈值不筛除训练点。')
        hint.setWordWrap(True); layout.addWidget(hint)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel('结果父目录'))
        self.output_edit = QLineEdit(str(Path(__file__).resolve().parents[2]/'data'/'offline_results'))
        self.output_button = QPushButton('选择')
        self.output_button.clicked.connect(self._choose_output)
        output_row.addWidget(self.output_edit,1); output_row.addWidget(self.output_button)
        layout.addLayout(output_row)
        actions = QHBoxLayout()
        self.reconstruct_button = QPushButton('重建时间轴并绘图')
        self.fit_button = QPushButton('一键联合标定并绘图')
        self.apply_button = QPushButton('应用本次系数到控制器')
        self.report_button = QPushButton('打开完整报告')
        self.folder_button = QPushButton('打开结果目录')
        self.apply_button.setEnabled(False); self.report_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.reconstruct_button.clicked.connect(lambda:self._start(False))
        self.fit_button.clicked.connect(lambda:self._start(True))
        self.apply_button.clicked.connect(self._apply)
        self.report_button.clicked.connect(self._open_report)
        self.folder_button.clicked.connect(self._open_result_directory)
        for button in (self.reconstruct_button,self.fit_button,self.apply_button,
                       self.report_button,self.folder_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel('请选择会话；完成标定不会自动替换当前系数。')
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True); self.summary.setMaximumHeight(155)
        layout.addWidget(self.summary)
        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel('图像预览'))
        self.preview_combo = QComboBox()
        self.preview_combo.currentIndexChanged.connect(self._show_plot)
        preview_row.addWidget(self.preview_combo,1); layout.addLayout(preview_row)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_label = QLabel('重建后显示六轴原值连续曲线和足端联合总力散点。')
        self.preview_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.preview_scroll.setWidget(self.preview_label)
        self.preview_scroll.viewport().installEventFilter(self)
        layout.addWidget(self.preview_scroll,5)

    @staticmethod
    def _spin(lo, hi, value):
        spin = QDoubleSpinBox()
        spin.setDecimals(6); spin.setRange(lo, hi); spin.setValue(value)
        return spin

    def add_directory(self, directory):
        sessions = discover_sessions(directory)
        if not sessions:
            self.status.setText('未找到同时包含 frames.csv 和 liuzhouli.csv 的会话')
            return
        for path in sessions:
            if path in self.paths:
                continue
            row = self.table.rowCount()
            self.paths.append(path); self.table.insertRow(row)
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked); self.table.setItem(row,0,item)
            label = QTableWidgetItem(path.name)
            label.setToolTip(str(path)); label.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row,1,label)
            offset = self._spin(-1e8,1e8,0); offset.setDecimals(3)
            offset.valueChanged.connect(self._invalidate); self.table.setCellWidget(row,2,offset)
        self._invalidate()
        self.status.setText(f'已添加{len(self.paths)}个会话；请勾选本次使用的数据。')

    def _choose_directory(self):
        directory = QFileDialog.getExistingDirectory(self,'选择会话或会话父目录')
        if directory:
            try:
                self.add_directory(directory)
            except Exception as exc:
                self.status.setText(str(exc))

    def _remove_rows(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()},reverse=True)
        for row in rows:
            self.table.removeRow(row); self.paths.pop(row)
        self._invalidate()

    def _clear(self):
        self.table.setRowCount(0); self.paths.clear(); self._invalidate()

    def _choose_output(self):
        directory = QFileDialog.getExistingDirectory(self,'选择结果父目录',self.output_edit.text())
        if directory:
            self.output_edit.setText(directory)

    def _invalidate(self, *_):
        if self.result is not None:
            self.result = None
            self.summary.clear()
            self.preview_combo.clear()
            self._image = None
            self.preview_label.setText('输入或参数已改变，请重新重建后查看图像。')
            self.status.setText('输入或参数已改变，请重新重建／标定。')
        self.apply_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self.folder_button.setEnabled(False)

    def _start(self, fit):
        if self.busy:
            return
        reason = self.idle_check()
        if reason:
            self.status.setText(reason); return
        selected = [p for i,p in enumerate(self.paths) if self.table.item(i,0).checkState()==Qt.Checked]
        if not selected or not self.output_edit.text().strip():
            self.status.setText('请勾选至少一个会话并选择结果父目录'); return
        options = ReconstructionOptions(self.six_period.value(),self.foot_period.value(),
                                        self.active_force.value())
        offsets = {str(p):self.table.cellWidget(i,2).value() for i,p in enumerate(self.paths)}
        self._invalidate()
        self.summary.clear()
        arguments = dict(paths=selected, output_root=self.output_edit.text().strip(),
                         options=options, offsets=offsets, fit=fit, config_path=self.config_path)
        self._job = AnalysisJob(arguments,self)
        self._job.progress.connect(self.status.setText)
        self._job.result_ready.connect(self._completed)
        self._job.failed.connect(lambda message:self.status.setText('分析失败：'+message))
        self._job.finished.connect(self._job_finished)
        self._set_busy(True)
        self.status.setText('正在分析；原始文件只读……')
        self._job.start()

    def _set_busy(self, busy):
        for widget in (self.table,self.add_button,self.remove_button,self.clear_button,
                       self.six_period,self.foot_period,self.active_force,self.output_edit,
                       self.output_button,self.reconstruct_button,self.fit_button):
            widget.setEnabled(not busy)
        self.apply_button.setEnabled(not busy and bool(self.result and self.result['fit']))
        self.report_button.setEnabled(not busy and self.result is not None)
        self.folder_button.setEnabled(not busy and self.result is not None)

    def _job_finished(self):
        self._job.deleteLater(); self._job = None
        self._set_busy(False)

    def _completed(self, result):
        self.result = result
        lines = [result['formula'], '上式压缩为负；控制总力取反（压缩为正）。']
        fitted = result['fit']
        if fitted:
            for key,label in [('training_all','训练全部点'),('training_active','训练受力点'),
                              ('leave_one_session_out_all','整次留出全部点'),
                              ('leave_one_session_out_active','整次留出受力点')]:
                m = fitted[key]
                lines.append(f'{label}：n={m["n"]}，MAE={m["mae_n"]:.4f} N，RMSE={m["rmse_n"]:.4f} N'
                             if m else label+'：不可用')
            if not fitted['validation_complete']:
                lines.append('注意：完整逐实验留出不可用，详见报告。')
        else:
            lines.append('本次仅重建，沿用当前联合模型系数，未重新拟合。')
        for session in result['sessions']:
            lines.extend(session['session']+'：'+w for w in session['warnings'])
        self.summary.setPlainText('\n'.join(lines))
        self.preview_combo.blockSignals(True); self.preview_combo.clear()
        for session in result['sessions']:
            self.preview_combo.addItem(session['session'],session['plot_png'])
        self.preview_combo.blockSignals(False); self._show_plot()
        self.status.setText('完成：'+result['output_dir']+'。当前系数未自动改变。')

    def _show_plot(self, *_):
        path = self.preview_combo.currentData()
        if path:
            self._image = QPixmap(path); self._scale_image()
            QTimer.singleShot(0, self._scale_image)

    def _scale_image(self):
        if self._image is not None and not self._image.isNull():
            width = max(400,self.preview_scroll.viewport().width()-16)
            self.preview_label.setPixmap(self._image.scaledToWidth(width,Qt.SmoothTransformation))

    def eventFilter(self, watched, event):
        if watched is self.preview_scroll.viewport() and event.type() == QEvent.Resize:
            # 等待布局及滚动条尺寸更新，首次显示和缩放窗口均使用实际可视宽度。
            QTimer.singleShot(0, self._scale_image)
        return super().eventFilter(watched, event)

    def _apply(self):
        if not self.result or not self.result['fit'] or self.busy:
            return
        reason = self.idle_check()
        if reason:
            self.status.setText(reason); return
        try:
            backup = apply_result(self.result,self.config_path,self.reload_callback)
        except Exception as exc:
            self.status.setText('应用失败，配置保持原值：'+str(exc)); return
        self.status.setText('系数已应用，控制器保持停止；请检查总阈值并重新布防。原配置备份：'+str(backup))
        self.coefficients_applied.emit()

    def _open_report(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result['report_html']))

    def _open_result_directory(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result['output_dir']))

    def closeEvent(self, event):
        if self.busy:
            self.status.setText('正在导出结果，请等待完成后关闭。')
            event.ignore()
        else:
            event.accept()

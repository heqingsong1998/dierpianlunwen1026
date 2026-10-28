"""只读的四路接近觉 2×2 三维显示。不发送命令，不修改采集或标定数据。"""
from pathlib import Path
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QVBoxLayout, QWidget,
)
from matplotlib import font_manager
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from .proximity_data import (
    CHANNELS, DEFAULT_POSITIONS, LAYOUT_PATH, POSITION_NAMES, ProximitySample,
    load_positions, save_positions, signal_differences,
)


COLORS = dict(zip(CHANNELS, ('#2878bf', '#139e93', '#ed9c24', '#8c5bc2')))


def _chinese_plot_font():
    fonts = {item.name: item.fname for item in font_manager.fontManager.ttflist}
    for name in ('Microsoft YaHei', 'SimHei', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei',
                 'Arial Unicode MS', 'SimSun'):
        if name in fonts:
            return font_manager.FontProperties(fname=fonts[name])
    return font_manager.FontProperties(family='DejaVu Sans')


class ProximityViewDialog(QDialog):
    REFRESH_MS = 200  # 5 Hz 只刷新显示，独立于真实采集和 CSV 写入节拍。
    STALE_NS = 1_000_000_000

    def __init__(self, parent=None, layout_path=LAYOUT_PATH):
        super().__init__(parent)
        self.setWindowTitle('接近觉 · 2×2 三维柱状图')
        self.setModal(False)
        self.resize(1120, 750)
        self.setMinimumSize(880, 650)
        self._layout_path = Path(layout_path)
        self._layout_error = ''
        try:
            self.positions = load_positions(self._layout_path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.positions = DEFAULT_POSITIONS
            self._layout_error = f'位置配置未载入，暂用默认布局：{exc}'
        self._connected = False
        self._sample = None
        self._input_error = ''
        self._dirty = True
        self._bars = None
        self._bar_labels = []
        self._z_limits = None
        self.last_displayed_values = None
        self._font = _chinese_plot_font()
        self._setup_ui()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(self.REFRESH_MS)
        self.refresh_timer.timeout.connect(self.refresh_view)
        self.refresh_view()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel('四路接近觉 · 姿态调整参考')
        title.setStyleSheet('font-size: 18px; font-weight: 600; color: #183650;')
        header.addWidget(title)
        header.addStretch()
        self.status_label = QLabel('未连接黏附脚')
        self.status_label.setStyleSheet('color: #687582;')
        header.addWidget(self.status_label)
        layout.addLayout(header)
        description = QLabel('柱高为清零后的接近觉相对信号，未换算为距离或角度。')
        description.setStyleSheet('color: #617184;')
        layout.addWidget(description)

        content = QHBoxLayout()
        self.figure = Figure(figsize=(7, 5), dpi=100, facecolor='#f8fafc')
        self.figure.subplots_adjust(left=0.01, right=0.89, bottom=0.04, top=0.98)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumSize(480, 400)
        self.canvas.setToolTip('左键拖动旋转，右键拖动缩放。四角位置以右侧俯视布局为准。')
        self.axes = self.figure.add_subplot(111, projection='3d')
        self.axes.set_facecolor('#f8fafc')
        self.axes.set_xlim(-0.6, 1.6)
        self.axes.set_ylim(-0.6, 1.6)
        self.axes.set_zlim(-1, 1)
        self.axes.set_xticks([0, 1], ['左', '右'], fontproperties=self._font)
        self.axes.set_yticks([0, 1], ['下', '上'], fontproperties=self._font)
        self.axes.set_zlabel('接近觉相对信号', fontproperties=self._font, labelpad=10)
        self.axes.ticklabel_format(axis='z', style='plain', useOffset=False)
        self.axes.set_box_aspect((1, 1, 0.9))
        self.axes.view_init(elev=26, azim=-58)
        self.axes.plot([-0.45, 1.45, 1.45, -0.45, -0.45],
                       [-0.45, -0.45, 1.45, 1.45, -0.45], [0] * 5,
                       color='#8296a9', linewidth=0.9)
        content.addWidget(self.canvas, 1)

        controls = QWidget()
        controls.setFixedWidth(300)
        side = QVBoxLayout(controls)
        side.setContentsMargins(0, 0, 0, 0)
        group = QGroupBox('俯视位置与通道数值')
        grid = QGridLayout(group)
        self.position_boxes = []
        self.value_labels = []
        for index, name in enumerate(POSITION_NAMES):
            card = QGroupBox(name)
            card_layout = QVBoxLayout(card)
            box = QComboBox()
            box.addItems(CHANNELS)
            box.setCurrentText(self.positions[index])
            box.setToolTip('选择已占用的通道时，会与该位置自动交换。')
            box.currentIndexChanged.connect(lambda _, position=index: self._change_position(position))
            value = QLabel('--')
            value.setAlignment(Qt.AlignCenter)
            value.setStyleSheet('font-size: 20px; font-weight: 600; color: #52677b;')
            card_layout.addWidget(box)
            card_layout.addWidget(value)
            grid.addWidget(card, index // 2, index % 2)
            self.position_boxes.append(box)
            self.value_labels.append(value)
        side.addWidget(group)
        position_buttons = QHBoxLayout()
        self.save_layout_btn = QPushButton('保存位置')
        self.save_layout_btn.clicked.connect(self._save_layout)
        position_buttons.addWidget(self.save_layout_btn)
        self.default_layout_btn = QPushButton('恢复默认位置')
        self.default_layout_btn.clicked.connect(self._restore_layout)
        position_buttons.addWidget(self.default_layout_btn)
        side.addLayout(position_buttons)
        self.layout_status_label = QLabel(self._layout_error or '默认：左列第一组，右列第二组。')
        self.layout_status_label.setWordWrap(True)
        self.layout_status_label.setStyleSheet('color: #617184;')
        side.addWidget(self.layout_status_label)

        differences = QGroupBox('信号差（仅作调整参考）')
        difference_layout = QVBoxLayout(differences)
        self.difference_label = QLabel('右侧均值 − 左侧均值：--\n上侧均值 − 下侧均值：--\n四路极差：--')
        self.difference_label.setWordWrap(True)
        difference_layout.addWidget(self.difference_label)
        side.addWidget(differences)
        self.pause_checkbox = QCheckBox('暂停显示（采集继续）')
        self.pause_checkbox.toggled.connect(self._pause_changed)
        side.addWidget(self.pause_checkbox)
        self.lock_z_checkbox = QCheckBox('锁定纵轴范围')
        self.lock_z_checkbox.toggled.connect(self._scale_changed)
        side.addWidget(self.lock_z_checkbox)
        self.reset_view_btn = QPushButton('恢复视角 / 自适应纵轴')
        self.reset_view_btn.clicked.connect(self._reset_view)
        side.addWidget(self.reset_view_btn)
        self.source_label = QLabel('等待真实黏附脚数据')
        self.source_label.setWordWrap(True)
        self.source_label.setStyleSheet('color: #617184;')
        side.addWidget(self.source_label)
        side.addStretch()
        content.addWidget(controls)
        layout.addLayout(content, 1)
        note = QLabel('四路等高仅能辅助比较；安装、灵敏度和零点差异需另行标定。绕法向的偏航需结合机械基准判断。')
        note.setWordWrap(True)
        note.setStyleSheet('color: #617184; padding: 6px 0;')
        layout.addWidget(note)

    def set_connected(self, connected):
        if self._connected == bool(connected):
            return
        self._connected = bool(connected)
        self._sample = None
        self._input_error = ''
        self._dirty = True
        self.refresh_view()

    def update_sample(self, frame):
        try:
            sample = ProximitySample.from_frame(frame)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
            self._sample = None
            self._input_error = str(exc)
            self._dirty = True
            return
        if sample != self._sample:
            self._sample = sample
            self._input_error = ''
            self._dirty = True

    def _change_position(self, position):
        positions = list(self.positions)
        selected = self.position_boxes[position].currentText()
        other = positions.index(selected)
        positions[position], positions[other] = positions[other], positions[position]
        self.positions = tuple(positions)
        self._sync_position_boxes()
        self.layout_status_label.setText('位置已应用；点击“保存位置”可供下次使用。')
        self._dirty = True
        self.refresh_view()

    def _sync_position_boxes(self):
        for box, channel in zip(self.position_boxes, self.positions):
            blocked = box.blockSignals(True)
            box.setCurrentText(channel)
            box.blockSignals(blocked)

    def _save_layout(self):
        try:
            save_positions(self.positions, self._layout_path)
            self.layout_status_label.setText('四角位置已保存。')
        except OSError as exc:
            self.layout_status_label.setText(f'位置已应用，但保存失败：{exc}')

    def _restore_layout(self):
        self.positions = DEFAULT_POSITIONS
        self._sync_position_boxes()
        self._dirty = True
        self.layout_status_label.setText('已恢复默认位置；点击“保存位置”可写入配置。')
        self.refresh_view()

    def _pause_changed(self, paused):
        # 冻结时保持四角名称与旧数值一致，避免换位后误读暂停画面。
        for box in self.position_boxes:
            box.setEnabled(not paused)
        self.default_layout_btn.setEnabled(not paused)
        if not paused:
            self._dirty = True
        self.refresh_view()

    def _scale_changed(self):
        self._dirty = True
        self.refresh_view()

    def _reset_view(self):
        self.axes.view_init(elev=26, azim=-58)
        self.lock_z_checkbox.setChecked(False)
        self._z_limits = None
        self._dirty = True
        self.refresh_view()
        self.canvas.draw_idle()

    def refresh_view(self):
        if not self._connected:
            state, color = '未连接黏附脚', '#687582'
        elif self._input_error:
            state, color = '数据无效', '#b94d36'
        elif self._sample is None:
            state, color = '已连接，等待接近觉数据', '#996c18'
        else:
            age_ns = time.perf_counter_ns() - self._sample.host_received_ns
            if self._sample.host_received_ns <= 0 or age_ns < 0:
                state, color = '接收时间未知', '#996c18'
            elif age_ns > self.STALE_NS:
                state, color = '数据超时：当前数值已停止更新', '#b94d36'
            elif self.pause_checkbox.isChecked():
                state, color = '显示已暂停，采集继续', '#996c18'
            else:
                state, color = '实时显示 · 5 Hz', '#16704a'
        self.status_label.setText(state)
        self.status_label.setStyleSheet(f'color: {color}; font-weight: 600;')
        if self._input_error:
            self.source_label.setText(self._input_error)
        if not self._connected or self._sample is None:
            if self._dirty:
                self._clear_display()
                self._dirty = False
            return
        if self.pause_checkbox.isChecked():
            return
        if self._dirty:
            self._draw_sample(self._sample)
            self._dirty = False

    def _clear_display(self):
        self._remove_bars()
        self.last_displayed_values = None
        for value in self.value_labels:
            value.setText('--')
        self.difference_label.setText('右侧均值 − 左侧均值：--\n上侧均值 − 下侧均值：--\n四路极差：--')
        if not self._input_error:
            self.source_label.setText('等待真实黏附脚数据')
        self.canvas.draw_idle()

    def _remove_bars(self):
        if self._bars is not None:
            self._bars.remove()
            self._bars = None
        for label in self._bar_labels:
            label.remove()
        self._bar_labels.clear()

    def _draw_sample(self, sample):
        values = sample.at_positions(self.positions)
        self._remove_bars()
        low, high = min(0, min(values)), max(0, max(values))
        span = max(high - low, 1.0)
        if not self.lock_z_checkbox.isChecked() or self._z_limits is None:
            self._z_limits = (low - span * 0.12, high + span * 0.24)
        self.axes.set_zlim(*self._z_limits)
        x, y = (0, 1, 0, 1), (1, 1, 0, 0)
        colors = [COLORS[channel] for channel in self.positions]
        # 负信号从其负值位置长到零平面，保留原始符号，不取绝对值充当正信号。
        bottoms = [min(0, value) for value in values]
        heights = [abs(value) for value in values]
        self._bars = self.axes.bar3d([value - 0.26 for value in x],
                                     [value - 0.26 for value in y], bottoms,
                                     0.52, 0.52, heights, color=colors, shade=True,
                                     edgecolor='white', linewidth=0.6)
        padding = (self._z_limits[1] - self._z_limits[0]) * 0.025
        for index, (channel, value, color) in enumerate(zip(self.positions, values, colors)):
            self.value_labels[index].setText(f'{value:+.0f}')
            self.value_labels[index].setStyleSheet(f'font-size: 20px; font-weight: 600; color: {color};')
            label = self.axes.text(x[index], y[index], value + (padding if value >= 0 else -padding),
                                   f'{channel}\n{value:+.0f}', ha='center',
                                   va='bottom' if value >= 0 else 'top', fontsize=9, color='#263e52')
            self._bar_labels.append(label)
        difference = signal_differences(values)
        self.difference_label.setText(
            f"右侧均值 − 左侧均值：{difference['right_minus_left']:+.1f}\n"
            f"上侧均值 − 下侧均值：{difference['upper_minus_lower']:+.1f}\n"
            f"四路极差：{difference['range']:.1f}")
        self.source_label.setText(f'当前显示帧：{sample.sample_sequence or "未提供编号"}\n'
                                  '高度与数值保留信号正负；可拖动图像旋转视角。')
        self.last_displayed_values = values
        self.canvas.draw_idle()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_timer.start()
        self.refresh_view()

    def hideEvent(self, event):
        self.refresh_timer.stop()
        super().hideEvent(event)

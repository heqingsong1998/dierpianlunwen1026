"""
控制面板 - 传感器控制操作
"""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                             QPushButton, QLabel, QFormLayout, QDoubleSpinBox, QComboBox, QSizePolicy)
from PyQt5.QtCore import pyqtSignal, Qt


class ControlPanel(QWidget):
    """传感器控制面板"""

    # 粘附脚 信号
    nianfujiao_reply = pyqtSignal()
    nianfujiao_engage = pyqtSignal()
    nianfujiao_zero = pyqtSignal()
    
    # 六轴力 信号
    liuzhouli_zero = pyqtSignal()
    controller_apply = pyqtSignal(dict)
    controller_start = pyqtSignal(dict)
    controller_stop = pyqtSignal()
    controller_reload_calibration = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 面板标题
        title_label = QLabel("<h3>传感器控制</h3>")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # 粘附脚控制组
        nianfujiao_group = QGroupBox("粘附脚传感器")
        nianfujiao_layout = QVBoxLayout()
        command_buttons = QHBoxLayout()
        
        self.nianfujiao_reply_btn = QPushButton("回复")
        self.nianfujiao_reply_btn.setToolTip("命令下位机拉低 PA4")
        self.nianfujiao_reply_btn.clicked.connect(self.nianfujiao_reply.emit)
        command_buttons.addWidget(self.nianfujiao_reply_btn)
        
        self.nianfujiao_engage_btn = QPushButton("吸合")
        self.nianfujiao_engage_btn.setToolTip("命令下位机拉高 PA4")
        self.nianfujiao_engage_btn.clicked.connect(self.nianfujiao_engage.emit)
        command_buttons.addWidget(self.nianfujiao_engage_btn)
        
        self.nianfujiao_zero_btn = QPushButton("清零")
        self.nianfujiao_zero_btn.setToolTip("更新下位机零点基准，保持 PA4 当前电平")
        self.nianfujiao_zero_btn.clicked.connect(self.nianfujiao_zero.emit)
        command_buttons.addWidget(self.nianfujiao_zero_btn)
        nianfujiao_layout.addLayout(command_buttons)
        
        nianfujiao_group.setLayout(nianfujiao_layout)
        layout.addWidget(nianfujiao_group)
        
        # 六轴力控制组
        liuzhouli_group = QGroupBox("六轴力传感器")
        liuzhouli_layout = QHBoxLayout()
        
        self.liuzhouli_zero_btn = QPushButton("清零")
        self.liuzhouli_zero_btn.clicked.connect(self.liuzhouli_zero.emit)
        liuzhouli_layout.addWidget(self.liuzhouli_zero_btn)
        
        liuzhouli_group.setLayout(liuzhouli_layout)
        layout.addWidget(liuzhouli_group)

        controller_group = QGroupBox("上位机控制器：总法向力 → 单阈值")
        controller_layout = QVBoxLayout(controller_group)
        form = QFormLayout()
        self.threshold_total = QDoubleSpinBox()
        self.threshold_total.setDecimals(3)
        self.threshold_total.setRange(0, 10000)
        self.threshold_total.setSingleStep(0.1)
        self.threshold_total.setSuffix(' N')
        self.threshold_total.setValue(3.0)
        self.threshold_total.setToolTip('联合模型总法向力；压缩为正，拉伸为负，不取绝对值')
        form.addRow('总法向力 ≥', self.threshold_total)
        self.trigger_action = QComboBox()
        self.trigger_action.addItem('低电平（LOW）', 0)
        self.trigger_action.addItem('高电平（HIGH）', 1)
        self.trigger_action.setCurrentIndex(1)
        self.trigger_action.setToolTip('按电磁铁实际驱动电路选择触发电平；此项不改变当前引脚')
        form.addRow('触发 PA4 电平', self.trigger_action)
        controller_layout.addLayout(form)
        rule = QLabel('总法向力达标触发一次（压缩为正）；未达标或触发后保持输出。\n'
                      '当前采用两路联合试拟合模型，参数待独立验证。')
        rule.setWordWrap(True)
        controller_layout.addWidget(rule)
        self.controller_applied_label = QLabel()
        self.controller_applied_label.setWordWrap(True)
        controller_layout.addWidget(self.controller_applied_label)
        parameter_buttons = QHBoxLayout()
        self.controller_apply_btn = QPushButton('应用并保存参数')
        self.controller_apply_btn.clicked.connect(
            lambda: self.controller_apply.emit(self.controller_settings()))
        parameter_buttons.addWidget(self.controller_apply_btn)
        self.controller_reload_btn = QPushButton('重载标定')
        self.controller_reload_btn.setToolTip('读取 controller/config/calibration.json，停止控制器，等待重新布防')
        self.controller_reload_btn.clicked.connect(self.controller_reload_calibration.emit)
        parameter_buttons.addWidget(self.controller_reload_btn)
        controller_layout.addLayout(parameter_buttons)
        run_buttons = QHBoxLayout()
        self.controller_start_btn = QPushButton('启动 / 重新布防')
        self.controller_start_btn.clicked.connect(
            lambda: self.controller_start.emit(self.controller_settings()))
        run_buttons.addWidget(self.controller_start_btn)
        self.controller_stop_btn = QPushButton('停止控制器')
        self.controller_stop_btn.clicked.connect(self.controller_stop.emit)
        run_buttons.addWidget(self.controller_stop_btn)
        controller_layout.addLayout(run_buttons)
        self.controller_status_label = QLabel('未启动')
        self.controller_status_label.setWordWrap(True)
        controller_layout.addWidget(self.controller_status_label)
        self.controller_values_label = QLabel('控制输入总法向力（压缩为正）：-- N')
        self.controller_values_label.setWordWrap(True)
        self.controller_values_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        controller_layout.addWidget(self.controller_values_label)
        layout.addWidget(controller_group)
        
        # 添加弹性空间
        layout.addStretch()
        
        self.setLayout(layout)
        
        # 初始禁用所有按钮
        self._set_all_buttons_enabled(False)
    
    def _set_all_buttons_enabled(self, enabled: bool):
        """设置所有按钮启用/禁用"""
        # 粘附脚
        self.nianfujiao_reply_btn.setEnabled(enabled)
        self.nianfujiao_engage_btn.setEnabled(enabled)
        self.nianfujiao_zero_btn.setEnabled(enabled)
        
        # 六轴力
        self.liuzhouli_zero_btn.setEnabled(enabled)
        self.controller_start_btn.setEnabled(enabled)
        self.controller_stop_btn.setEnabled(False)
    
    def update_button_states(self, connection_status: dict):
        """
        根据连接状态更新按钮
        
        Args:
            connection_status: 连接状态字典
        """
        # 粘附脚
        nianfujiao_connected = connection_status.get('nianfujiao', False)
        self.nianfujiao_reply_btn.setEnabled(nianfujiao_connected)
        self.nianfujiao_engage_btn.setEnabled(nianfujiao_connected)
        self.nianfujiao_zero_btn.setEnabled(nianfujiao_connected)
        self.controller_start_btn.setEnabled(nianfujiao_connected)
        
        # 六轴力
        liuzhouli_connected = connection_status.get('liuzhouli', False)
        self.liuzhouli_zero_btn.setEnabled(liuzhouli_connected)

    def controller_settings(self):
        return {'threshold_total_n': self.threshold_total.value(),
                'trigger_action': self.trigger_action.currentData()}

    def set_controller_settings(self, settings):
        self.threshold_total.setValue(settings['threshold_total_n'])
        self.trigger_action.setCurrentIndex(self.trigger_action.findData(settings['trigger_action']))

    def update_controller_status(self, status):
        settings = status['settings']
        self.controller_applied_label.setText(
            f"已应用：总法向力 ≥ {settings['threshold_total_n']:g} N（压缩为正），"
            f"PA4 {'HIGH' if settings['trigger_action'] else 'LOW'}")
        self.controller_stop_btn.setEnabled(status['active'])
        self.controller_status_label.setText(status['message'])
        self.controller_status_label.setStyleSheet(
            'color: #b22222;' if status['state'] == 'error' else
            'color: #176b35;' if status['active'] else 'color: #555;')
        if 'fz_total_n' in status:
            self.controller_values_label.setText(
                f"控制输入总法向力：{status['fz_total_n']:.4f} N（压缩为正）\n"
                f"六轴符号参考：{status['fz_total_reference_n']:.4f} N\n"
                f"帧 {status['sample_sequence']}；处理 {status['controller_us']:.1f} μs；"
                f"丢弃输入 {status['dropped_inputs']}")
        else:
            self.controller_values_label.setText('控制输入总法向力（压缩为正）：-- N')

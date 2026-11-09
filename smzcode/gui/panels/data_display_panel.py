"""
数据显示面板 - 显示黏附脚、六轴力及超声波传感器的实时数据
"""
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                             QLabel, QGridLayout, QTableWidget,
                             QTableWidgetItem, QHeaderView)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


class LiuZhouLiDisplayWidget(QWidget):
    """六轴力传感器数据显示组件"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 标题
        title = QLabel("<b>六轴力传感器</b>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # 数据网格
        grid = QGridLayout()
        grid.setSpacing(10)
        
        font = QFont("Courier New", 11)
        
        # �?(N)
        force_label = QLabel("<b>力 (N):</b>")
        grid.addWidget(force_label, 0, 0)
        
        self.fx_label = QLabel("Fx: +0.0000")
        self.fx_label.setFont(font)
        grid.addWidget(self.fx_label, 1, 0)
        
        self.fy_label = QLabel("Fy: +0.0000")
        self.fy_label.setFont(font)
        grid.addWidget(self.fy_label, 2, 0)
        
        self.fz_label = QLabel("Fz: +0.0000")
        self.fz_label.setFont(font)
        grid.addWidget(self.fz_label, 3, 0)
        
        # 力矩 (N·m)
        moment_label = QLabel("<b>力矩 (N·m):</b>")
        grid.addWidget(moment_label, 0, 1)
        
        self.mx_label = QLabel("Mx: +0.000000")
        self.mx_label.setFont(font)
        grid.addWidget(self.mx_label, 1, 1)
        
        self.my_label = QLabel("My: +0.000000")
        self.my_label.setFont(font)
        grid.addWidget(self.my_label, 2, 1)
        
        self.mz_label = QLabel("Mz: +0.000000")
        self.mz_label.setFont(font)
        grid.addWidget(self.mz_label, 3, 1)
        
        layout.addLayout(grid)
        self.setLayout(layout)
        
        # 样式
        self.setStyleSheet("""
            LiuZhouLiDisplayWidget {
                border: 1px solid #ccc;
                border-radius: 5px;
                background-color: #fff8dc;
            }
        """)
    
    def update_data(self, data: dict):
        """
        更新数据显示
        
        Args:
            data: 数据字典 {'Fx': float, 'Fy': float, ...}
        """
        try:
            # 安全获取浮点数值（防御性编程）
            def safe_float(value, default=0.0):
                """安全转换为浮点数"""
                if isinstance(value, (list, tuple)):
                    return float(value[0]) if len(value) > 0 else default
                return float(value)
            
            fx = safe_float(data.get('Fx', 0.0))
            fy = safe_float(data.get('Fy', 0.0))
            fz = safe_float(data.get('Fz', 0.0))
            mx = safe_float(data.get('Mx', 0.0))
            my = safe_float(data.get('My', 0.0))
            mz = safe_float(data.get('Mz', 0.0))
            
            self.fx_label.setText(f"Fx: {fx:+.4f}")
            self.fy_label.setText(f"Fy: {fy:+.4f}")
            self.fz_label.setText(f"Fz: {fz:+.4f}")
            self.mx_label.setText(f"Mx: {mx:+.6f}")
            self.my_label.setText(f"My: {my:+.6f}")
            self.mz_label.setText(f"Mz: {mz:+.6f}")
            
        except Exception as e:
            print(f"[ERROR] 更新六轴力显示失败: {e}")
            print(f"[DEBUG] 数据类型: {type(data)}")
            print(f"[DEBUG] 数据内容: {data}")


class NianFuJiaoDisplayWidget(QWidget):
    """粘附脚传感器 Style0 数据显示组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)

        title = QLabel("<b>粘附脚传感器</b>")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        headers = [
            'Fx1', 'Fy1', 'Fz1+', 'Mx1', 'My1', 'App11', 'App12',
            'Fx2', 'Fy2', 'Fz2+', 'Mx2', 'My2', 'App21', 'App22',
            'FZ1-', 'FZ2-', '停用标志1', '停用标志2', '输出状态', 'PA4'
        ]
        self.style0_table = self._create_table(len(headers), headers)
        layout.addWidget(self.style0_table)
        self.frame_info_label = QLabel('等待黏附脚数据；下位机清零，上位机换算力值')
        self.total_force_label = QLabel('联合总法向力（压缩为正）：-- N')
        self.pair_info_label = QLabel('等待 stage 对和与基准版本')
        self.feedback_label = QLabel('等待命令反馈')
        for label in (self.total_force_label, self.frame_info_label,
                      self.pair_info_label, self.feedback_label):
            label.setWordWrap(True)
            layout.addWidget(label)
        self.setLayout(layout)

    def _create_table(self, col_count: int, headers: list) -> QTableWidget:
        table = QTableWidget(2, col_count)
        table.setHorizontalHeaderLabels(headers)
        table.setVerticalHeaderLabels(['原始值', '标定值'])
        # Keep calibrated numbers readable; scroll horizontally when all
        # channels do not fit, rather than truncating every numeric cell.
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setMinimumSectionSize(70)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)

        for row in range(2):
            for col in range(col_count):
                    table.setItem(row, col, QTableWidgetItem("0"))
        return table

    def update_data(self, style: int, data: dict):
        if style != 0:
            return
        self._update_table(self.style0_table, data)
        if data.get('fz_total_cal') is not None:
            self.total_force_label.setText(
                f"联合总法向力：{data['fz_total_cal']:.4f} N（压缩为正）"
                f"  |  六轴符号参考：{data['fz_total_reference_n']:.4f} N\n"
                f"模型：{data['normal_force_model_id']}")
        else:
            self.total_force_label.setText('联合总法向力（压缩为正）：-- N')
        sequence = data.get('sample_sequence', 0)
        self.frame_info_label.setText(
            f"采样帧: {sequence or '旧版/未提供'}  |  MCU 数据准备: {data.get('ready_us', 0)} µs"
            f"  |  PA4: {data.get('pin_state', '--')}")

    def update_protocol(self, data: dict):
        pairs = data.get('stage_pairs')
        if pairs:
            values = ', '.join(str(value) for value in pairs.get('pairs', []))
            self.pair_info_label.setText(
                f"帧 {pairs['sample_sequence']}  |  基准版本: {pairs['baseline_generation']}"
                f"  |  stage 对和: [{values}]"
                f"  |  丢命令/发送失败: {pairs['dropped_commands']}/{pairs['dropped_tx_frames']}")
        ack = data.get('feedback')
        if ack:
            status = {0: '已接受', 1: '拒绝：关联帧已失效', 2: '拒绝：清零基准已变化'}.get(
                ack['status'], f"设备状态 {ack['status']}")
            text = f"命令 {ack['command_id']}: {status}  |  PA4: {ack['pin_state']}"
            if ack.get('zero_pending'):
                text += '  |  清零已请求，等待基准版本更新'
            if ack.get('timing_valid'):
                text += (f"  |  传输合计: {ack['transport_sum_us']:.1f} µs"
                         f"  |  控制器: {ack['controller_us']:.1f} µs")
            elif ack.get('timing_error') in ('unbound_command', 'legacy_command'):
                text += '  |  手动指令，无控制器输入关联，不计算处理/传输时延'
            else:
                text += '  |  测时关联无效，原因已记录到反馈文件'
            self.feedback_label.setText(text)

    def _update_table(self, table: QTableWidget, data: dict):
        """更新表格数据（style0 包含原始值和标定值）"""
        raw_values = data.get('raw', [])
        calibrated_values = data.get('calibrated', [])

        col_count = table.columnCount()
        for col in range(min(col_count, len(raw_values))):
            item = table.item(0, col)
            if item:
                item.setText(f"{raw_values[col]}")
        for col in range(min(col_count, len(calibrated_values))):
            item = table.item(1, col)
            if item:
                item.setText(f"{calibrated_values[col]:.4f}")
class DataDisplayPanel(QWidget):
    """数据显示面板"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout()
        
        # 面板标题
        title_label = QLabel("<h3>传感器数据显示</h3>")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        ultrasonic = QGroupBox("超声波测距 / 测速")
        ultrasonic_layout = QHBoxLayout(ultrasonic)
        self.ultrasonic_distance = QLabel()
        self.ultrasonic_speed = QLabel()
        self.ultrasonic_status = QLabel()
        for label in (self.ultrasonic_distance, self.ultrasonic_speed, self.ultrasonic_status):
            ultrasonic_layout.addWidget(label)
        self.update_ultrasonic_data(status="未连接")
        layout.addWidget(ultrasonic)

        # 六轴力传感器
        self.liuzhouli_widget = LiuZhouLiDisplayWidget()
        layout.addWidget(self.liuzhouli_widget)
        
        # 粘附脚传感器
        self.nianfujiao_widget = NianFuJiaoDisplayWidget()
        layout.addWidget(self.nianfujiao_widget)
        
        self.setLayout(layout)
    
    def update_liuzhouli_data(self, data: dict):
        """更新六轴力数据"""
        self.liuzhouli_widget.update_data(data)
    
    def update_nianfujiao_data(self, style: int, data: dict):
        """更新粘附脚数据"""
        self.nianfujiao_widget.update_data(style, data)

    def update_nianfujiao_protocol(self, data: dict):
        self.nianfujiao_widget.update_protocol(data)

    def update_ultrasonic_data(self, data=None, *, status=""):
        distance = speed = None
        if data is not None:
            distance = data["distance_mm"]
            speed = data["speed_mm_s"]
            status = {
                "OK": "测量正常",
                "WARMUP": "等待速度估算",
                "TIMEOUT": "回波超时",
                "OUT_OF_RANGE": "超出测量范围",
                "INVALID_ECHO": "无效回波",
            }.get(data["status"], data["status"])
        distance_text = "--" if distance is None else f"{distance} mm"
        speed_text = "--" if speed is None else f"{speed:+d} mm/s"
        self.ultrasonic_distance.setText(f"距离 {distance_text}")
        self.ultrasonic_speed.setText(f"速度 {speed_text}")
        self.ultrasonic_status.setText(status)

"""Prefer the original chat's Qt 6; support the system Qt 5 on Linux too."""
try:
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize, QRect, QEvent
    from PyQt6.QtGui import QColor, QPainter
    from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame,
        QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
        QMessageBox, QPushButton, QSizePolicy, QSplitter, QTextEdit, QPlainTextEdit,
        QVBoxLayout, QWidget, QLineEdit)
except ImportError:
    from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize, QRect, QEvent
    from PyQt5.QtGui import QColor, QPainter
    from PyQt5.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame,
        QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
        QMessageBox, QPushButton, QSizePolicy, QSplitter, QTextEdit, QPlainTextEdit,
        QVBoxLayout, QWidget, QLineEdit)

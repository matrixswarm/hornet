"""Small revision-based chat UI, deliberately separate from legacy sessions."""
from __future__ import annotations

import copy
import os
import queue
import sys
import threading
import uuid
from pathlib import Path

from .qt import (Qt, QTimer, pyqtSignal, QApplication, QComboBox, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget, QLineEdit)
from .model import ModelClient, build_messages, discover_models
from .store import DistilStore
from .transcript import NumberedTranscript
from .exports import export_revision, normalize_export_path, suggested_filename, transcript_text


class MessageInput(QTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and modifiers == Qt.KeyboardModifier.NoModifier:
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class DistilWindow(QWidget):
    def __init__(self, root=None, client_factory=ModelClient, model_loader=discover_models):
        super().__init__()
        self.store = DistilStore(root or Path(os.getenv('HORNET_DISTIL_ROOT', Path.home() / 'hornet_distil')))
        self.client_factory = client_factory
        self.model_loader = model_loader
        self.model_results = queue.Queue()
        self._model_request = 0
        self._model_host = None
        self.current_revision_id = None
        self.state = None
        self.job = None
        self.results = queue.Queue()
        self._loading = False
        self._closed = False
        self._export_directory = Path.home() / 'Documents'
        if not self._export_directory.is_dir():
            self._export_directory = Path.home()
        self._export_filter = 'PDF (*.pdf)'
        self.setWindowTitle('Hornet Distil')
        self.resize(1160, 820)
        self.setMinimumSize(800, 590)
        self._build_ui()
        self._load_projects()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_results)
        self.timer.start(40)
        QTimer.singleShot(0, self.refresh_models)

    def _build_ui(self):
        self.setStyleSheet('''
            QWidget { background: #10151d; color: #e6edf5; font-size: 14px; }
            QTextEdit, QPlainTextEdit, QLineEdit, QListWidget, QComboBox { background: #18212d;
                border: 1px solid #334155; border-radius: 5px; padding: 7px; }
            QPushButton { background: #263549; border: none; border-radius: 5px; padding: 9px 14px; }
            QPushButton:hover { background: #334863; }
            QPushButton:disabled { color: #78879b; background: #1a2330; }
            QPushButton#primary { background: #f3b84b; color: #171c24; font-weight: bold; }
            QPushButton#primary:disabled { background: #534a36; color: #a59a84; }
            QListWidget::item { padding: 9px; }
            QListWidget::item:selected { background: #334863; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        header = QHBoxLayout()
        title = QLabel('HORNET / DISTIL')
        title.setStyleSheet('font-size: 23px; font-weight: bold; color: #f3b84b;')
        header.addWidget(title)
        header.addStretch()
        self.settings_button = QPushButton('Model settings')
        self.settings_button.clicked.connect(self.toggle_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)
        self.settings = QWidget()
        settings_box = QVBoxLayout(self.settings)
        settings_box.setContentsMargins(0, 4, 0, 4)
        settings_layout = QHBoxLayout()
        settings_box.addLayout(settings_layout)
        settings_layout.addWidget(QLabel('Server'))
        self.host = QLineEdit(os.getenv('LMSTUDIO_HOST', 'http://127.0.0.1:1234'))
        self.host.editingFinished.connect(self.refresh_models)
        self.host.setMinimumWidth(140)
        settings_layout.addWidget(self.host, 2)
        settings_layout.addWidget(QLabel('Model'))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText('Choose a model')
        self.model.setMinimumWidth(150)
        self.model.setCurrentText(os.getenv('LMSTUDIO_MODEL', ''))
        settings_layout.addWidget(self.model, 3)
        self.refresh_models_button = QPushButton('Refresh models')
        self.refresh_models_button.clicked.connect(self.refresh_models)
        settings_layout.addWidget(self.refresh_models_button)
        self.model_status = QLabel('')
        settings_layout.addWidget(self.model_status)
        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel('Maximum output tokens'))
        self.max_output_tokens = QLineEdit()
        self.max_output_tokens.setPlaceholderText('Server default')
        self.max_output_tokens.setToolTip('Maximum generated tokens, not input context. Leave empty to use the server default.')
        self.max_output_tokens.setMaximumWidth(150)
        limit_layout.addWidget(self.max_output_tokens)
        limit_layout.addWidget(QLabel('Timeout (seconds)'))
        self.request_timeout = QLineEdit(os.getenv('LMSTUDIO_REQUEST_TIMEOUT', '600'))
        self.request_timeout.setMaximumWidth(100)
        limit_layout.addWidget(self.request_timeout)
        limit_layout.addStretch()
        settings_box.addLayout(limit_layout)
        self.settings.hide()
        layout.addWidget(self.settings)
        panes = QSplitter(Qt.Orientation.Horizontal)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 12, 14, 0)
        side.addWidget(QLabel('PROJECT'))
        self.projects = QComboBox()
        self.projects.currentIndexChanged.connect(self.project_changed)
        side.addWidget(self.projects)
        self.new_project_button = QPushButton('+ New project')
        self.new_project_button.clicked.connect(self.new_project)
        side.addWidget(self.new_project_button)
        side.addSpacing(12)
        side.addWidget(QLabel('REVISIONS'))
        self.revisions = QListWidget()
        self.revisions.currentItemChanged.connect(self.revision_changed)
        self.revisions.itemClicked.connect(self.revision_changed)
        side.addWidget(self.revisions, 1)
        self.new_revision_button = QPushButton('+ New revision')
        self.new_revision_button.clicked.connect(self.new_revision)
        side.addWidget(self.new_revision_button)
        revision_actions = QHBoxLayout()
        self.clone_button = QPushButton('Clone')
        self.clone_button.clicked.connect(self.clone_revision)
        revision_actions.addWidget(self.clone_button)
        self.rename_button = QPushButton('Rename')
        self.rename_button.clicked.connect(self.rename_revision)
        revision_actions.addWidget(self.rename_button)
        self.delete_button = QPushButton('Delete')
        self.delete_button.clicked.connect(self.delete_revision)
        revision_actions.addWidget(self.delete_button)
        side.addLayout(revision_actions)
        self.restore_button = QPushButton('Restore deleted revision…')
        self.restore_button.clicked.connect(self.restore_revision)
        side.addWidget(self.restore_button)
        self.import_button = QPushButton('Import distilled text…')
        self.import_button.setToolTip('Replace this revision’s active chat with a text seed. Previous history is kept locally.')
        self.import_button.clicked.connect(self.import_text)
        side.addWidget(self.import_button)
        storage = QLabel('Storage: ' + str(self.store.root))
        storage.setWordWrap(True)
        storage.setStyleSheet('font-size: 11px; color: #99a9bd;')
        storage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        side.addWidget(storage)
        main = QWidget()
        body = QVBoxLayout(main)
        body.setContentsMargins(8, 12, 0, 0)
        self.revision_label = QLabel('Choose or create a project and revision')
        self.revision_label.setStyleSheet('font-size: 17px; font-weight: bold;')
        revision_header = QHBoxLayout()
        revision_header.addWidget(self.revision_label, 1)
        self.export_button = QPushButton('Save revision…')
        self.export_button.setToolTip('Export the current chat as PDF, Text, or Markdown. Unsent drafts and archived history are not included.')
        self.export_button.clicked.connect(self.save_revision)
        revision_header.addWidget(self.export_button)
        body.addLayout(revision_header)
        self.transcript = NumberedTranscript()
        body.addWidget(self.transcript, 1)
        self.input_text = MessageInput()
        self.input_text.setAcceptRichText(False)
        self.input_text.setPlaceholderText('Message…  Enter to send · Shift+Enter for a new line')
        self.input_text.setFixedHeight(100)
        self.input_text.send_requested.connect(self.handle_send)
        self.input_text.textChanged.connect(self.save_draft)
        body.addWidget(self.input_text)
        actions = QHBoxLayout()
        actions.addWidget(QLabel('Distill detail'))
        self.detail = QComboBox()
        self.detail.addItems(['Brief', 'Balanced', 'Detailed'])
        self.detail.setCurrentText('Balanced')
        self.detail.currentTextChanged.connect(self.save_detail)
        actions.addWidget(self.detail)
        self.distill_button = QPushButton('Distill')
        self.distill_button.setToolTip('Send the entire active chat to the model. Replace it only when distilled.md is saved.')
        self.distill_button.clicked.connect(self.handle_distill)
        actions.addWidget(self.distill_button)
        actions.addStretch()
        self.stop_button = QPushButton('Stop')
        self.stop_button.clicked.connect(self.stop)
        actions.addWidget(self.stop_button)
        self.send_button = QPushButton('Send')
        self.send_button.setObjectName('primary')
        self.send_button.clicked.connect(self.handle_send)
        actions.addWidget(self.send_button)
        body.addLayout(actions)
        hint = QLabel('Successful distills replace the active chat. New messages continue underneath. No automatic trimming.')
        hint.setStyleSheet('font-size: 11px; color: #99a9bd;')
        hint.setWordWrap(True)
        body.addWidget(hint)
        panes.addWidget(sidebar)
        panes.addWidget(main)
        panes.setStretchFactor(0, 0)
        panes.setStretchFactor(1, 1)
        panes.setSizes([245, 875])
        layout.addWidget(panes, 1)
        self.status_label = QLabel('Ready. Create a project to begin.')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._set_enabled()

    def toggle_settings(self):
        self.settings.setVisible(not self.settings.isVisible())

    def refresh_models(self):
        if self._closed:
            return
        self._model_request += 1
        ident, host = self._model_request, self.host.text().strip()
        if host != self._model_host:
            current = self.model.currentText().strip()
            self.model.clear()
            if current:
                self.model.setCurrentText(current)
            self._model_host = host
        self.model_status.setText('Loading models…')
        results, loader = self.model_results, self.model_loader
        def run():
            try:
                models = loader(host, api_key=os.getenv('LMSTUDIO_API_KEY', ''))
                results.put((ident, host, models, None))
            except Exception:
                results.put((ident, host, None, 'Models unavailable'))
        try:
            threading.Thread(target=run, daemon=True, name='HornetDistilModels').start()
        except Exception:
            self.model_status.setText('Could not start model discovery.')

    def _poll_models(self):
        while True:
            try:
                ident, host, models, error = self.model_results.get_nowait()
            except queue.Empty:
                break
            if ident != self._model_request or host != self.host.text().strip():
                continue
            if error:
                self.model_status.setText(error)
                continue
            current = self.model.currentText().strip()
            self.model.blockSignals(True)
            try:
                self.model.clear()
                self.model.addItems(models)
                if current:
                    self.model.setCurrentText(current)
                else:
                    self.model.setCurrentIndex(-1)
            finally:
                self.model.blockSignals(False)
            self.model_status.setText(('%d model%s' % (len(models), '' if len(models) == 1 else 's'))
                                      if models else 'No models found.')

    def _error(self, error):
        self.status_label.setText('Not saved: ' + str(error))

    def _set_enabled(self):
        busy = self.job is not None
        selected = self.current_revision_id is not None
        for widget in (self.projects, self.revisions, self.new_project_button, self.host, self.model, self.settings_button, self.refresh_models_button, self.max_output_tokens, self.request_timeout):
            widget.setEnabled(not busy)
        self.new_revision_button.setEnabled(not busy and self.projects.currentData() is not None)
        self.restore_button.setEnabled(not busy and self.projects.currentData() is not None)
        for widget in (self.input_text, self.detail, self.import_button, self.send_button, self.clone_button, self.rename_button, self.delete_button, self.export_button):
            widget.setEnabled(not busy and selected)
        self.distill_button.setEnabled(not busy and selected and bool(self.state and self.state['messages']))
        self.stop_button.setEnabled(busy)

    def _load_projects(self, project_id=None, revision_id=None):
        if not self.save_draft():
            return
        self._loading = True
        self.projects.clear()
        for project in self.store.list_projects():
            self.projects.addItem(project['name'], project['id'])
        if project_id is not None:
            self.projects.setCurrentIndex(self.projects.findData(project_id))
        self._loading = False
        self._load_revisions(revision_id)

    def _load_revisions(self, selected=None):
        if not self.save_draft():
            return
        self._loading = True
        self.revisions.clear()
        project_id = self.projects.currentData()
        if project_id is not None:
            for revision in self.store.list_revisions(project_id):
                item = QListWidgetItem(revision['name'])
                item.setData(Qt.ItemDataRole.UserRole, revision['id'])
                self.revisions.addItem(item)
                if revision['id'] == selected:
                    self.revisions.setCurrentItem(item)
        if self.revisions.currentItem() is None and self.revisions.count():
            self.revisions.setCurrentRow(0)
        self._loading = False
        self.revision_changed(self.revisions.currentItem())

    def project_changed(self, *_):
        if self._loading or self.job:
            return
        if not self.save_draft():
            self.projects.blockSignals(True)
            self.projects.setCurrentIndex(self.projects.findData(self.state['project_id']))
            self.projects.blockSignals(False)
            return
        self._load_revisions()

    def revision_changed(self, item, *_):
        if self._loading or self.job:
            return
        if not self.save_draft():
            self.revisions.blockSignals(True)
            for index in range(self.revisions.count()):
                previous = self.revisions.item(index)
                if previous.data(Qt.ItemDataRole.UserRole) == self.current_revision_id:
                    self.revisions.setCurrentItem(previous)
                    break
            self.revisions.blockSignals(False)
            return
        self._loading = True
        try:
            self.current_revision_id = item.data(Qt.ItemDataRole.UserRole) if item else None
            self.state = self.store.get_revision(self.current_revision_id) if item else None
            self.input_text.setPlainText(self.state.get('draft', '') if self.state else '')
            self.detail.setCurrentText(self.state.get('detail', 'Balanced') if self.state else 'Balanced')
            self._render()
            self.status_label.setText('Loaded saved revision.' if item else 'Create a project and revision to begin.')
        except Exception as error:
            self.current_revision_id = None
            self.state = None
            self._error(error)
        finally:
            self._loading = False
            self._set_enabled()

    def _render(self):
        if not self.state:
            self.transcript.clear()
            self.revision_label.setText('Choose or create a project and revision')
            return
        self.revision_label.setText(f"{self.projects.currentText()} / {self.state['name']}")
        self.transcript.setPlainText(transcript_text(self.state['messages']))
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def save_revision(self):
        if self.job or not self.state:
            return
        # Freeze what the selected revision shows before opening the modal dialog.
        snapshot = copy.deepcopy(self.state)
        project_name = self.projects.currentText()
        formats = {'PDF (*.pdf)': 'PDF', 'Text (*.txt)': 'Text', 'Markdown (*.md)': 'Markdown'}
        extension = {'PDF': '.pdf', 'Text': '.txt', 'Markdown': '.md'}[formats[self._export_filter]]
        suggested = self._export_directory / (suggested_filename(project_name, snapshot['name']) + extension)
        filename, selected_filter = QFileDialog.getSaveFileName(self, 'Save revision',
            str(suggested), ';;'.join(formats), self._export_filter,
            options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename:
            return
        try:
            format_name = formats.get(selected_filter, formats[self._export_filter])
            path = normalize_export_path(filename, format_name)
            storage = self.store.root.resolve()
            resolved = path.resolve()
            if resolved == storage or storage in resolved.parents:
                raise ValueError('Choose a folder outside the chat storage folder to protect its saved history.')
            if path.is_symlink():
                raise ValueError('Choose a regular file, not a symbolic link.')
            overwrite = path.exists()
            if overwrite:
                answer = QMessageBox.question(self, 'Replace exported file?',
                    'This file already exists:\n' + str(path) + '\n\nReplace it with the selected revision?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            saved = export_revision(path, project_name, snapshot, format_name, overwrite=overwrite)
            self._export_directory = saved.parent
            self._export_filter = next(label for label, value in formats.items() if value == format_name)
            self.status_label.setText('Saved ' + format_name + ': ' + str(saved))
        except Exception as error:
            self.status_label.setText('Export not saved: ' + str(error))

    def new_project(self):
        if self.job or not self.save_draft():
            return
        name, ok = QInputDialog.getText(self, 'New project', 'Project name')
        if not ok or not name.strip():
            return
        try:
            project = self.store.create_project(name.strip())
            self._load_projects(project['id'])
            self.status_label.setText('Project created. Choose New revision to start its chat.')
        except Exception as error:
            self._error(error)

    def new_revision(self):
        if self.job or self.projects.currentData() is None or not self.save_draft():
            return
        name, ok = QInputDialog.getText(self, 'New revision', 'Revision name (starts with an empty chat)')
        if not ok or not name.strip():
            return
        try:
            revision = self.store.create_revision(self.projects.currentData(), name.strip())
            self._load_revisions(revision['id'])
        except Exception as error:
            self._error(error)

    def clone_revision(self):
        if self.job or not self.state or not self.save_draft():
            return
        name, ok = QInputDialog.getText(self, 'Clone revision', 'Name for the independent copy', text=self.state['name'] + ' copy')
        if not ok or not name.strip():
            return
        try:
            revision = self.store.clone_revision(self.current_revision_id, name.strip(), expected_version=self.state['version'])
            self._load_revisions(revision['id'])
            self.status_label.setText('Independent revision cloned. The original is unchanged.')
        except Exception as error:
            self._error(error)

    def rename_revision(self):
        if self.job or not self.state or not self.save_draft():
            return
        name, ok = QInputDialog.getText(self, 'Rename revision', 'Revision name', text=self.state['name'])
        if not ok or not name.strip():
            return
        try:
            self.state = self.store.rename_revision(self.current_revision_id, name.strip(), expected_version=self.state['version'])
            self._load_revisions(self.current_revision_id)
        except Exception as error:
            self._error(error)

    def delete_revision(self):
        if self.job or not self.state or not self.save_draft():
            return
        answer = QMessageBox.question(self, 'Delete revision?', 'Remove “' + self.state['name'] + '” from the revision list? Its saved work is retained and can be restored.')
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_revision(self.current_revision_id, expected_version=self.state['version'])
            self._load_revisions()
            self.status_label.setText('Revision deleted from the list. Use Restore deleted revision to bring it back.')
        except Exception as error:
            self._error(error)

    def restore_revision(self):
        if self.job or self.projects.currentData() is None or not self.save_draft():
            return
        try:
            deleted = [row for row in self.store.list_revisions(self.projects.currentData(), include_deleted=True) if row['deleted']]
            if not deleted:
                self.status_label.setText('No deleted revisions in this project.')
                return
            labels = [row['name'] + ' (' + row['id'][:8] + ')' for row in deleted]
            choice, ok = QInputDialog.getItem(self, 'Restore revision', 'Revision', labels, 0, False)
            if not ok:
                return
            state = self.store.restore_revision(deleted[labels.index(choice)]['id'])
            self._load_revisions(state['id'])
        except Exception as error:
            self._error(error)

    def save_draft(self):
        if self._loading or not self.current_revision_id or self.job:
            return True
        content = self.input_text.toPlainText()
        if content == self.state.get('draft', ''):
            return True
        try:
            self.state = self.store.save_draft(self.current_revision_id, content,
                expected_version=self.state['version'])
            return True
        except Exception as error:
            self.status_label.setText('Draft not saved. Keep it here or copy it before leaving this revision. ' + str(error))
            return False

    def save_detail(self, value):
        if self._loading or not self.current_revision_id or self.job:
            return
        try:
            self.state = self.store.save_detail(self.current_revision_id, value,
                expected_version=self.state['version'])
        except Exception as error:
            self.detail.blockSignals(True)
            self.detail.setCurrentText(self.state['detail'])
            self.detail.blockSignals(False)
            self._error(error)

    def import_text(self):
        if self.job or not self.state or not self.save_draft():
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Import distilled text', '', 'Text (*.md *.txt *.json);;All files (*)')
        if not filename:
            return
        if self.state['messages']:
            answer = QMessageBox.question(self, 'Replace active chat?', 'Import replaces this revision’s active chat and distilled.md. Earlier messages and distills remain in its local history. Continue?')
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            content = Path(filename).read_text(encoding='utf-8-sig')
            self.state = self.store.replace_with_distilled(self.current_revision_id, content,
                detail=self.detail.currentText(), source='import', expected_version=self.state['version'])
            self._render()
            self.status_label.setText('Imported as this revision’s distilled context. Original file unchanged.')
            self._set_enabled()
        except Exception as error:
            self._error(error)

    def handle_send(self):
        if self.job or not self.state or not self.input_text.toPlainText().strip():
            return
        content = self.input_text.toPlainText()
        try:
            client = self._client()
            self.state = self.store.add_message(self.current_revision_id, 'user', content,
                expected_version=self.state['version'])
            self.input_text.clear()
            self._render()
            self._start('chat', client)
        except Exception as error:
            self._error(error)

    def handle_distill(self):
        if self.job or not self.state or not self.state['messages']:
            return
        if self.input_text.toPlainText().strip():
            self.status_label.setText('Send your draft or clear it before distilling. Your draft is still here.')
            return
        try:
            self._start('distill', self._client())
        except Exception as error:
            self._error(error)

    def _client(self):
        if not self.model.currentText().strip():
            raise ValueError('Choose a model in Model settings.')
        output = self.max_output_tokens.text().strip()
        timeout = self.request_timeout.text().strip()
        if output and (not output.isascii() or not output.isdecimal() or not 1 <= int(output) <= 1000000):
            raise ValueError('Maximum output tokens must be between 1 and 1000000, or blank for server default.')
        if not timeout.isascii() or not timeout.isdecimal() or not 1 <= int(timeout) <= 86400:
            raise ValueError('Timeout must be between 1 and 86400 seconds.')
        return self.client_factory(self.host.text().strip(), self.model.currentText().strip(),
            api_key=os.getenv('LMSTUDIO_API_KEY', ''), timeout=int(timeout),
            max_tokens=int(output) if output else None)

    def _start(self, mode, client):
        detail = self.detail.currentText()
        messages = build_messages(self.state['messages'], mode=mode, detail=detail)
        job = {'id': uuid.uuid4().hex, 'cancel': threading.Event(), 'client': client,
            'mode': mode, 'detail': detail, 'revision': self.current_revision_id,
            'version': self.state['version']}
        self.job = job
        self._set_enabled()
        self.status_label.setText('Distilling the entire active chat… Current chat stays until saved.' if mode == 'distill' else 'Waiting for Hornet…')
        results = self.results
        def run():
            try:
                result = client.complete(messages, job['cancel'])
                results.put((job['id'], result, None))
            except Exception as error:
                results.put((job['id'], None, error))
        try:
            threading.Thread(target=run, daemon=True, name='HornetDistilRequest').start()
        except Exception:
            self.job = None
            self._set_enabled()
            raise

    def poll_results(self):
        if self._closed:
            return
        self._poll_models()
        while True:
            try:
                ident, result, error = self.results.get_nowait()
            except queue.Empty:
                break
            job = self.job
            if not job or job['id'] != ident or job['cancel'].is_set():
                continue
            self.job = None
            try:
                if error:
                    raise error
                if job['mode'] == 'distill':
                    self.state = self.store.replace_with_distilled(job['revision'], result,
                        detail=job['detail'], source='distill', expected_version=job['version'])
                    self.status_label.setText('Distill saved. Continue the conversation below. ' + str(self.store.distilled_path(job['revision'])))
                else:
                    self.state = self.store.add_message(job['revision'], 'assistant', result,
                        expected_version=job['version'])
                    self.status_label.setText('Reply saved.')
                self._render()
            except Exception as failure:
                prefix = 'Distill not saved. Current chat and previous distill kept. ' if job['mode'] == 'distill' else 'Reply not saved. Your message is kept. '
                self.status_label.setText(prefix + str(failure))
            self._set_enabled()

    def stop(self):
        job = self.job
        if not job:
            return
        job['cancel'].set()
        self.job = None
        cancel = getattr(job['client'], 'cancel', None)
        if cancel:
            # Closing a socket may block on a read lock; never block the UI.
            try:
                threading.Thread(target=cancel, daemon=True, name='HornetDistilStop').start()
            except RuntimeError:
                pass  # The transport also watches the cancellation event.
        self.status_label.setText('Stopped. Current chat and saved distill unchanged.' if job['mode'] == 'distill' else 'Stopped. Your message is saved; no partial reply was added.')
        self._set_enabled()

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self.stop()
        if not self.save_draft():
            event.ignore()
            return
        self._closed = True
        self.timer.stop()
        self.store.close()
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = DistilWindow()
    window.show()
    sys.exit(app.exec())

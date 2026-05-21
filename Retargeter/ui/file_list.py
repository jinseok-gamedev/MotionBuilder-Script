"""Source FBX list widget with drag&drop and context-menu actions.

``FbxDropList`` is a thin ``QListWidget`` subclass that:

* accepts ``.fbx`` files and folders dropped from the OS file manager
  (folder drops walk one level for ``.fbx`` files, mirroring
  ``RetargeterPanel._on_add_folder``);
* exposes a right-click context menu (open in explorer, copy path, remove,
  clear);
* emits ``filesChanged`` whenever items are added or removed so the host
  panel can refresh its "N files (X MB total)" footer and Run-button
  enabled-state.

The widget never touches the MoBu scene; it is purely a path container.
"""

from __future__ import annotations

import os
from typing import List

from ._qt import QtCore, QtGui, QtWidgets  # type: ignore
from ._qt_helpers import open_in_file_explorer


_USER_ROLE_PATH = QtCore.Qt.UserRole


class FbxDropList(QtWidgets.QListWidget):
    """Selection-friendly file list that accepts FBX drops from the OS."""

    filesChanged = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        self.setMinimumHeight(110)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_path(self, path: str) -> bool:
        """Add a single FBX path. Returns False if duplicate or invalid."""
        if not path:
            return False
        if not path.lower().endswith(".fbx"):
            return False
        existing = self.all_paths()
        if path in existing:
            return False
        item = QtWidgets.QListWidgetItem(os.path.basename(path))
        item.setData(_USER_ROLE_PATH, path)
        item.setToolTip(path)
        self.addItem(item)
        self.filesChanged.emit()
        return True

    def add_paths(self, paths) -> int:
        """Bulk add; returns the number of paths that were actually added."""
        added = 0
        existing = set(self.all_paths())
        for p in paths:
            if not p or not p.lower().endswith(".fbx") or p in existing:
                continue
            item = QtWidgets.QListWidgetItem(os.path.basename(p))
            item.setData(_USER_ROLE_PATH, p)
            item.setToolTip(p)
            self.addItem(item)
            existing.add(p)
            added += 1
        if added:
            self.filesChanged.emit()
        return added

    def remove_selected(self) -> int:
        removed = 0
        for item in self.selectedItems():
            self.takeItem(self.row(item))
            removed += 1
        if removed:
            self.filesChanged.emit()
        return removed

    def clear_all(self) -> None:
        if self.count() == 0:
            return
        self.clear()
        self.filesChanged.emit()

    def all_paths(self) -> List[str]:
        return [self.item(i).data(_USER_ROLE_PATH) for i in range(self.count())]

    def total_size_bytes(self) -> int:
        total = 0
        for p in self.all_paths():
            try:
                total += os.path.getsize(p)
            except OSError:
                continue
        return total

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if self._mime_has_fbx(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if self._mime_has_fbx(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        mime = event.mimeData()
        if not mime.hasUrls():
            super().dropEvent(event)
            return
        collected: List[str] = []
        for url in mime.urls():
            local = url.toLocalFile()
            if not local:
                continue
            if os.path.isdir(local):
                try:
                    for name in sorted(os.listdir(local)):
                        if name.lower().endswith(".fbx"):
                            collected.append(os.path.join(local, name))
                except OSError:
                    continue
            elif local.lower().endswith(".fbx"):
                collected.append(local)
        if collected:
            self.add_paths(collected)
            event.acceptProposedAction()
        else:
            event.ignore()

    @staticmethod
    def _mime_has_fbx(mime: QtCore.QMimeData) -> bool:
        if not mime.hasUrls():
            return False
        for url in mime.urls():
            local = url.toLocalFile()
            if not local:
                continue
            if os.path.isdir(local) or local.lower().endswith(".fbx"):
                return True
        return False

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.itemAt(pos)
        menu = QtWidgets.QMenu(self)
        act_open = menu.addAction("Reveal in file explorer")
        act_copy = menu.addAction("Copy path")
        menu.addSeparator()
        act_remove = menu.addAction("Remove selected")
        act_clear = menu.addAction("Clear all")
        act_open.setEnabled(item is not None)
        act_copy.setEnabled(item is not None)
        act_remove.setEnabled(self.selectedItems() != [])
        act_clear.setEnabled(self.count() > 0)

        chosen = menu.exec_(self.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_open and item is not None:
            open_in_file_explorer(item.data(_USER_ROLE_PATH))
        elif chosen is act_copy and item is not None:
            QtWidgets.QApplication.clipboard().setText(item.data(_USER_ROLE_PATH))
        elif chosen is act_remove:
            self.remove_selected()
        elif chosen is act_clear:
            self.clear_all()

"""Pair Align dialog (PySide2 / PySide6).

Shows the user a list of FBCharacters in the scene, lets them pick a
source / target pair, choose which chains to align, run the alignment, and
inspect a colour-coded diff of the offsets that were applied.

PySide2 is the standard MotionBuilder UI toolkit (2018+); PySide6 is used
in 2023+ on Apple Silicon. We import the first available one so the same
script works across versions.
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from ..core.chain_groups import (
    CHAIN_DISPLAY_NAMES,
    CHAIN_PROCESS_ORDER,
    ChainId,
)
from ..core.preset_io import (
    Preset,
    default_presets_dir,
    list_presets,
    load_preset,
    save_preset,
    apply_preset,
)
from ..core.snapshot import OffsetSnapshot, restore as restore_snapshot
from ..core.tpose_align import (
    AlignOptions,
    AlignResult,
    align_pair,
    connect_for_retarget,
)
from ..core.validation import (
    OffsetGrade,
    Severity,
    compare_proportions,
    is_y_up_scene,
    severity_color_hex,
)


ALIGN_DIALOG_OBJECT_NAME = "TPoseAlignerAlignDialog"


def _scene_characters() -> List:
    try:
        from pyfbsdk import FBSystem  # type: ignore
    except Exception:
        return []
    return list(FBSystem().Scene.Characters)


def _select_in_scene(model_name: str) -> None:
    try:
        from pyfbsdk import FBFindModelByLabelName  # type: ignore
    except Exception:
        return
    model = FBFindModelByLabelName(model_name)
    if model is None:
        return
    model.Selected = True


class AlignDialog(QtWidgets.QDialog):
    """Main pair-alignment dialog."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName(ALIGN_DIALOG_OBJECT_NAME)
        self.setWindowTitle("TPoseAligner - Pair Align")
        self.resize(720, 640)
        self.setSizeGripEnabled(True)

        self._characters: List = []
        self._last_source_result: Optional[AlignResult] = None
        self._last_target_result: Optional[AlignResult] = None
        self._pre_source_snapshot: Optional[OffsetSnapshot] = None
        self._pre_target_snapshot: Optional[OffsetSnapshot] = None

        self._build_ui()
        self.refresh_characters()
        self._refresh_preset_list()
        self._update_scene_warning()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        char_box = QtWidgets.QGroupBox("Characters")
        char_form = QtWidgets.QFormLayout(char_box)
        self.source_combo = QtWidgets.QComboBox()
        self.target_combo = QtWidgets.QComboBox()
        char_form.addRow("Source:", self.source_combo)
        char_form.addRow("Target:", self.target_combo)
        refresh_btn = QtWidgets.QPushButton("Refresh from scene")
        refresh_btn.clicked.connect(self.refresh_characters)
        char_form.addRow("", refresh_btn)
        layout.addWidget(char_box)

        chain_box = QtWidgets.QGroupBox("Chains to align")
        chain_grid = QtWidgets.QGridLayout(chain_box)
        self._chain_checks: Dict[ChainId, QtWidgets.QCheckBox] = {}
        for idx, chain in enumerate(CHAIN_PROCESS_ORDER):
            check = QtWidgets.QCheckBox(CHAIN_DISPLAY_NAMES[chain])
            check.setChecked(chain not in (ChainId.LEFT_HAND, ChainId.RIGHT_HAND))
            row, col = divmod(idx, 2)
            chain_grid.addWidget(check, row, col)
            self._chain_checks[chain] = check
        layout.addWidget(chain_box)

        opts_box = QtWidgets.QGroupBox("Options")
        opts_grid = QtWidgets.QGridLayout(opts_box)
        self.opt_clear = QtWidgets.QCheckBox("Clear existing offsets first")
        self.opt_clear.setChecked(True)
        self.opt_micro_bend = QtWidgets.QCheckBox("Preserve micro bend (~1 deg)")
        self.opt_micro_bend.setChecked(True)
        self.opt_wrist_flip = QtWidgets.QCheckBox("Wrist flip guard")
        self.opt_wrist_flip.setChecked(True)
        self.opt_palms = QtWidgets.QCheckBox("Palms down post-pass")
        self.opt_palms.setChecked(True)
        self.opt_feet = QtWidgets.QCheckBox("Feet flat / forward post-pass")
        self.opt_feet.setChecked(True)
        self.opt_fingers = QtWidgets.QCheckBox("Include fingers")
        self.opt_fingers.setChecked(False)
        self.opt_require_y_up = QtWidgets.QCheckBox("Require Y-up scene")
        self.opt_require_y_up.setChecked(True)

        opts_grid.addWidget(self.opt_clear, 0, 0)
        opts_grid.addWidget(self.opt_micro_bend, 0, 1)
        opts_grid.addWidget(self.opt_wrist_flip, 1, 0)
        opts_grid.addWidget(self.opt_palms, 1, 1)
        opts_grid.addWidget(self.opt_feet, 2, 0)
        opts_grid.addWidget(self.opt_fingers, 2, 1)
        opts_grid.addWidget(self.opt_require_y_up, 3, 0)
        layout.addWidget(opts_box)

        self.scene_warning = QtWidgets.QLabel("")
        self.scene_warning.setStyleSheet("color: #c84630; font-weight: bold;")
        self.scene_warning.setVisible(False)
        layout.addWidget(self.scene_warning)

        button_row = QtWidgets.QHBoxLayout()
        align_src_btn = QtWidgets.QPushButton("Align Source")
        align_tgt_btn = QtWidgets.QPushButton("Align Target")
        align_both_btn = QtWidgets.QPushButton("Align Both")
        align_both_btn.setDefault(True)
        restore_btn = QtWidgets.QPushButton("Restore Snapshot")
        connect_btn = QtWidgets.QPushButton("Connect && Activate")
        for btn in (align_src_btn, align_tgt_btn, align_both_btn, restore_btn, connect_btn):
            button_row.addWidget(btn)
        layout.addLayout(button_row)

        align_src_btn.clicked.connect(lambda: self._run_align(do_source=True, do_target=False))
        align_tgt_btn.clicked.connect(lambda: self._run_align(do_source=False, do_target=True))
        align_both_btn.clicked.connect(lambda: self._run_align(do_source=True, do_target=True))
        restore_btn.clicked.connect(self._on_restore)
        connect_btn.clicked.connect(self._on_connect)

        diff_box = QtWidgets.QGroupBox("Applied offsets")
        diff_layout = QtWidgets.QVBoxLayout(diff_box)
        self.diff_table = QtWidgets.QTableWidget(0, 6)
        self.diff_table.setHorizontalHeaderLabels(
            ["Side", "Bone", "RX", "RY", "RZ", "Severity"],
        )
        self.diff_table.horizontalHeader().setStretchLastSection(True)
        self.diff_table.verticalHeader().setVisible(False)
        self.diff_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.diff_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.diff_table.itemDoubleClicked.connect(self._on_diff_double_clicked)
        diff_layout.addWidget(self.diff_table)
        layout.addWidget(diff_box, stretch=1)

        diag_row = QtWidgets.QHBoxLayout()
        self.diag_label = QtWidgets.QLabel("")
        self.diag_label.setWordWrap(True)
        proportion_btn = QtWidgets.QPushButton("Compare proportions")
        proportion_btn.clicked.connect(self._on_compare_proportions)
        diag_row.addWidget(self.diag_label, stretch=1)
        diag_row.addWidget(proportion_btn)
        layout.addLayout(diag_row)

        preset_box = QtWidgets.QGroupBox("Presets")
        preset_layout = QtWidgets.QHBoxLayout(preset_box)
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.setMinimumWidth(220)
        save_preset_btn = QtWidgets.QPushButton("Save current as preset")
        load_preset_btn = QtWidgets.QPushButton("Load selected preset")
        refresh_preset_btn = QtWidgets.QPushButton("Refresh")
        preset_layout.addWidget(self.preset_combo, stretch=1)
        preset_layout.addWidget(load_preset_btn)
        preset_layout.addWidget(save_preset_btn)
        preset_layout.addWidget(refresh_preset_btn)
        layout.addWidget(preset_box)

        save_preset_btn.clicked.connect(self._on_save_preset)
        load_preset_btn.clicked.connect(self._on_load_preset)
        refresh_preset_btn.clicked.connect(self._refresh_preset_list)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color: #888;")
        layout.addWidget(self.status)

    def refresh_characters(self) -> None:
        self._characters = _scene_characters()
        names = [getattr(c, "LongName", "<unnamed>") for c in self._characters]
        for combo in (self.source_combo, self.target_combo):
            current = combo.currentText()
            combo.clear()
            combo.addItems(names)
            if current in names:
                combo.setCurrentText(current)
        if len(names) >= 2 and self.target_combo.currentIndex() == self.source_combo.currentIndex():
            self.target_combo.setCurrentIndex(1 if self.source_combo.currentIndex() == 0 else 0)
        self._update_scene_warning()

    def _update_scene_warning(self) -> None:
        if is_y_up_scene():
            self.scene_warning.setVisible(False)
            return
        self.scene_warning.setText(
            "Scene is not Y-up. HumanIK retargeting may misbehave. "
            "Set up axis to Y in File > Scene Settings."
        )
        self.scene_warning.setVisible(True)

    def _selected_source(self):
        idx = self.source_combo.currentIndex()
        if 0 <= idx < len(self._characters):
            return self._characters[idx]
        return None

    def _selected_target(self):
        idx = self.target_combo.currentIndex()
        if 0 <= idx < len(self._characters):
            return self._characters[idx]
        return None

    def _build_options(self) -> AlignOptions:
        chains: Set[ChainId] = {
            chain for chain, check in self._chain_checks.items() if check.isChecked()
        }
        if not chains:
            chains = set(CHAIN_PROCESS_ORDER)
        return AlignOptions(
            chains=chains,
            clear_existing=self.opt_clear.isChecked(),
            preserve_micro_bend=self.opt_micro_bend.isChecked(),
            handle_wrist_flip=self.opt_wrist_flip.isChecked(),
            palms_down=self.opt_palms.isChecked(),
            feet_flat_forward=self.opt_feet.isChecked(),
            include_fingers=self.opt_fingers.isChecked(),
            require_y_up=self.opt_require_y_up.isChecked(),
        )

    def _run_align(self, do_source: bool, do_target: bool) -> None:
        source = self._selected_source() if do_source else None
        target = self._selected_target() if do_target else None
        if do_source and source is None:
            self._set_status("Source character not selected.", error=True)
            return
        if do_target and target is None:
            self._set_status("Target character not selected.", error=True)
            return
        if do_source and do_target and source is target:
            self._set_status("Source and target must be different characters.", error=True)
            return

        options = self._build_options()
        try:
            from ..core.tpose_align import align_character_to_canonical_tpose
            if do_source and do_target:
                src_res, tgt_res = align_pair(source, target, options)
            else:
                src_res = align_character_to_canonical_tpose(source, options) if do_source else None
                tgt_res = align_character_to_canonical_tpose(target, options) if do_target else None
        except Exception as exc:
            self._set_status(f"Alignment failed: {exc}", error=True)
            traceback.print_exc()
            return

        if src_res is not None:
            self._last_source_result = src_res
            self._pre_source_snapshot = src_res.pre_snapshot
        if tgt_res is not None:
            self._last_target_result = tgt_res
            self._pre_target_snapshot = tgt_res.pre_snapshot

        self._populate_diff_table(src_res, tgt_res)

        parts = []
        if src_res is not None:
            parts.append(f"Source: {len(src_res.offsets)} offsets, {src_res.num_high_warnings} high")
        if tgt_res is not None:
            parts.append(f"Target: {len(tgt_res.offsets)} offsets, {tgt_res.num_high_warnings} high")
        self._set_status(". ".join(parts) or "Done.")

    def _populate_diff_table(
        self,
        src_res: Optional[AlignResult],
        tgt_res: Optional[AlignResult],
    ) -> None:
        rows: List[Tuple[str, str, Tuple[float, float, float], OffsetGrade]] = []
        if src_res is not None:
            for name, offset in sorted(src_res.offsets.items()):
                grade = src_res.grades.get(name, OffsetGrade(Severity.OK, 0.0))
                rows.append(("Source", name, offset, grade))
        if tgt_res is not None:
            for name, offset in sorted(tgt_res.offsets.items()):
                grade = tgt_res.grades.get(name, OffsetGrade(Severity.OK, 0.0))
                rows.append(("Target", name, offset, grade))

        source_char = self._selected_source()
        target_char = self._selected_target()

        self.diff_table.setRowCount(len(rows))
        for row_idx, (side, name, offset, grade) in enumerate(rows):
            colour = QtGui.QColor(severity_color_hex(grade.severity))
            owner = source_char if side == "Source" else target_char
            display = self._friendly_bone_label(owner, name)
            for col, text in enumerate((
                side, display,
                f"{offset[0]:+7.2f}", f"{offset[1]:+7.2f}", f"{offset[2]:+7.2f}",
                "{:.1f}deg {}".format(grade.angle_deg, grade.severity.value),
            )):
                item = QtWidgets.QTableWidgetItem(text)
                item.setForeground(colour)
                if col == 1:
                    item.setData(QtCore.Qt.UserRole, (side, name))
                    item.setToolTip(name)
                self.diff_table.setItem(row_idx, col, item)
        self.diff_table.resizeColumnsToContents()

    @staticmethod
    def _friendly_slot(node_name: str) -> str:
        """Strip the kFB prefix and NodeId suffix for cleaner display."""
        n = node_name
        if n.startswith("kFB"):
            n = n[3:]
        if n.endswith("NodeId"):
            n = n[:-6]
        return n

    def _friendly_bone_label(self, character, node_name: str) -> str:
        """Return e.g. ``LeftCollar  (Bip01_L_Clavicle)``.

        Falls back to just the slot name if the character is not provided
        or the bone is unmapped on this character.
        """
        slot = self._friendly_slot(node_name)
        if character is None:
            return slot
        from ..core.canonical_pose import resolve_node_id
        node_id = resolve_node_id(node_name)
        if node_id is None:
            return slot
        try:
            bone = character.GetModel(node_id)
        except Exception:
            bone = None
        if bone is None:
            return slot
        bone_name = bone.LongName.rsplit(":", 1)[-1]
        return f"{slot}  ({bone_name})"

    def _on_diff_double_clicked(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        payload = item.data(QtCore.Qt.UserRole)
        if not payload:
            return
        side, name = payload
        from ..core.canonical_pose import resolve_node_id
        node_id = resolve_node_id(name)
        if node_id is None:
            return
        char = self._selected_source() if side == "Source" else self._selected_target()
        if char is None:
            return
        bone = char.GetModel(node_id)
        if bone is not None:
            try:
                bone.Selected = True
                self._set_status(f"Selected bone {bone.LongName}.")
            except Exception:
                pass

    def _on_restore(self) -> None:
        ok = False
        if self._pre_source_snapshot is not None and self._selected_source() is not None:
            try:
                restore_snapshot(self._selected_source(), self._pre_source_snapshot)
                ok = True
            except Exception as exc:
                self._set_status(f"Restore source failed: {exc}", error=True)
                return
        if self._pre_target_snapshot is not None and self._selected_target() is not None:
            try:
                restore_snapshot(self._selected_target(), self._pre_target_snapshot)
                ok = True
            except Exception as exc:
                self._set_status(f"Restore target failed: {exc}", error=True)
                return
        if not ok:
            self._set_status("No snapshot to restore. Run alignment first.", error=True)
            return
        self.diff_table.setRowCount(0)
        self._set_status("Restored pre-alignment offsets.")

    def _on_connect(self) -> None:
        source = self._selected_source()
        target = self._selected_target()
        if source is None or target is None or source is target:
            self._set_status("Need distinct source and target characters.", error=True)
            return
        try:
            connect_for_retarget(source, target, activate=True)
            self._set_status(
                f"Connected {target.LongName} <- {source.LongName}. "
                "Use Bake > Plot To Skeleton when ready."
            )
        except Exception as exc:
            self._set_status(f"Connect failed: {exc}", error=True)

    def _on_compare_proportions(self) -> None:
        source = self._selected_source()
        target = self._selected_target()
        if source is None or target is None or source is target:
            self._set_status("Need distinct source and target characters.", error=True)
            return
        try:
            rep = compare_proportions(source, target)
        except Exception as exc:
            self._set_status(f"Proportion compare failed: {exc}", error=True)
            return
        text = (
            "Height ratio (target / source) {:.2f}x | Arm {:.2f}x | Leg {:.2f}x"
            .format(rep.height_ratio, rep.arm_ratio, rep.leg_ratio)
        )
        if rep.notes:
            text += "\n" + "\n".join("- " + n for n in rep.notes)
        self.diag_label.setText(text)

    def _refresh_preset_list(self) -> None:
        self.preset_combo.clear()
        for path in list_presets():
            self.preset_combo.addItem(path.stem, str(path))

    def _on_save_preset(self) -> None:
        if self._last_source_result is None and self._last_target_result is None:
            self._set_status("Run alignment before saving a preset.", error=True)
            return
        source = self._selected_source()
        target = self._selected_target()
        src_name = getattr(source, "LongName", "source") if source else "source"
        tgt_name = getattr(target, "LongName", "target") if target else "target"
        preset = Preset(
            source_character=src_name,
            target_character=tgt_name,
            options=self._build_options().to_dict(),
            source_snapshot=(
                self._last_source_result.post_snapshot if self._last_source_result else None
            ),
            target_snapshot=(
                self._last_target_result.post_snapshot if self._last_target_result else None
            ),
        )
        try:
            path = save_preset(preset)
        except Exception as exc:
            self._set_status(f"Save preset failed: {exc}", error=True)
            return
        self._refresh_preset_list()
        self._set_status(f"Saved preset {path.name}")

    def _on_load_preset(self) -> None:
        path = self.preset_combo.currentData()
        if not path:
            self._set_status("No preset selected.", error=True)
            return
        try:
            preset = load_preset(Path(path))
        except Exception as exc:
            self._set_status(f"Load preset failed: {exc}", error=True)
            return
        n_src, n_tgt = apply_preset(preset, self._selected_source(), self._selected_target())
        self._set_status(
            f"Applied preset {Path(path).name}: source={n_src} bones, target={n_tgt} bones."
        )

    def _set_status(self, text: str, error: bool = False) -> None:
        if error:
            self.status.setStyleSheet("color: #c84630; font-weight: bold;")
        else:
            self.status.setStyleSheet("color: #888;")
        self.status.setText(text)


def _close_existing_align_dialogs() -> None:
    """Close any prior copy of this dialog still alive in the Qt app.

    The menu callback in ``install_menus.py`` purges the whole
    ``TPoseAligner`` package out of ``sys.modules`` before re-importing, so a
    module-level singleton reference is unreliable. The QWidget itself is
    owned by Qt and survives reloads, so we find it via ``objectName``.
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        try:
            if widget.objectName() != ALIGN_DIALOG_OBJECT_NAME:
                continue
        except RuntimeError:
            continue
        try:
            widget.close()
            widget.deleteLater()
        except Exception:
            pass


def show_align_dialog():
    """Single-instance entry point for the menu / Python editor."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    _close_existing_align_dialogs()

    parent = app.activeWindow()
    dialog = AlignDialog(parent)
    dialog.refresh_characters()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


if __name__ == "__main__":
    show_align_dialog()

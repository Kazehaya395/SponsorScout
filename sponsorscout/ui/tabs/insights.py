"""Insights tab: aggregated distribution of categories, skills, domains, and sponsorship.

Draws vibrant, colourful charts with native QPainter:
- DonutPieChart with clear interactive legend, slice percentages, and hover tooltips.
- Dynamic HorizontalBarChart with gradients, counts, and scrollable container for ALL data points.
- Table with distinct colour-coded badges for Visa / Relocation statuses.
- Country & source filter controls and on-demand database re-analysis.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QSize, Signal, QThread, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QLinearGradient
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from sponsorscout.ui.async_load import AsyncLoader
from sponsorscout.i18n import _

logger = logging.getLogger(__name__)

COLOR_PALETTE = [
    ("#3B82F6", "#1D4ED8"),  # Blue
    ("#10B981", "#047857"),  # Emerald
    ("#8B5CF6", "#6D28D9"),  # Purple
    ("#F59E0B", "#B45309"),  # Amber
    ("#EC4899", "#BE185D"),  # Pink
    ("#06B6D4", "#0E7490"),  # Cyan
    ("#F97316", "#C2410C"),  # Orange
    ("#6366F1", "#4338CA"),  # Indigo
    ("#14B8A6", "#0F766E"),  # Teal
    ("#84CC16", "#4D7C0F"),  # Lime
    ("#E11D48", "#9F1239"),  # Rose
    ("#64748B", "#334155"),  # Slate
]


class DonutPieChart(QWidget):
    """Custom Donut / Pie chart with interactive filtering, side legend, and hover tooltips."""

    slice_clicked = Signal(str)  # emits the clicked category/domain label (or empty string to clear)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[tuple[str, int]] = []
        self.selected_label: str = ""
        self.setMinimumHeight(240)
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMouseTracking(True)
        self._slices: list[dict] = []
        self._legend_rects: list[tuple[QRect, str]] = []

    def set_data(self, items: list[tuple[str, int]], selected_label: str = ""):
        self.items = items or []
        self.selected_label = selected_label or ""
        self._compute_slices()
        self.update()

    def set_selected(self, label: str):
        self.selected_label = label or ""
        self.update()

    def _compute_slices(self):
        self._slices = []
        if not self.items:
            return
        total = sum(cnt for _, cnt in self.items) or 1
        top_items = self.items[:8]
        other_cnt = sum(cnt for _, cnt in self.items[8:])
        display_items = list(top_items)
        if other_cnt > 0:
            display_items.append((_("Other"), other_cnt))

        current_angle = 90.0
        for idx, (label, count) in enumerate(display_items):
            pct = (count / total) * 100.0
            span = (count / total) * 360.0
            color_hex = COLOR_PALETTE[idx % len(COLOR_PALETTE)][0]
            self._slices.append({
                "label": label,
                "count": count,
                "pct": pct,
                "start_angle": current_angle,
                "span_angle": span,
                "color": color_hex,
            })
            current_angle -= span

    def _get_slice_at(self, pos) -> str | None:
        """Return the label of the slice at position pos, or '__center__' if donut hole clicked."""
        w, h = self.width(), self.height()
        pie_size = min(h - 30, int(w * 0.42))
        cx = 20.0 + pie_size / 2.0
        cy = h / 2.0
        dx = pos.x() - cx
        dy = -(pos.y() - cy)
        dist = math.hypot(dx, dy)
        outer_r = pie_size / 2.0
        inner_r = outer_r * 0.52

        if dist < inner_r:
            return "__center__"

        if inner_r <= dist <= outer_r:
            angle = math.degrees(math.atan2(dy, dx))
            if angle < 0:
                angle += 360.0
            for sl in self._slices:
                s_ang = sl["start_angle"] % 360.0
                e_ang = (sl["start_angle"] - sl["span_angle"]) % 360.0
                matched = False
                if s_ang >= e_ang:
                    matched = (e_ang <= angle <= s_ang)
                else:
                    matched = (angle <= s_ang or angle >= e_ang)
                if matched:
                    return sl["label"]

        # Check legend clicks
        for rect, label in self._legend_rects:
            if rect.contains(int(pos.x()), int(pos.y())):
                return label

        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            label = self._get_slice_at(event.position())
            if label == "__center__":
                # Center click clears any active filter
                self.slice_clicked.emit("")
            elif label:
                if self.selected_label == label:
                    # Toggle off
                    self.slice_clicked.emit("")
                else:
                    self.slice_clicked.emit(label)

    def mouseMoveEvent(self, event):
        if not self._slices:
            return
        label = self._get_slice_at(event.position())
        if label == "__center__":
            tip = _("Click center to clear filter") if self.selected_label else _("Total Jobs")
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
            self.setCursor(Qt.PointingHandCursor)
            return

        if label:
            sl = next((s for s in self._slices if s["label"] == label), None)
            if sl:
                action = _("Click to unfilter") if self.selected_label == label else _("Click to filter dashboard")
                tip = f"{sl['label']}: {sl['count']} ({sl['pct']:.1f}%)\n{action}"
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                self.setCursor(Qt.PointingHandCursor)
                return

        self.setCursor(Qt.ArrowCursor)
        QToolTip.hideText()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if not self.items:
            painter.setPen(QColor("#9CA3AF"))
            painter.drawText(self.rect(), int(Qt.AlignCenter), _("No data available"))
            painter.end()
            return

        total = sum(cnt for _, cnt in self.items) or 1
        w, h = self.width(), self.height()

        pie_size = min(h - 30, int(w * 0.42))
        pie_rect = QRectF(20.0, (h - pie_size) / 2.0, float(pie_size), float(pie_size))

        if not self._slices:
            self._compute_slices()

        for sl in self._slices:
            s_angle = int(sl["start_angle"] * 16)
            s_span = -int(sl["span_angle"] * 16)
            is_sel = (self.selected_label == sl["label"])

            # If something is selected, fade out unselected slices slightly
            col = QColor(sl["color"])
            if self.selected_label and not is_sel:
                col.setAlpha(90)
            else:
                col.setAlpha(255)

            painter.setBrush(QBrush(col))
            if is_sel:
                painter.setPen(QPen(QColor("#1E293B"), 3))
            else:
                painter.setPen(QPen(QColor("#FFFFFF"), 2))

            painter.drawPie(pie_rect, s_angle, s_span)

        hole_size = pie_size * 0.52
        hole_rect = QRectF(
            pie_rect.center().x() - hole_size / 2.0,
            pie_rect.center().y() - hole_size / 2.0,
            hole_size,
            hole_size,
        )
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.setPen(QPen(QColor("#E5E7EB"), 1))
        painter.drawEllipse(hole_rect)

        painter.setPen(QColor("#1F2937"))
        f_center = painter.font()
        f_center.setPointSize(10)
        f_center.setBold(True)
        painter.setFont(f_center)
        if self.selected_label:
            c_text = f"{total}\n" + _("Filtered")
        else:
            c_text = f"{total}\n" + _("Jobs")
        painter.drawText(hole_rect.toRect(), int(Qt.AlignCenter), c_text)

        legend_x = int(pie_rect.right() + 24)
        legend_y = 16
        legend_w = max(100, w - legend_x - 12)
        item_h = 22

        f_leg = painter.font()
        f_leg.setPointSize(8)
        f_leg.setBold(False)
        painter.setFont(f_leg)

        self._legend_rects = []
        for sl in self._slices:
            is_sel = (self.selected_label == sl["label"])
            col = QColor(sl["color"])
            if self.selected_label and not is_sel:
                col.setAlpha(100)

            item_rect = QRect(legend_x, legend_y, legend_w, item_h)
            self._legend_rects.append((item_rect, sl["label"]))

            painter.setBrush(QBrush(col))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRect(legend_x, legend_y + 4, 12, 12), 3, 3)

            text = f"{sl['label']} ({sl['pct']:.1f}%)"
            elided = painter.fontMetrics().elidedText(text, Qt.ElideRight, legend_w - 20)
            
            if is_sel:
                painter.setPen(QColor("#1D4ED8"))
                f_bold = painter.font()
                f_bold.setBold(True)
                painter.setFont(f_bold)
            else:
                painter.setPen(QColor("#374151") if not self.selected_label else QColor("#9CA3AF"))
                f_plain = painter.font()
                f_plain.setBold(False)
                painter.setFont(f_plain)

            painter.drawText(QRect(legend_x + 18, legend_y, legend_w - 20, item_h), int(Qt.AlignVCenter | Qt.AlignLeft), elided)

            legend_y += item_h

        painter.end()


class ScrollableBarChart(QWidget):
    """Horizontal bar chart housed in a scroll area to present EVERY data point without cap.

    A bar is also a control (2026-10-28): clicking one filters the tab to that
    value, and clicking the highlighted bar again clears it - the same
    interaction the donut already had, so the two chart types cannot drift apart.
    The whole row is the hit target, not just the painted bar, because the bars
    for small values are a few pixels wide.
    """

    bar_clicked = Signal(str)

    def __init__(self, color_pair: tuple[str, str] = None, parent=None, *, filterable: bool = True):
        super().__init__(parent)
        self.color_pair = color_pair
        self.items: list[tuple[str, int]] = []
        self.filterable = filterable
        self.selected_label = ""
        self._hit_rows: list[tuple[QRect, str]] = []
        self._pressed_label = ""

    def set_data(self, items: list[tuple[str, int]], selected_label: str = ""):
        self.items = items or []
        self.selected_label = selected_label or ""
        needed_height = max(160, len(self.items) * 26 + 30)
        self.setFixedHeight(needed_height)
        self.update()

    # --- click handling ----------------------------------------------------
    def _label_at(self, point) -> str:
        for rect, label in self._hit_rows:
            if rect.contains(point):
                return label
        return ""

    def mousePressEvent(self, event):
        if self.filterable and event.button() == Qt.LeftButton:
            self._pressed_label = self._label_at(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if (self.filterable and event.button() == Qt.LeftButton and self._pressed_label
                and self._label_at(event.position().toPoint()) == self._pressed_label):
            self.bar_clicked.emit(self._pressed_label)
            self._pressed_label = ""
            event.accept()
            return
        self._pressed_label = ""
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self.filterable:
            over_bar = bool(self._label_at(event.position().toPoint()))
            self.setCursor(Qt.PointingHandCursor if over_bar else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if not self.items:
            painter.setPen(QColor("#9CA3AF"))
            painter.drawText(self.rect(), int(Qt.AlignCenter), _("No data available"))
            painter.end()
            return

        w = self.width()
        max_val = max((cnt for _, cnt in self.items), default=1) or 1
        label_col_w = min(220, int(w * 0.38))
        chart_w = max(100, w - label_col_w - 70)
        y = 10
        bar_h = 16

        self._hit_rows = []
        for idx, (label, count) in enumerate(self.items):
            start_hex, end_hex = self.color_pair if self.color_pair else COLOR_PALETTE[idx % len(COLOR_PALETTE)]
            self._hit_rows.append((QRect(0, y - 4, w, 26), label))
            if self.selected_label and label != self.selected_label:
                # Dim everything the current filter is not, so the highlighted
                # row reads as the active filter rather than as random colouring.
                painter.setOpacity(0.35)
            elif self.selected_label:
                painter.setOpacity(1.0)

            painter.setPen(QColor("#1F2937"))
            font = painter.font()
            font.setPointSize(9)
            font.setBold(False)
            painter.setFont(font)
            label_rect = QRect(8, y - 2, label_col_w - 14, bar_h + 4)
            elided = painter.fontMetrics().elidedText(label, Qt.ElideRight, label_col_w - 14)
            painter.drawText(label_rect, int(Qt.AlignVCenter | Qt.AlignLeft), elided)

            bw = max(4, int((count / max_val) * chart_w))
            bar_x = label_col_w
            bar_rect = QRect(bar_x, y, bw, bar_h)

            grad = QLinearGradient(bar_x, y, bar_x + bw, y)
            grad.setColorAt(0.0, QColor(start_hex))
            grad.setColorAt(1.0, QColor(end_hex))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(bar_rect, 4, 4)

            painter.setPen(QColor("#4B5563"))
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            cnt_rect = QRect(bar_x + bw + 8, y - 2, 50, bar_h + 4)
            painter.drawText(cnt_rect, int(Qt.AlignVCenter | Qt.AlignLeft), str(count))
            if self.selected_label and label == self.selected_label:
                painter.setPen(QColor("#1D4ED8"))
                painter.drawRect(QRect(2, y - 4, w - 5, 24))
            painter.setOpacity(1.0)

            y += 26

        painter.end()


class _InsightsRefreshWorker(QThread):
    progress_signal = Signal(int,int,str)

    def __init__(self,db_path,force,parent=None):
        super().__init__(parent)
        self.db_path,self.force = db_path,force
        self.cancel_event = threading.Event()
        self.count,self.error = 0,''

    def run(self):
        try:
            from sponsorscout.insights import queries
            self.count = queries.backfill_existing_job_terms(
                self.db_path,force_all=self.force, batch_size=128,
                progress_callback=self.progress_signal.emit,cancel_event=self.cancel_event)
        except Exception as exc:
            self.error = str(exc)
        # Charts are answered from the aggregate cube, so it has to be rebuilt
        # in the same background pass that changed the rows - never on the GUI
        # thread and never while a snapshot read is in flight.
        try:
            from sponsorscout.insights import cube
            cube.refresh(self.db_path)
        except Exception as exc:
            logger.debug('Insights cube refresh: %s', exc)


class InsightsTab(QWidget):
    """Seventh tab: Visual market intelligence and granular insights across all scanned jobs."""

    def __init__(self, db_path: str, parent=None, *, defer_load=False):
        super().__init__(parent)
        self.db_path = db_path
        self._chart_loader = AsyncLoader(self, name="Insights charts")
        self._country_loader = AsyncLoader(self, name="Insights countries")
        self._pending_loader = AsyncLoader(self, name="Insights dirty check")
        self._refresh_pending = False
        self._force_pending = False
        self._closing = False
        self._last_chart_refresh = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Filters Bar
        header_box = QGroupBox(_("Filters & Coverage"))
        h_layout = QHBoxLayout(header_box)
        h_layout.setContentsMargins(10, 10, 10, 10)
        h_layout.setSpacing(12)

        # Country Filter
        h_layout.addWidget(QLabel(_("Country:")))
        self.country_combo = QComboBox()
        self.country_combo.currentIndexChanged.connect(self.refresh)
        h_layout.addWidget(self.country_combo)

        # Source Type Filter
        h_layout.addWidget(QLabel(_("Source:")))
        self.source_combo = QComboBox()
        self.source_combo.addItem(_("All Sources"), "")
        self.source_combo.addItem(_("Direct Employers Only"), "direct")
        self.source_combo.addItem(_("Aggregators / Agencies Only"), "recruiter")
        self.source_combo.currentIndexChanged.connect(self.refresh)
        h_layout.addWidget(self.source_combo)

        # Single consolidated Refresh Insights button (re-extracts skills/domains/
        # categories from SQLite full JDs off the UI thread + reloads all charts).
        self.refresh_btn = QPushButton(_("Refresh Insights"))
        self.refresh_btn.setObjectName("Primary")
        self.refresh_btn.setToolTip(
            _("Extract skills, domains, and categories from saved job descriptions in SQLite and refresh charts")
        )
        self.refresh_btn.clicked.connect(lambda: self.refresh_from_sqlite(force_all=False))
        h_layout.addWidget(self.refresh_btn)
        self.rebuild_btn = QPushButton(_("Rebuild all"))
        self.rebuild_btn.setToolTip(_("Re-extract every saved job. Normal refresh processes only new or changed inputs."))
        self.rebuild_btn.clicked.connect(self._confirm_rebuild)
        h_layout.addWidget(self.rebuild_btn)
        self._insights_worker = None

        # Coverage Banner
        self.coverage_label = QLabel(_("Coverage: --"))
        self.coverage_label.setStyleSheet("color: #1F2937; font-weight: bold; background: #F3F4F6; padding: 4px 10px; border-radius: 4px;")
        h_layout.addStretch(1)
        h_layout.addWidget(self.coverage_label)

        root.addWidget(header_box)
        self.insights_progress_row = QWidget()
        progress_layout = QHBoxLayout(self.insights_progress_row)
        progress_layout.setContentsMargins(0,0,0,0)
        self.insights_progress_label = QLabel(_("Ready"))
        self.insights_progress = QProgressBar()
        self.insights_progress.setRange(0,100)
        self.cancel_insights_btn = QPushButton(_("Cancel refresh"))
        self.cancel_insights_btn.clicked.connect(self.cancel_refresh)
        progress_layout.addWidget(self.insights_progress_label)
        progress_layout.addWidget(self.insights_progress,1)
        progress_layout.addWidget(self.cancel_insights_btn)
        root.addWidget(self.insights_progress_row)
        self.insights_progress_row.hide()

        # Main Scrollable View with dedicated styling and smooth vertical scrolling
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        content_widget = QWidget()
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.grid = QVBoxLayout(content_widget)
        self.grid.setContentsMargins(4, 4, 4, 16)
        self.grid.setSpacing(16)
        self.scroll.setWidget(content_widget)
        root.addWidget(self.scroll, 1)

        # Row 1: Donut Pie Charts (High Level Category & Domain Breakdown)
        row_pie = QHBoxLayout()
        row_pie.setSpacing(14)

        # Active cross-filter status banner
        self.active_pie_filter: tuple[str, str] = ("", "")  # (type: "category"|"domain", value: str)

        self.pie_cat_box = QGroupBox(_("Job Role / Category Distribution (Functional Profession)"))
        self.pie_cat_box.setToolTip(_("Click any slice or legend item to filter the dashboard by this category"))
        pie_cat_lay = QVBoxLayout(self.pie_cat_box)
        self.pie_cat_chart = DonutPieChart()
        self.pie_cat_chart.slice_clicked.connect(lambda cat: self._on_pie_slice_clicked("category", cat))
        pie_cat_lay.addWidget(self.pie_cat_chart)
        row_pie.addWidget(self.pie_cat_box, 1)

        self.pie_dom_box = QGroupBox(_("Employer Industry Domain (Hiring Company Sector)"))
        self.pie_dom_box.setToolTip(_("Click any slice or legend item to filter the dashboard by this industry sector"))
        pie_dom_lay = QVBoxLayout(self.pie_dom_box)
        self.pie_dom_chart = DonutPieChart()
        self.pie_dom_chart.slice_clicked.connect(lambda dom: self._on_pie_slice_clicked("domain", dom))
        pie_dom_lay.addWidget(self.pie_dom_chart)
        row_pie.addWidget(self.pie_dom_box, 1)

        self.grid.addLayout(row_pie)

        # Interactive Filter Indicator / Reset Bar
        self.filter_indicator_widget = QWidget()
        filter_ind_lay = QHBoxLayout(self.filter_indicator_widget)
        filter_ind_lay.setContentsMargins(4, 0, 4, 0)
        self.filter_indicator_lbl = QLabel("")
        self.filter_indicator_lbl.setStyleSheet(
            "background-color: #EFF6FF; color: #1D4ED8; font-weight: bold; "
            "padding: 5px 12px; border: 1px solid #BFDBFE; border-radius: 4px;"
        )
        self.clear_pie_filter_btn = QPushButton(_("Clear Filter (Show All)"))
        self.clear_pie_filter_btn.setStyleSheet(
            "background-color: #FFFFFF; color: #DC2626; border: 1px solid #FCA5A5; "
            "padding: 4px 10px; border-radius: 4px; font-weight: bold;"
        )
        self.clear_pie_filter_btn.clicked.connect(self._clear_pie_filter)
        filter_ind_lay.addWidget(self.filter_indicator_lbl)
        filter_ind_lay.addWidget(self.clear_pie_filter_btn)
        filter_ind_lay.addStretch(1)
        self.filter_indicator_widget.setVisible(False)
        self.grid.addWidget(self.filter_indicator_widget)

                # Row 2: Granular Categories & Distinct Side-by-Side Skills Visuals
        row_bars = QHBoxLayout()
        row_bars.setSpacing(12)

        # 1. Left: Granular Categories
        self.cat_box = QGroupBox(_("Jobs by Granular Category") + _(" (All Categories)"))
        cat_vbox = QVBoxLayout(self.cat_box)
        cat_scroll = QScrollArea()
        cat_scroll.setWidgetResizable(True)
        cat_scroll.setFixedHeight(230)
        self.cat_chart = ScrollableBarChart()
        self.cat_chart.bar_clicked.connect(lambda label: self._on_bar_clicked('category', label))
        cat_scroll.setWidget(self.cat_chart)
        cat_vbox.addWidget(cat_scroll)
        row_bars.addWidget(self.cat_box, 1)

        # 2. Middle-Right: Dedicated Technical Skills Visual
        self.tech_skill_box = QGroupBox(_("In-Demand Technical Skills & Tools"))
        tech_vbox = QVBoxLayout(self.tech_skill_box)
        tech_scroll = QScrollArea()
        tech_scroll.setWidgetResizable(True)
        tech_scroll.setFixedHeight(230)
        self.tech_skill_chart = ScrollableBarChart(color_pair=("#3B82F6", "#1D4ED8"))
        self.tech_skill_chart.bar_clicked.connect(lambda label: self._on_bar_clicked('skill', label))
        tech_scroll.setWidget(self.tech_skill_chart)
        tech_vbox.addWidget(tech_scroll)
        row_bars.addWidget(self.tech_skill_box, 1)

        # 3. Far-Right: Dedicated Soft & Professional Skills Visual
        self.soft_skill_box = QGroupBox(_("In-Demand Soft & Professional Skills"))
        soft_vbox = QVBoxLayout(self.soft_skill_box)
        soft_scroll = QScrollArea()
        soft_scroll.setWidgetResizable(True)
        soft_scroll.setFixedHeight(230)
        self.soft_skill_chart = ScrollableBarChart(color_pair=("#10B981", "#047857"))
        self.soft_skill_chart.bar_clicked.connect(lambda label: self._on_bar_clicked('skill', label))
        soft_scroll.setWidget(self.soft_skill_chart)
        soft_vbox.addWidget(soft_scroll)
        row_bars.addWidget(self.soft_skill_box, 1)

        # Keep self.skill_chart alias pointing to tech_skill_chart for backwards compatibility
        self.skill_chart = self.tech_skill_chart
        self.skill_box = self.tech_skill_box

        self.grid.addLayout(row_bars)

        # Row 3: Sponsorship Breakdown Table
        self.spon_box = QGroupBox(_("Sponsorship by Category"))
        spon_vbox = QVBoxLayout(self.spon_box)
        self.spon_table = QTableWidget(0, 5)
        self.spon_table.setHorizontalHeaderLabels([
            _("Category"), _("Total"), _("Visa Yes"), _("Visa ?"), _("Reloc Yes")
        ])
        self.spon_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.spon_table.verticalHeader().setVisible(False)
        self.spon_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.spon_table.setAlternatingRowColors(True)
        self.spon_table.setMinimumHeight(260)
        self.spon_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        spon_vbox.addWidget(self.spon_table)
        self.grid.addWidget(self.spon_box)

        # Disclaimer
        note = QLabel(_("* Insights reflect scanned job portals in SponsorScout's database, not the entire employment market."))
        note.setStyleSheet("color: #6B7280; font-size: 11px; font-style: italic;")
        self.grid.addWidget(note)

        if not defer_load:
            self._populate_countries()
            self.refresh()

    def _populate_countries(self):
        from sponsorscout.db.database import get_distinct_job_countries
        path = self.db_path
        self._country_loader.request(lambda: get_distinct_job_countries(path), self._apply_countries)

    def _apply_countries(self, countries):
        current = self.country_combo.currentText()
        self.country_combo.blockSignals(True)
        self.country_combo.clear()
        self.country_combo.addItem(_("All Countries"))
        self.country_combo.addItems([c for c in countries if c])
        index = self.country_combo.findText(current)
        if index >= 0:
            self.country_combo.setCurrentIndex(index)
        self.country_combo.blockSignals(False)

    def request_auto_refresh(self):
        if self._closing:
            return
        from sponsorscout.insights import queries
        path = self.db_path
        self._pending_loader.request(lambda: queries.has_pending_insights(path),
            lambda pending: self.refresh_from_sqlite(force_all=False) if pending and not self._closing else None)

    def retranslate(self):
        from sponsorscout.i18n import retranslate_widget_literals
        retranslate_widget_literals(self)
        self.country_combo.blockSignals(True)
        if self.country_combo.count():
            self.country_combo.setItemText(0,_('All Countries'))
        self.country_combo.blockSignals(False)
        self.source_combo.blockSignals(True)
        for index,key in enumerate(('All Sources','Direct Employers Only','Aggregators / Agencies Only')):
            self.source_combo.setItemText(index,_(key))
        self.source_combo.blockSignals(False)
        self.spon_table.setHorizontalHeaderLabels(
            [_('Category'),_('Total'),_('Visa Yes'),_('Visa ?'),_('Reloc Yes')])
        self.refresh(auto_backfill=False)  # charts only, no hidden forced rebuild

    def _confirm_rebuild(self):
        if QMessageBox.question(self,_("Rebuild Insights"),
            _("Re-extract all saved jobs? Previously committed results remain visible during the rebuild.")) == QMessageBox.Yes:
            self.refresh_from_sqlite(force_all=True)

    def cancel_refresh(self):
        self._refresh_pending = self._force_pending = False
        if self._insights_worker is not None:
            self._insights_worker.cancel_event.set()
            self.insights_progress_label.setText(_("Cancelling refresh... Saved batches are retained."))
            self.cancel_insights_btn.setEnabled(False)

    def shutdown(self,wait_ms=3000):
        self._closing = True
        for loader in (self._chart_loader, self._country_loader, self._pending_loader):
            loader.close()
        self.cancel_refresh()
        return self._insights_worker is None or self._insights_worker.wait(wait_ms)

    def refresh_from_sqlite(self, force_all: bool = False):
        """One background worker; coalesce changes arriving during a refresh."""
        if self._closing:
            return
        if self._insights_worker is not None:
            self._refresh_pending = True
            self._force_pending = self._force_pending or force_all
            return
        self.refresh_btn.setEnabled(False)
        self.rebuild_btn.setEnabled(False)
        self.refresh_btn.setText(_("Refreshing Insights..."))
        self.cancel_insights_btn.setEnabled(True)
        self.insights_progress_row.show()
        worker = _InsightsRefreshWorker(self.db_path,force_all,self)
        self._insights_worker = worker
        worker.progress_signal.connect(self._on_insights_progress)
        # Use QThread.finished, not a custom signal emitted before run returns.
        worker.finished.connect(lambda: self._finish_insights_worker(worker))
        worker.start()

    def _on_insights_progress(self,done,total,phase):
        self.insights_progress.setRange(0,max(1,total))
        self.insights_progress.setValue(done)
        if phase == 'waiting':
            text = _("Waiting for ingestion; {done} / {total} processed")
        else:
            text = _("Processed {done} / {total} jobs")
        self.insights_progress_label.setText(text.format(done=done,total=total))
        now = time.monotonic()
        if phase == 'saved' and now-self._last_chart_refresh >= 2:
            self._last_chart_refresh = now
            self.refresh(auto_backfill=False)  # committed coverage, not 0% until the end

    def _finish_insights_worker(self,worker):
        if self._insights_worker is not worker:
            return
        self._insights_worker = None
        self.refresh_btn.setEnabled(True)
        self.rebuild_btn.setEnabled(True)
        self.refresh_btn.setText(_("Refresh Insights"))
        self.cancel_insights_btn.setEnabled(False)
        cancelled = worker.cancel_event.is_set()
        error = worker.error
        if error:
            logger.warning('Insights background refresh failed: %s',error)
            self.insights_progress_label.setText(_("Insights error: {error}").format(error=error))
        elif cancelled:
            self.insights_progress_label.setText(_("Refresh cancelled. Committed batches are saved."))
        else:
            self.insights_progress_label.setText(_("Insights up to date. Updated {count} jobs.").format(count=worker.count))
        worker.deleteLater()
        self._populate_countries()
        self.refresh(auto_backfill=False)
        # Keep the completion message visible briefly, then retire the progress
        # row so a finished refresh does not look like an active one.
        QTimer.singleShot(2500, lambda: self.insights_progress_row.hide()
                          if self._insights_worker is None and not self._closing else None)
        rerun, force = self._refresh_pending, self._force_pending
        self._refresh_pending = self._force_pending = False
        if not error and not cancelled and not self._closing:
            if rerun and force:
                QTimer.singleShot(0, lambda: self.refresh_from_sqlite(force_all=True))
            else:
                self.request_auto_refresh()  # pending check is also off the GUI thread

    def _on_chart_filter_clicked(self, filter_type: str, label: str):
        """One active filter for the whole tab: category, domain or skill term.

        Clicking the already-selected slice/bar clears it. Only one filter is
        active at a time on purpose - the panels would otherwise be reading a
        population the filter bar cannot describe.
        """
        if not label or (self.active_pie_filter[0] == filter_type and self.active_pie_filter[1] == label):
            self.active_pie_filter = ("", "")
        else:
            self.active_pie_filter = (filter_type, label)
        self.refresh(auto_backfill=False)

    def _on_pie_slice_clicked(self, filter_type: str, label: str):
        """Slice/legend click in a Category or Domain donut."""
        self._on_chart_filter_clicked(filter_type, label)

    def _on_bar_clicked(self, filter_type: str, label: str):
        """Bar click in the category or skills charts (ScrollableBarChart)."""
        self._on_chart_filter_clicked(filter_type, label)

    def _clear_pie_filter(self):
        self.active_pie_filter = ("", "")
        self.refresh(auto_backfill=False)

    def refresh(self, *_args, auto_backfill: bool = True):
        if self._closing:
            return
        from sponsorscout.insights import queries
        country = self.country_combo.currentText()
        if country == _("All Countries") or not country:
            country = None
        source_sub = self.source_combo.currentData() or None
        f_type, f_val = self.active_pie_filter
        cat_filter = f_val if f_type == 'category' else ''
        dom_filter = f_val if f_type == 'domain' else ''
        # A technical/soft skill bar carries the term itself, which filters every
        # panel to the jobs where that term was actually extracted (job_terms).
        skill_filter = f_val if f_type == 'skill' else ''
        path = self.db_path
        def load():
            result = {}
            calls = (
                ('cov', lambda: queries.get_insights_coverage(path, country=country, source_subtype=source_sub, category=cat_filter, domain=dom_filter, skill_term=skill_filter)),
                ('cats', lambda: queries.get_jobs_by_category(path, country=country, source_subtype=source_sub, domain=dom_filter, skill_term=skill_filter)),
                ('skills_dict', lambda: queries.get_segregated_skills(path, country=country, source_subtype=source_sub, category=cat_filter, domain=dom_filter, skill_term=skill_filter)),
                ('domains', lambda: queries.get_jobs_by_domain(path, country=country, source_subtype=source_sub, category=cat_filter, skill_term=skill_filter)),
                ('spon_data', lambda: queries.get_sponsorship_by_category(path, country=country, source_subtype=source_sub, category=cat_filter, domain=dom_filter, skill_term=skill_filter)))
            for key, function in calls:
                try:
                    result[key] = function()
                except Exception as exc:
                    result[key] = None
                    logger.debug('Insights snapshot %s: %s', key, exc)
            result['pending'] = bool(auto_backfill and queries.has_pending_insights(path))
            return result
        self._chart_loader.request(load, lambda snapshot: self._apply_charts(snapshot, f_type, f_val))

    def _apply_charts(self, snapshot, f_type, f_val):
        if self._closing:
            return
        # Filters are computed here, not only in refresh(), because this
        # callback receives f_type/f_val rather than the two derived strings.
        cat_filter = f_val if f_type == 'category' else ''
        dom_filter = f_val if f_type == 'domain' else ''
        skill_filter = f_val if f_type == 'skill' else ''
        if snapshot.get('pending'):
            self.refresh_from_sqlite(force_all=False)
        # Update Filter Indicator Bar
        if f_val:
            type_name = (_("Category") if f_type == "category"
                         else _("Skill") if f_type == "skill" else _("Employer Domain"))
            self.filter_indicator_lbl.setText(_("Filtered by %s: %s") % (type_name, f_val))
            self.filter_indicator_widget.setVisible(True)
        else:
            self.filter_indicator_widget.setVisible(False)

        def _scoped_title(base: str, active: str) -> str:
            # A panel title that still says "(All Categories)" while one slice is
            # highlighted is how a filter starts looking like broken data.
            return base + (_(" (All Categories)") if not (cat_filter or dom_filter or skill_filter)
                           else _(" (Filtered: %s)") % (active or cat_filter or dom_filter or skill_filter))
        self.cat_box.setTitle(_scoped_title(_("Jobs by Granular Category"), cat_filter))
        self.tech_skill_box.setTitle(_scoped_title(_("In-Demand Technical Skills & Tools"), skill_filter))
        self.soft_skill_box.setTitle(_scoped_title(_("In-Demand Soft & Professional Skills"), skill_filter))

        # 1. Coverage
        try:
            cov = snapshot['cov']
            tot = cov["total_jobs"]
            cat_pct = (cov["categorized_jobs"] / tot * 100) if tot else 0.0
            skl_pct = (cov["jobs_with_skills"] / tot * 100) if tot else 0.0
            self.coverage_label.setText(
                _("Categorised: %d / %d (%.1f%%) | With Skills: %d (%.1f%%)")
                % (
                    cov["categorized_jobs"],
                    tot,
                    cat_pct,
                    cov["jobs_with_skills"],
                    skl_pct,
                )
            )
        except Exception as e:
            logger.debug("Error getting coverage: %s", e)

        # 2. Categories & Category Pie Chart
        try:
            cats = snapshot['cats']
            self.cat_chart.set_data(cats, selected_label=cat_filter)
            self.pie_cat_chart.set_data(cats, selected_label=cat_filter)
        except Exception as e:
            logger.debug("Error getting categories: %s", e)

        # 3. Top Skills (Distinct Technical & Soft Visuals, filtered by selected category/domain)
        try:
            skills_dict = snapshot['skills_dict']
            self.tech_skill_chart.set_data(skills_dict.get("technical", []), selected_label=skill_filter)
            self.soft_skill_chart.set_data(skills_dict.get("soft", []), selected_label=skill_filter)
        except Exception as e:
            logger.debug("Error getting skills: %s", e)

        # 4. Employer Domains & Domain Pie Chart
        try:
            domains = snapshot['domains']
            self.pie_dom_chart.set_data(domains, selected_label=dom_filter)
        except Exception as e:
            logger.debug("Error getting domains: %s", e)

        # 5. Sponsorship Table (filtered by selected category/domain)
        try:
            spon_data = snapshot['spon_data']
            self.spon_table.setRowCount(len(spon_data))
            for i, row in enumerate(spon_data):
                item_cat = QTableWidgetItem(str(row["category"]))
                f_cat = item_cat.font()
                f_cat.setBold(True)
                item_cat.setFont(f_cat)

                item_tot = QTableWidgetItem(str(row["total"]))
                item_tot.setTextAlignment(int(Qt.AlignCenter))

                item_vy = QTableWidgetItem(str(row["visa_yes"]))
                item_vy.setTextAlignment(int(Qt.AlignCenter))
                item_vy.setForeground(QColor("#059669"))  # emerald

                item_vu = QTableWidgetItem(str(row["visa_unk"]))
                item_vu.setTextAlignment(int(Qt.AlignCenter))
                item_vu.setForeground(QColor("#D97706"))  # amber

                item_ry = QTableWidgetItem(str(row["reloc_yes"]))
                item_ry.setTextAlignment(int(Qt.AlignCenter))
                item_ry.setForeground(QColor("#2563EB"))  # blue

                self.spon_table.setItem(i, 0, item_cat)
                self.spon_table.setItem(i, 1, item_tot)
                self.spon_table.setItem(i, 2, item_vy)
                self.spon_table.setItem(i, 3, item_vu)
                self.spon_table.setItem(i, 4, item_ry)

            # Dynamically size spon_table to show rows cleanly
            row_count = len(spon_data)
            header_h = self.spon_table.horizontalHeader().height() or 30
            row_h = self.spon_table.rowHeight(0) if row_count > 0 else 28
            calc_height = header_h + (row_count * row_h) + 20
            self.spon_table.setFixedHeight(max(260, min(550, calc_height)))
        except Exception as e:
            logger.debug("Error getting sponsorship table: %s", e)

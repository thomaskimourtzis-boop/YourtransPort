from __future__ import annotations
import sys
import os
import subprocess
import glob
import json
import calendar
from dataclasses import dataclass, field, field
from datetime import date, datetime
from typing import Optional, List, Dict, Any, Callable

from PySide6.QtCore import Qt, QTimer, QStandardPaths, QUrl, QCoreApplication
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox,
    QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QAbstractItemView, QHeaderView, QGroupBox,
    QDialog, QStackedWidget, QComboBox, QTabWidget,
    QFormLayout, QScrollArea, QFrame,
)

ORG_NAME = "YOURTRANS"
APP_NAME = "YOURTRANS OIL"

DEBUG = False  # set True to enable console logs


QCoreApplication.setOrganizationName(ORG_NAME)
QCoreApplication.setApplicationName(APP_NAME)

# -----------------------------
# Storage (Auto-save JSON)
# -----------------------------

def data_dir() -> str:
    """Επιστρέφει τον φάκελο δεδομένων της εφαρμογής και τον δημιουργεί αν λείπει."""
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not base:
        # Fallback (σπάνιο): φτιάξε φάκελο στο home
        base = os.path.expanduser(os.path.join("~", f".{APP_NAME.lower().replace(' ', '_')}_data"))
    # Βεβαιώσου ότι είναι ο σωστός φάκελος της εφαρμογής
    os.makedirs(base, exist_ok=True)
    return base


def app_data_file() -> str:
    """Κύριο αρχείο αποθήκευσης."""
    return os.path.join(data_dir(), "pratirio_data.json")


def open_data_folder_in_finder():
    """Ανοίγει τον φάκελο δεδομένων της εφαρμογής στον file explorer (Windows/macOS/Linux)."""
    folder = os.path.normpath(data_dir())
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass

    # Αν για κάποιο λόγο δεν δημιουργήθηκε, μην προσπαθήσεις να τον ανοίξεις τυφλά
    if not os.path.isdir(folder):
        QMessageBox.warning(None, "Φάκελος δεδομένων", f"Δεν βρέθηκε/δημιουργήθηκε ο φάκελος:\n{folder}")
        return

    try:
        if sys.platform.startswith("win"):
            # πιο αξιόπιστο στο Windows από το startfile
            subprocess.Popen(["explorer", folder])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        # Fallback μέσω Qt
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


def safe_read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def safe_write_json(path: str, data: Dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# -----------------------------
# Core logic (Πρατήριο / Τόκοι)
# -----------------------------

def parse_date(s: str) -> date:
    s = s.strip()
    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%d-%m-%Y",
        "%d-%m-%y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError("Μη έγκυρη ημερομηνία. Δεκτές μορφές: 2025-02-12 ή 12/02/2025 ή 12/2/25")


def add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    day = min(d.day, last_day)
    return date(y, m, day)


def pct_to_rate(pct: float) -> float:
    return pct / 100.0


def parse_amount_eur(s: str) -> float:
    s = s.strip()
    if not s:
        raise ValueError("Κενό ποσό")

    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")

    return float(s)


def fmt_eur(x: float) -> str:
    s = f"{x:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " €"


def fmt_date(d: Optional[date]) -> str:
    if d is None:
        return ""
    return d.strftime("%d/%m/%Y")


@dataclass
class Customer:
    cid: int
    name: str
    afm: str = ""
    phone: str = ""
    notes: str = ""


@dataclass
class Invoice:
    invoice_no: str
    amount: float
    issue_date: date
    credit_months: int
    paid_date: Optional[date] = None
    annual_rate: Optional[float] = None  # π.χ. 0.06
    customer_id: Optional[int] = None


def calc_interest(inv: Invoice, default_rate: float, as_of: date):
    due_date = add_months(inv.issue_date, inv.credit_months)
    end_date = inv.paid_date or as_of

    delay_days = (end_date - due_date).days
    if delay_days < 0:
        delay_days = 0

    rate = inv.annual_rate if inv.annual_rate is not None else default_rate
    interest = inv.amount * rate * (delay_days / 365.0)
    return due_date, end_date, delay_days, rate, interest


# -----------------------------
# Model (shared station data)
# -----------------------------

class StationModel:
    """
    Κοινά δεδομένα Πρατηρίου:
    - customers
    - invoices
    - settings (default_rate_pct, default_credit_months, as_of)
    Auto-save/Auto-load σε JSON.
    """
    def __init__(self):
        self.path = app_data_file()
        self.invoices: List[Invoice] = []
        self.customers: List[Customer] = []
        self.next_customer_id: int = 1

        self.default_rate_pct: float = 6.0
        self.default_credit_months: int = 5
        self.as_of: str = date.today().strftime("%Y-%m-%d")

        self._save_timer: Optional[QTimer] = None

    def attach_autosave_timer(self, owner_widget: QWidget, save_now_cb: Callable[[], None]):
        t = QTimer(owner_widget)
        t.setSingleShot(True)
        t.timeout.connect(save_now_cb)
        self._save_timer = t

    def schedule_save(self, ms: int = 300):
        if self._save_timer is None:
            return
        self._save_timer.start(ms)

    def save_now(self):
        data = {
            "settings": {
                "default_rate_pct": float(self.default_rate_pct),
                "default_credit_months": int(self.default_credit_months),
                "as_of": str(self.as_of).strip(),
            },
            "customers": [self.customer_to_dict(c) for c in self.customers],
            "next_customer_id": int(self.next_customer_id),
            "invoices": [self.invoice_to_dict(inv) for inv in self.invoices],
        }
        safe_write_json(self.path, data)

    def load(self):
        data = safe_read_json(self.path)
        if not data:
            if DEBUG: print('[Trucks][load] no data at path', self.path)
            return
        if isinstance(data, dict):
            if DEBUG: print('[Trucks][load] top-level keys:', list(data.keys()
))
        else:
            if DEBUG: print('[Trucks][load] unexpected JSON type:', type(data)
)

        s = data.get("settings", {})
        try:
            if "default_rate_pct" in s:
                self.default_rate_pct = float(s["default_rate_pct"])
            if "default_credit_months" in s:
                self.default_credit_months = int(s["default_credit_months"])
            if "as_of" in s and str(s["as_of"]).strip():
                self.as_of = str(s["as_of"]).strip()
        except Exception:
            pass

        # customers
        self.customers = []
        try:
            self.next_customer_id = int(data.get("next_customer_id", 1))
        except Exception:
            self.next_customer_id = 1

        for obj in data.get("customers", []) or []:
            c = self.dict_to_customer(obj)
            if c is not None and c.name:
                self.customers.append(c)

        if self.customers:
            try:
                self.next_customer_id = max(self.next_customer_id, max(c.cid for c in self.customers) + 1)
            except Exception:
                pass

        invs = data.get("invoices", [])
        loaded: List[Invoice] = []
        for obj in invs:
            inv = self.dict_to_invoice(obj)
            if inv is not None:
                loaded.append(inv)
        self.invoices = loaded

    @staticmethod
    def invoice_to_dict(inv: Invoice) -> Dict[str, Any]:
        return {
            "invoice_no": inv.invoice_no,
            "amount": inv.amount,
            "issue_date": inv.issue_date.isoformat(),
            "credit_months": inv.credit_months,
            "paid_date": inv.paid_date.isoformat() if inv.paid_date else None,
            "annual_rate": inv.annual_rate,
            "customer_id": inv.customer_id,
        }

    @staticmethod
    def dict_to_invoice(obj: Dict[str, Any]) -> Optional[Invoice]:
        try:
            invoice_no = str(obj.get("invoice_no", "")).strip()
            amount = float(obj["amount"])
            issue_date = date.fromisoformat(obj["issue_date"])
            credit_months = int(obj.get("credit_months", 0))
            paid_date = obj.get("paid_date")
            if paid_date:
                paid_date = date.fromisoformat(paid_date)
            else:
                paid_date = None
            annual_rate = obj.get("annual_rate")
            if annual_rate is not None:
                annual_rate = float(annual_rate)

            customer_id = obj.get("customer_id")
            if customer_id is not None:
                try:
                    customer_id = int(customer_id)
                except Exception:
                    customer_id = None

            return Invoice(
                invoice_no=invoice_no,
                amount=amount,
                issue_date=issue_date,
                credit_months=credit_months,
                paid_date=paid_date,
                annual_rate=annual_rate,
                customer_id=customer_id,
            )
        except Exception:
            return None

    # -----------------------------
    # Invoice number checks
    # -----------------------------

    @staticmethod
    def normalize_invoice_no(s: str) -> str:
        # normalize για "2026-001" == " 2026-001 "
        return (s or "").strip()

    def invoice_no_exists(self, invoice_no: str, exclude_index: Optional[int] = None) -> bool:
        n = self.normalize_invoice_no(invoice_no)
        if not n:
            return False
        for i, inv in enumerate(self.invoices):
            if exclude_index is not None and i == exclude_index:
                continue
            if self.normalize_invoice_no(inv.invoice_no) == n:
                return True
        return False

    # -----------------------------
    # Customers helpers
    # -----------------------------

    @staticmethod
    def customer_to_dict(c: Customer) -> Dict[str, Any]:
        return {"cid": c.cid, "name": c.name, "afm": c.afm, "phone": c.phone, "notes": c.notes}

    @staticmethod
    def dict_to_customer(obj: Dict[str, Any]) -> Optional[Customer]:
        try:
            return Customer(
                cid=int(obj["cid"]),
                name=str(obj.get("name", "")).strip(),
                afm=str(obj.get("afm", "")).strip(),
                phone=str(obj.get("phone", "")).strip(),
                notes=str(obj.get("notes", "")).strip(),
            )
        except Exception:
            return None

    def customer_name(self, cid: Optional[int]) -> str:
        if cid is None:
            return "-"
        for c in self.customers:
            if c.cid == cid:
                return c.name
        return "(άγνωστος)"

    def add_customer(self, name: str, afm: str = "", phone: str = "", notes: str = "") -> Customer:
        c = Customer(
            cid=self.next_customer_id,
            name=name.strip(),
            afm=afm.strip(),
            phone=phone.strip(),
            notes=notes.strip(),
        )
        self.customers.append(c)
        self.next_customer_id += 1
        return c

    def has_invoices_for_customer(self, cid: int) -> bool:
        return any(inv.customer_id == cid for inv in self.invoices)


# -----------------------------
# UI helpers
# -----------------------------

def set_label_role(label: QLabel, role: str):
    label.setProperty("role", role)


def set_button_role(btn: QPushButton, role: str):
    btn.setProperty("role", role)
    btn.setCursor(Qt.PointingHandCursor)


def style_action_buttons(*buttons: QPushButton):
    for btn in buttons:
        txt = (btn.text() or "").strip().lower()
        if "διαγραφ" in txt or "🗑" in txt:
            set_button_role(btn, "danger")
        elif "προσθήκ" in txt or "εφαρμογ" in txt or "επανυπολογ" in txt:
            set_button_role(btn, "primary")
        elif "καθαρισ" in txt or "νέο" in txt:
            set_button_role(btn, "soft")
        else:
            set_button_role(btn, "secondary")


# -----------------------------
# Start dialog (section)
# -----------------------------

class StartDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YOURTRANS OIL — Επιλογή")
        self.setModal(True)
        self.choice: Optional[str] = None  # "trucks" ή "station"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        self.setObjectName("StartDialog")

        title = QLabel("Διάλεξε ενότητα:")
        title.setAlignment(Qt.AlignCenter)
        set_label_role(title, "section-title")
        layout.addWidget(title)

        btns = QHBoxLayout()
        btns.setSpacing(10)

        self.trucks_btn = QPushButton("🚚 Φορτηγά")
        self.station_btn = QPushButton("⛽ Πρατήριο")

        self.trucks_btn.setMinimumHeight(70)
        self.station_btn.setMinimumHeight(70)
        set_button_role(self.trucks_btn, "choice")
        set_button_role(self.station_btn, "choice")

        self.trucks_btn.clicked.connect(self._choose_trucks)
        self.station_btn.clicked.connect(self._choose_station)

        btns.addWidget(self.trucks_btn)
        btns.addWidget(self.station_btn)
        layout.addLayout(btns)

        hint = QLabel("Το Πρατήριο έχει Καταχώρηση & Τόκους, με autosave σε JSON.")
        hint.setAlignment(Qt.AlignCenter)
        set_label_role(hint, "subtitle")
        layout.addWidget(hint)

        self.resize(560, 220)

    def _choose_trucks(self):
        self.choice = "trucks"
        self.accept()

    def _choose_station(self):
        self.choice = "station"
        self.accept()


# -----------------------------
# App controller
# -----------------------------

class AppController:
    def __init__(self):
        self.station_model = StationModel()
        self.station_model.load()

        self.start_dialog: Optional[StartDialog] = None
        self.main_window: Optional[QMainWindow] = None

    def start(self):
        self.start_dialog = StartDialog()
        if self.start_dialog.exec() != QDialog.Accepted:
            return

        if self.start_dialog.choice == "station":
            self.main_window = StationWindow(self, self.station_model)
        else:
            self.main_window = TruckWindow(self)

        self.main_window.show()

    def go_to_station(self):
        if self.main_window:
            self.main_window.close()
        self.main_window = StationWindow(self, self.station_model)
        self.main_window.show()

    def go_to_trucks(self):
        if self.main_window:
            self.main_window.close()
        self.main_window = TruckWindow(self)
        self.main_window.show()


# -----------------------------
# Reusable top bar
# -----------------------------

def make_section_bar(controller: AppController, current: str) -> QWidget:
    bar = QWidget()
    bar.setObjectName("TopSwitchBar")
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)

    btn_trucks = QPushButton("🚚 Φορτηγά")
    btn_station = QPushButton("⛽ Πρατήριο")
    set_button_role(btn_trucks, "nav")
    set_button_role(btn_station, "nav")

    btn_trucks.setEnabled(current != "trucks")
    btn_station.setEnabled(current != "station")

    btn_trucks.clicked.connect(controller.go_to_trucks)
    btn_station.clicked.connect(controller.go_to_station)

    layout.addWidget(btn_trucks)
    layout.addWidget(btn_station)
    layout.addStretch(1)
    return bar


# -----------------------------
# Station nav bar
# -----------------------------

def make_station_nav(on_go_entry: Callable[[], None],
                     on_go_interest: Callable[[], None],
                     on_go_customers: Callable[[], None],
                     current: str) -> QWidget:
    bar = QWidget()
    bar.setObjectName("ModuleNavBar")
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)

    btn_entry = QPushButton("🧾 Καταχώρηση")
    btn_interest = QPushButton("💶 Τόκοι")
    btn_customers = QPushButton("👤 Πελάτες")
    set_button_role(btn_entry, "nav")
    set_button_role(btn_interest, "nav")
    set_button_role(btn_customers, "nav")

    btn_entry.setEnabled(current != "entry")
    btn_interest.setEnabled(current != "interest")
    btn_customers.setEnabled(current != "customers")

    btn_entry.clicked.connect(on_go_entry)
    btn_interest.clicked.connect(on_go_interest)
    btn_customers.clicked.connect(on_go_customers)

    layout.addWidget(btn_entry)
    layout.addWidget(btn_interest)
    layout.addWidget(btn_customers)
    layout.addStretch(1)

    return bar


# -----------------------------
# Station window (contains pages)
# -----------------------------

class StationWindow(QMainWindow):
    def __init__(self, controller: AppController, model: StationModel):
        super().__init__()
        self.controller = controller
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0

        self.setWindowTitle("YOURTRANS OIL — Πρατήριο")
        self.resize(1150, 720)

        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(14, 14, 14, 14)
        main.setSpacing(12)
        main.setContentsMargins(14, 14, 14, 14)
        main.setSpacing(12)

        main.addWidget(make_section_bar(self.controller, current="station"))

        tools = QWidget()
        tools.setObjectName("ToolsBar")
        tools_layout = QHBoxLayout(tools)
        tools_layout.setContentsMargins(10, 8, 10, 8)
        tools_layout.setSpacing(8)

        open_folder_btn = QPushButton("🗂 Άνοιγμα φακέλου δεδομένων")
        set_button_role(open_folder_btn, "secondary")
        open_folder_btn.clicked.connect(open_data_folder_in_finder)

        tools_layout.addWidget(open_folder_btn)
        tools_layout.addStretch(1)
        main.addWidget(tools)

        self.stack = QStackedWidget()
        self.entry_page = StationEntryPage(self.model, on_data_changed=self._on_data_changed)
        self.interest_page = StationInterestPage(self.model)
        self.customers_page = StationCustomersPage(self.model, on_data_changed=self._on_data_changed)

        self.stack.addWidget(self.entry_page)
        self.stack.addWidget(self.interest_page)
        self.stack.addWidget(self.customers_page)

        self.model.attach_autosave_timer(self, self._save_now)

        self.nav_holder = QWidget()
        self.nav_layout = QVBoxLayout(self.nav_holder)
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(8)

        self._set_station_page("entry")

        main.addWidget(self.nav_holder)
        main.addWidget(self.stack, 1)

    def _save_now(self):
        self.model.save_now()

    def _on_data_changed(self):
        self.interest_page.refresh()
        self.model.schedule_save()

    def _set_station_page(self, which: str):
        for i in reversed(range(self.nav_layout.count())):
            item = self.nav_layout.itemAt(i)
            w = item.widget()
            if w:
                w.setParent(None)

        nav = make_station_nav(
            on_go_entry=lambda: self._set_station_page("entry"),
            on_go_interest=lambda: self._set_station_page("interest"),
            on_go_customers=lambda: self._set_station_page("customers"),
            current=which
        )
        self.nav_layout.addWidget(nav)

        if which == "entry":
            self.stack.setCurrentIndex(0)
            self.entry_page.refresh()
        elif which == "interest":
            self.stack.setCurrentIndex(1)
            self.interest_page.refresh()
        else:
            self.stack.setCurrentIndex(2)
            self.customers_page.refresh()
            self.entry_page.refresh()

    def closeEvent(self, event):
        try:
            self.model.save_now()
        except Exception:
            pass
        super().closeEvent(event)


# -----------------------------
# Station: Entry Page (Καταχώρηση)
# -----------------------------

class StationEntryPage(QWidget):
    def refresh(self):
        """Early-defined safe refresh to avoid AttributeError during init.
        It can be overwritten later in the class; if not, this no-op keeps startup stable.
        """
        try:
            # If a later refresh was defined, it will override this method.
            pass
        except Exception:
            pass

    def _early_refresh(self):
        try:
            self.refresh()
        except Exception:
            pass

    def __init__(self, model: StationModel, on_data_changed: Callable[[], None]):
        super().__init__()
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0
        self.on_data_changed = on_data_changed

        main = QVBoxLayout(self)

        settings_box = QGroupBox("Ρυθμίσεις Καταχώρησης")
        sgrid = QGridLayout(settings_box)

        self.default_credit_months = QSpinBox()
        self.default_credit_months.setRange(0, 60)
        self.default_credit_months.setValue(int(self.model.default_credit_months))

        sgrid.addWidget(QLabel("Default πίστωση (μήνες):"), 0, 0)
        sgrid.addWidget(self.default_credit_months, 0, 1)
        sgrid.setColumnStretch(2, 1)

        main.addWidget(settings_box)

        entry_box = QGroupBox("Καταχώρηση Τιμολογίου")
        egrid = QGridLayout(entry_box)

        self.invoice_no_edit = QLineEdit()
        self.invoice_no_edit.setPlaceholderText("π.χ. 2026-00123")

        self.customer_combo = QComboBox()
        self.customer_combo.setMinimumWidth(260)

        self.amount_edit = QLineEdit()
        self.amount_edit.setPlaceholderText("π.χ. 1200,50 ή 1.200,50")

        self.issue_edit = QLineEdit()
        self.issue_edit.setPlaceholderText("π.χ. 12/2/25 ή 2025-02-12")

        self.credit_spin = QSpinBox()
        self.credit_spin.setRange(0, 60)
        self.credit_spin.setValue(int(self.model.default_credit_months))

        self.paid_edit = QLineEdit()
        self.paid_edit.setPlaceholderText("προαιρετικό (π.χ. 10/01/2026)")

        self.rate_override_pct = QDoubleSpinBox()
        self.rate_override_pct.setRange(0.0, 200.0)
        self.rate_override_pct.setDecimals(2)
        self.rate_override_pct.setSuffix(" %")
        self.rate_override_pct.setValue(0.0)

        self.use_override_btn = QPushButton("Επιτόκιο τιμολογίου: OFF")
        self.use_override_btn.setCheckable(True)
        set_button_role(self.use_override_btn, "soft")
        self.use_override_btn.toggled.connect(self._toggle_override)

        egrid.addWidget(QLabel("Αρ. Τιμολογίου:"), 0, 0)
        egrid.addWidget(self.invoice_no_edit, 0, 1)

        egrid.addWidget(QLabel("Πελάτης:"), 0, 2)
        egrid.addWidget(self.customer_combo, 0, 3, 1, 3)

        egrid.addWidget(QLabel("Ποσό (€):"), 1, 0)
        egrid.addWidget(self.amount_edit, 1, 1)
        egrid.addWidget(QLabel("Ημ/νία έκδοσης:"), 1, 2)
        egrid.addWidget(self.issue_edit, 1, 3)
        egrid.addWidget(QLabel("Πίστωση (μήνες):"), 1, 4)
        egrid.addWidget(self.credit_spin, 1, 5)

        egrid.addWidget(QLabel("Ημ/νία πληρωμής:"), 2, 0)
        egrid.addWidget(self.paid_edit, 2, 1)
        egrid.addWidget(QLabel("Επιτόκιο τιμολογίου:"), 2, 2)
        egrid.addWidget(self.rate_override_pct, 2, 3)
        egrid.addWidget(self.use_override_btn, 2, 4, 1, 2)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("➕ Προσθήκη")
        self.update_btn = QPushButton("✏️ Ενημέρωση")
        self.delete_btn = QPushButton("🗑️ Διαγραφή")
        self.clear_btn = QPushButton("🧹 Καθαρισμός")
        style_action_buttons(self.add_btn, self.update_btn, self.delete_btn, self.clear_btn)

        self.add_btn.clicked.connect(self.add_invoice)
        self.update_btn.clicked.connect(self.update_selected)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.clear_btn.clicked.connect(self.clear_fields)

        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.update_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.clear_btn)

        egrid.addLayout(btn_row, 3, 0, 1, 6)

        main.addWidget(entry_box)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "#", "Αρ. Τιμ.", "Πελάτης", "Ποσό", "Έκδοση", "Πίστωση", "Πληρωμή", "Επιτόκιο τιμ.", "Κατάσταση"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_select_row)

        main.addWidget(self.table, 1)

        self.default_credit_months.valueChanged.connect(self._default_credit_changed)

        self._toggle_override(False)
        self.refresh()

    def _default_credit_changed(self, v: int):
        self.model.default_credit_months = int(v)
        self.credit_spin.setValue(int(v))
        self.model.schedule_save()
        self.on_data_changed()

    def _toggle_override(self, checked: bool):
        self.rate_override_pct.setEnabled(checked)
        self.use_override_btn.setText(f"Επιτόκιο τιμολογίου: {'ON' if checked else 'OFF'}")

    def _refresh_customers_combo(self):
        current_cid = self.customer_combo.currentData()
        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        self.customer_combo.addItem("— Χωρίς πελάτη —", None)
        for c in self.model.customers:
            label = c.name if not c.afm else f"{c.name} (ΑΦΜ {c.afm})"
            self.customer_combo.addItem(label, c.cid)
        if current_cid is not None:
            idx = self.customer_combo.findData(current_cid)
            if idx >= 0:
                self.customer_combo.setCurrentIndex(idx)
        self.customer_combo.blockSignals(False)

    def _message(self, title: str, text: str, icon=QMessageBox.Warning):
        m = QMessageBox(self)
        m.setIcon(icon)
        m.setWindowTitle(title)
        m.setText(text)
        m.exec()

    def _read_invoice_from_fields(self) -> Invoice:
        invoice_no = self.invoice_no_edit.text().strip()
        amount = parse_amount_eur(self.amount_edit.text())
        issue_date = parse_date(self.issue_edit.text())
        credit_months = int(self.credit_spin.value())

        paid_s = self.paid_edit.text().strip()
        paid_date = parse_date(paid_s) if paid_s else None

        annual_rate = None
        if self.use_override_btn.isChecked():
            annual_rate = pct_to_rate(float(self.rate_override_pct.value()))

        return Invoice(
            invoice_no=invoice_no,
            amount=amount,
            issue_date=issue_date,
            credit_months=credit_months,
            paid_date=paid_date,
            annual_rate=annual_rate,
            customer_id=self.customer_combo.currentData()
        )

    def _selected_row(self) -> Optional[int]:
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        return sel[0].row()

    def _on_select_row(self):
        row = self._selected_row()
        if row is None:
            return
        inv = self.model.invoices[row]
        self.invoice_no_edit.setText(inv.invoice_no or "")
        self.amount_edit.setText(str(inv.amount).replace(".", ","))
        self.issue_edit.setText(inv.issue_date.strftime("%d/%m/%Y"))
        self.credit_spin.setValue(inv.credit_months)
        self.paid_edit.setText(inv.paid_date.strftime("%d/%m/%Y") if inv.paid_date else "")

        cid = inv.customer_id
        idx_c = self.customer_combo.findData(cid)
        self.customer_combo.setCurrentIndex(idx_c if idx_c >= 0 else 0)

        if inv.annual_rate is not None:
            self.use_override_btn.setChecked(True)
            self.rate_override_pct.setValue(inv.annual_rate * 100.0)
        else:
            self.use_override_btn.setChecked(False)
            self.rate_override_pct.setValue(0.0)

    def clear_fields(self):
        self.table.clearSelection()
        self.invoice_no_edit.clear()
        self.amount_edit.clear()
        self.issue_edit.clear()
        self.credit_spin.setValue(int(self.model.default_credit_months))
        self.paid_edit.clear()
        self.use_override_btn.setChecked(False)
        self.rate_override_pct.setValue(0.0)
        self.customer_combo.setCurrentIndex(0)

    def add_invoice(self):
        try:
            inv = self._read_invoice_from_fields()
        except Exception as e:
            self._message("Λάθος δεδομένων", str(e))
            return

        # ΔΙΠΛΟΤΥΠΟ: έλεγχος μόνο αν έχεις γράψει κάτι
        if self.model.invoice_no_exists(inv.invoice_no):
            self._message("Διπλό τιμολόγιο",
                          f"Υπάρχει ήδη καταχώρηση με Αρ. Τιμολογίου: {inv.invoice_no}\n"
                          f"Βάλε διαφορετικό αριθμό ή άφησέ το κενό.")
            return

        self.model.invoices.append(inv)
        self.model.schedule_save()
        self.on_data_changed()
        self.refresh()
        self.clear_fields()

    def update_selected(self):
        row = self._selected_row()
        if row is None:
            self._message("Επιλογή", "Διάλεξε γραμμή από τον πίνακα.")
            return

        try:
            inv = self._read_invoice_from_fields()
        except Exception as e:
            self._message("Λάθος δεδομένων", str(e))
            return

        # ΔΙΠΛΟΤΥΠΟ: exclude την ίδια γραμμή
        if self.model.invoice_no_exists(inv.invoice_no, exclude_index=row):
            self._message("Διπλό τιμολόγιο",
                          f"Υπάρχει ήδη άλλη καταχώρηση με Αρ. Τιμολογίου: {inv.invoice_no}\n"
                          f"Βάλε διαφορετικό αριθμό ή άφησέ το κενό.")
            return

        self.model.invoices[row] = inv
        self.model.schedule_save()
        self.on_data_changed()
        self.refresh()

    def delete_selected(self):
        row = self._selected_row()
        if row is None:
            self._message("Επιλογή", "Διάλεξε γραμμή από τον πίνακα.")
            return

        del self.model.invoices[row]
        self.model.schedule_save()
        self.on_data_changed()
        self.refresh()
        self.clear_fields()

    def set_period(self, year: int, month: int):
        self.period_year = year
        self.period_month = int(month or 0)
        self.refresh()

    def _in_period(self, d: date) -> bool:
        if self.period_year is None:
            return True
        if d.year != self.period_year:
            return False
        if self.period_month in (0, None):
            return True
        return d.month == self.period_month

    
    def _on_wear_changed(self, _val: float):
        try:
            self.model.wear_rate_per_km = float(self.sp_wear_default.value())
        except Exception:
            self.model.wear_rate_per_km = 0.10
        try:
            self.model.schedule_save()
        except Exception:
            pass
        try:
            self.refresh()
        except Exception:
            pass

    def refresh_driver_combo(self, prefer_truck_id: Optional[int] = None, prefer_driver_id: Optional[int] = None):
        current = self.cb_driver.currentData() if hasattr(self, 'cb_driver') else None
        self.cb_driver.blockSignals(True)
        self.cb_driver.clear()
        self.cb_driver.addItem('— Χωρίς οδηγό —', None)
        for d in self.model.active_drivers():
            self.cb_driver.addItem(d.name, d.did)
        # Prefer main driver of selected truck
        did = None
        if prefer_driver_id is not None:
            did = prefer_driver_id
        elif prefer_truck_id is not None:
            t = self.model.truck_by_id(prefer_truck_id)
            did = getattr(t, 'main_driver_id', None) if t else None
        if did is not None:
            idx = self.cb_driver.findData(did)
            if idx >= 0:
                self.cb_driver.setCurrentIndex(idx)
        self.cb_driver.blockSignals(False)
def refresh(self):
        self._refresh_customers_combo()
        self.table.setRowCount(0)

        for idx, inv in enumerate(self.model.invoices, 1):
            row = self.table.rowCount()
            self.table.insertRow(row)

            status = "ΠΛΗΡΩΜΕΝΟ" if inv.paid_date else "ΑΠΛΗΡΩΤΟ"
            rate_txt = f"{inv.annual_rate*100:.2f}%" if inv.annual_rate is not None else "-"

            cust = self.model.customer_name(inv.customer_id)

            values = [
                str(idx),
                inv.invoice_no or "",
                cust,
                fmt_eur(inv.amount),
                fmt_date(inv.issue_date),
                str(inv.credit_months),
                fmt_date(inv.paid_date),
                rate_txt,
                status
            ]

            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                if col in (0, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                if col in (3, 8):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)

        self.table.resizeColumnsToContents()
        try:
            self._hide_odometer_column_if_exists()
        except Exception:
            pass


# -----------------------------
# Station: Customers Page (Πελάτες)
# -----------------------------

class StationCustomersPage(QWidget):
    def __init__(self, model: StationModel, on_data_changed: Callable[[], None]):
        super().__init__()
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0
        self.on_data_changed = on_data_changed

        main = QVBoxLayout(self)

        form_box = QGroupBox("Πελάτες")
        grid = QGridLayout(form_box)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("π.χ. Παπαδόπουλος ΑΕ")

        self.afm_edit = QLineEdit()
        self.afm_edit.setPlaceholderText("π.χ. 099999999")

        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("π.χ. 2101234567")

        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("προαιρετικό")

        grid.addWidget(QLabel("Όνομα*:"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(QLabel("ΑΦΜ:"), 0, 2)
        grid.addWidget(self.afm_edit, 0, 3)

        grid.addWidget(QLabel("Τηλέφωνο:"), 1, 0)
        grid.addWidget(self.phone_edit, 1, 1)
        grid.addWidget(QLabel("Σχόλια:"), 1, 2)
        grid.addWidget(self.notes_edit, 1, 3)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("➕ Προσθήκη")
        self.update_btn = QPushButton("✏️ Ενημέρωση")
        self.delete_btn = QPushButton("🗑️ Διαγραφή")
        self.clear_btn = QPushButton("🧹 Καθαρισμός")
        style_action_buttons(self.add_btn, self.update_btn, self.delete_btn, self.clear_btn)

        self.add_btn.clicked.connect(self.add_customer)
        self.update_btn.clicked.connect(self.update_selected)
        self.delete_btn.clicked.connect(self.delete_selected)
        self.clear_btn.clicked.connect(self.clear_fields)

        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.update_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.clear_btn)

        grid.addLayout(btn_row, 2, 0, 1, 4)

        main.addWidget(form_box)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Όνομα", "ΑΦΜ", "Τηλέφωνο", "Σχόλια"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_select_row)

        main.addWidget(self.table, 1)

        self.refresh()

    def _message(self, title: str, text: str, icon=QMessageBox.Warning):
        m = QMessageBox(self)
        m.setIcon(icon)
        m.setWindowTitle(title)
        m.setText(text)
        m.exec()

    def _selected_row(self) -> Optional[int]:
        sel = self.table.selectionModel().selectedRows()
        if not sel:
            return None
        return sel[0].row()

    def _on_select_row(self):
        row = self._selected_row()
        if row is None:
            return
        c = self.model.customers[row]
        self.name_edit.setText(c.name)
        self.afm_edit.setText(c.afm)
        self.phone_edit.setText(c.phone)
        self.notes_edit.setText(c.notes)

    def clear_fields(self):
        self.table.clearSelection()
        self.name_edit.clear()
        self.afm_edit.clear()
        self.phone_edit.clear()
        self.notes_edit.clear()

    def _read_customer_from_fields(self) -> Dict[str, str]:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Το όνομα πελάτη είναι υποχρεωτικό.")
        return {
            "name": name,
            "afm": self.afm_edit.text().strip(),
            "phone": self.phone_edit.text().strip(),
            "notes": self.notes_edit.text().strip(),
        }

    def add_customer(self):
        try:
            d = self._read_customer_from_fields()
        except Exception as e:
            self._message("Λάθος πελάτη", str(e))
            return

        self.model.add_customer(**d)
        self.model.schedule_save()
        self.on_data_changed()
        self.refresh()
        self.clear_fields()

    def update_selected(self):
        row = self._selected_row()
        if row is None:
            self._message("Επιλογή", "Διάλεξε πελάτη από τον πίνακα.")
            return
        try:
            d = self._read_customer_from_fields()
        except Exception as e:
            self._message("Λάθος πελάτη", str(e))
            return

        c = self.model.customers[row]
        c.name = d["name"]
        c.afm = d["afm"]
        c.phone = d["phone"]
        c.notes = d["notes"]

        self.model.schedule_save()
        self.on_data_changed()
        self.refresh()

    def delete_selected(self):
        row = self._selected_row()
        if row is None:
            self._message("Επιλογή", "Διάλεξε πελάτη από τον πίνακα.")
            return
        c = self.model.customers[row]

        if self.model.has_invoices_for_customer(c.cid):
            self._message(
                "Δεν γίνεται διαγραφή",
                "Ο πελάτης χρησιμοποιείται σε τιμολόγια.\nΑφαίρεσε/άλλαξε πρώτα τον πελάτη από τα τιμολόγια."
            )
            return

        del self.model.customers[row]
        self.model.schedule_save()
        self.on_data_changed()
        self.refresh()
        self.clear_fields()

    def refresh(self):
        # fill drivers combo
        current = self.cb_main_driver.currentData() if hasattr(self, 'cb_main_driver') else None
        if hasattr(self, 'cb_main_driver'):
            self.cb_main_driver.blockSignals(True)
            self.cb_main_driver.clear()
            self.cb_main_driver.addItem('— κανένας —', None)
            for d in self.model.active_drivers():
                self.cb_main_driver.addItem(d.name, d.did)
            if current is not None:
                idx = self.cb_main_driver.findData(current)
                if idx >= 0:
                    self.cb_main_driver.setCurrentIndex(idx)
            self.cb_main_driver.blockSignals(False)
        self.table.setRowCount(0)

        for i, c in enumerate(self.model.customers, 1):
            row = self.table.rowCount()
            self.table.insertRow(row)

            values = [str(i), c.name, c.afm, c.phone, c.notes]
            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                if col == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)

        self.table.resizeColumnsToContents()
        try:
            self._hide_odometer_column_if_exists()
        except Exception:
            pass


# -----------------------------
# Station: Interest Page (Τόκοι)
# -----------------------------

class StationInterestPage(QWidget):
    def __init__(self, model: StationModel):
        super().__init__()
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0

        main = QVBoxLayout(self)

        settings_box = QGroupBox("Ρυθμίσεις Τόκων")
        grid = QGridLayout(settings_box)

        self.default_rate_pct = QDoubleSpinBox()
        self.default_rate_pct.setRange(0.0, 200.0)
        self.default_rate_pct.setDecimals(2)
        self.default_rate_pct.setSuffix(" %")
        self.default_rate_pct.setValue(float(self.model.default_rate_pct))

        self.as_of_edit = QLineEdit()
        self.as_of_edit.setPlaceholderText("π.χ. 2026-02-06 ή 06/02/2026")
        self.as_of_edit.setText(self.model.as_of)

        self.recalc_btn = QPushButton("🔄 Επανυπολογισμός")
        set_button_role(self.recalc_btn, "primary")
        self.recalc_btn.clicked.connect(self.refresh)

        grid.addWidget(QLabel("Default επιτόκιο:"), 0, 0)
        grid.addWidget(self.default_rate_pct, 0, 1)
        grid.addWidget(QLabel("Ημ/νία υπολογισμού (as of):"), 0, 2)
        grid.addWidget(self.as_of_edit, 0, 3)
        grid.addWidget(self.recalc_btn, 0, 4)

        main.addWidget(settings_box)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "#", "Αρ. Τιμ.", "Πελάτης", "Ποσό", "Έκδοση", "Λήξη", "Μέχρι", "Ημέρες", "Επιτόκιο", "Τόκος", "Πληρώθηκε;"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        main.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.total_label = QLabel("ΣΥΝΟΛΙΚΟΣ ΤΟΚΟΣ: 0,00 €")
        set_label_role(self.total_label, "summary-total")
        bottom.addWidget(self.total_label)
        bottom.addStretch(1)
        main.addLayout(bottom)

        self.default_rate_pct.valueChanged.connect(self._settings_changed)
        self.as_of_edit.textChanged.connect(self._settings_changed)

    def _message(self, title: str, text: str, icon=QMessageBox.Warning):
        m = QMessageBox(self)
        m.setIcon(icon)
        m.setWindowTitle(title)
        m.setText(text)
        m.exec()

    def _settings_changed(self):
        self.model.default_rate_pct = float(self.default_rate_pct.value())
        self.model.as_of = self.as_of_edit.text().strip() or date.today().strftime("%Y-%m-%d")
        self.model.schedule_save()

    def _get_as_of(self) -> date:
        s = self.as_of_edit.text().strip()
        if not s:
            return date.today()
        return parse_date(s)

    def refresh(self):
        self._settings_changed()

        try:
            as_of = self._get_as_of()
        except Exception as e:
            self._message("Λάθος ημερομηνίας as of", str(e))
            return

        default_rate = pct_to_rate(float(self.default_rate_pct.value()))

        self.table.setRowCount(0)
        total_interest = 0.0

        for idx, inv in enumerate(self.model.invoices, 1):
            due, end, days, rate, intr = calc_interest(inv, default_rate, as_of)
            total_interest += intr

            row = self.table.rowCount()
            self.table.insertRow(row)

            cust = self.model.customer_name(inv.customer_id)

            values = [
                str(idx),
                inv.invoice_no or "",
                cust,
                fmt_eur(inv.amount),
                fmt_date(inv.issue_date),
                fmt_date(due),
                fmt_date(end),
                str(days),
                f"{rate*100:.2f}%",
                fmt_eur(intr),
                "ΝΑΙ" if inv.paid_date else "ΟΧΙ",
            ]

            for col, v in enumerate(values):
                item = QTableWidgetItem(v)
                if col in (0, 7):
                    item.setTextAlignment(Qt.AlignCenter)
                if col in (3, 9):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)

        self.table.resizeColumnsToContents()
        try:
            self._hide_odometer_column_if_exists()
        except Exception:
            pass
        self.total_label.setText(f"ΣΥΝΟΛΙΚΟΣ ΤΟΚΟΣ: {fmt_eur(total_interest)}")


# -----------------------------
# Trucks window (placeholder)
# -----------------------------



# -----------------------------
# Drivers Page (Οδηγοί)
# -----------------------------


class PayHistoryDialog(QDialog):
    def __init__(self, parent, driver: 'Driver'):
        super().__init__(parent)
        self.driver = driver
        self.setWindowTitle("Μισθοδοσία ανά μήνα")
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Month selector
        row = QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(QLabel("Έτος:"))
        self.sp_year = QSpinBox()
        self.sp_year.setRange(2000, 2100)
        row.addWidget(self.sp_year)
        row.addWidget(QLabel("Μήνας:"))
        self.sp_month = QSpinBox()
        self.sp_month.setRange(1, 12)
        row.addWidget(self.sp_month)
        row.addStretch(1)

        # Form
        form = QFormLayout()
        layout.addLayout(form)

        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["Μηνιαίος", "Ανά δρομολόγιο"])
        form.addRow("Τρόπος πληρωμής:", self.cb_mode)

        self.sp_salary = QDoubleSpinBox()
        self.sp_salary.setRange(0, 1_000_000)
        self.sp_salary.setDecimals(2)
        form.addRow("Μισθός (€):", self.sp_salary)

        self.sp_per_trip = QDoubleSpinBox()
        self.sp_per_trip.setRange(0, 1_000_000)
        self.sp_per_trip.setDecimals(2)
        form.addRow("€ / Δρομολόγιο:", self.sp_per_trip)

        self.sp_stamps = QDoubleSpinBox()
        self.sp_stamps.setRange(0, 1_000_000)
        self.sp_stamps.setDecimals(2)
        form.addRow("Ένσημα (€):", self.sp_stamps)

        self.lb_hint = QLabel("Αν δεν υπάρχει τιμή για κάποιον μήνα, χρησιμοποιείται η πιο πρόσφατη προηγούμενη.")
        self.lb_hint.setWordWrap(True)
        layout.addWidget(self.lb_hint)

        # Buttons
        btns = QHBoxLayout()
        layout.addLayout(btns)
        self.btn_copy_prev = QPushButton("Αντιγραφή από προηγούμενο")
        self.btn_save = QPushButton("Αποθήκευση")
        self.btn_close = QPushButton("Κλείσιμο")
        btns.addWidget(self.btn_copy_prev)
        btns.addStretch(1)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_close)

        self.btn_close.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)
        self.btn_copy_prev.clicked.connect(self._copy_prev)
        self.cb_mode.currentIndexChanged.connect(self._toggle_fields)
        self.sp_year.valueChanged.connect(self._load_for_selected_month)
        self.sp_month.valueChanged.connect(self._load_for_selected_month)

        # init to current month and load
        today = date.today()
        self.sp_year.setValue(today.year)
        self.sp_month.setValue(today.month)
        self._load_for_selected_month()
        self._toggle_fields()

    def _ym(self) -> str:
        return _ym_from_year_month(int(self.sp_year.value()), int(self.sp_month.value()))

    def _toggle_fields(self):
        per_trip = (self.cb_mode.currentIndex() == 1)
        self.sp_per_trip.setEnabled(per_trip)
        self.sp_salary.setEnabled(not per_trip)

    def _load_for_selected_month(self):
        ym = self._ym()
        snap = driver_pay_for_month(self.driver, ym)
        mode = str(snap.get("pay_mode","monthly") or "monthly")
        self.cb_mode.setCurrentIndex(1 if mode == "per_trip" else 0)
        self.sp_salary.setValue(float(snap.get("salary", 0.0) or 0.0))
        self.sp_stamps.setValue(float(snap.get("stamp_cost", 0.0) or 0.0))
        self.sp_per_trip.setValue(float(snap.get("pay_per_trip", 0.0) or 0.0))
        self._toggle_fields()

    def _copy_prev(self):
        # Load snapshot from previous month (fallback logic already does that)
        ym = self._ym()
        y, m = int(ym[:4]), int(ym[5:7])
        m -= 1
        if m == 0:
            y -= 1
            m = 12
        prev = _ym_from_year_month(y, m)
        snap = driver_pay_for_month(self.driver, prev)
        mode = str(snap.get("pay_mode","monthly") or "monthly")
        self.cb_mode.setCurrentIndex(1 if mode == "per_trip" else 0)
        self.sp_salary.setValue(float(snap.get("salary", 0.0) or 0.0))
        self.sp_stamps.setValue(float(snap.get("stamp_cost", 0.0) or 0.0))
        self.sp_per_trip.setValue(float(snap.get("pay_per_trip", 0.0) or 0.0))
        self._toggle_fields()

    def _save(self):
        ym = self._ym()
        pay_mode = "per_trip" if self.cb_mode.currentIndex() == 1 else "monthly"
        salary = float(self.sp_salary.value())
        stamps = float(self.sp_stamps.value())
        per_trip = float(self.sp_per_trip.value())
        set_driver_pay_for_month(self.driver, ym, pay_mode, salary, stamps, per_trip)
        QMessageBox.information(self, "OK", f"Αποθηκεύτηκε για {ym}.")

class DriversPage(QWidget):
    def __init__(self, model, on_changed):
        super().__init__()
        self.model = model
        self.on_changed = on_changed
        self.selected_did: Optional[int] = None

        self.period_year: Optional[int] = None
        self.period_month: int = 0

        root = QVBoxLayout(self)
        title = QLabel("Οδηγοί")
        set_label_role(title, "section-title")
        root.addWidget(title)

        # Περίοδος (ίδιο component)
        self.period_bar = PeriodBar(None)
        root.addWidget(self.period_bar)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # --- Tab 1: Στοιχεία ---
        tab_details = QWidget()
        v = QVBoxLayout(tab_details)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["ID", "Όνομα", "Τηλέφωνο", "Τρόπος", "€/Δρομ.", "Μισθός", "Ένσημο", "Ενεργός", "Σχόλια"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(0, True)
        self.table.cellClicked.connect(self.on_row_clicked)
        v.addWidget(self.table, 1)

        form = QGroupBox("Στοιχεία")
        grid = QGridLayout(form)

        self.ed_name = QLineEdit()
        self.ed_phone = QLineEdit()

        self.sp_salary = QDoubleSpinBox()
        self.sp_salary.setRange(0.0, 1_000_000.0)
        self.sp_salary.setDecimals(2)
        self.sp_salary.setSuffix(" €")

        self.sp_stamp = QDoubleSpinBox()
        self.sp_stamp.setRange(0.0, 1_000_000.0)
        self.sp_stamp.setDecimals(2)
        self.sp_stamp.setSuffix(" €")

        self.cb_pay_mode = QComboBox()
        self.cb_pay_mode.addItems(["Μηνιαίος", "Ανά δρομολόγιο"])

        self.sp_per_trip = QDoubleSpinBox()
        self.sp_per_trip.setRange(0.0, 1_000_000.0)
        self.sp_per_trip.setDecimals(2)
        self.sp_per_trip.setSuffix(" €")

        self.cb_active = QComboBox()
        self.cb_active.addItems(["Ναι", "Όχι"])

        self.ed_notes = QLineEdit()

        self.cb_pay_mode.currentIndexChanged.connect(self._update_pay_mode_ui)
        self._update_pay_mode_ui()

        self.lbl_salary_per_km = QLabel("Μισθός / km: —")
        set_label_role(self.lbl_salary_per_km, "metric")

        grid.addWidget(QLabel("Όνομα:"), 0, 0); grid.addWidget(self.ed_name, 0, 1)
        grid.addWidget(QLabel("Τηλέφωνο:"), 1, 0); grid.addWidget(self.ed_phone, 1, 1)
        grid.addWidget(QLabel("Τρόπος πληρωμής:"), 0, 2); grid.addWidget(self.cb_pay_mode, 0, 3)
        grid.addWidget(QLabel("€ / δρομολόγιο:"), 1, 2); grid.addWidget(self.sp_per_trip, 1, 3)
        grid.addWidget(QLabel("Μισθός:"), 0, 4); grid.addWidget(self.sp_salary, 0, 5)
        grid.addWidget(QLabel("Ένσημο:"), 1, 4); grid.addWidget(self.sp_stamp, 1, 5)
        grid.addWidget(QLabel("Ενεργός:"), 2, 0); grid.addWidget(self.cb_active, 2, 1)
        grid.addWidget(QLabel("Σχόλια:"), 2, 2); grid.addWidget(self.ed_notes, 2, 5)
        grid.addWidget(self.lbl_salary_per_km, 3, 0, 1, 4)

        v.addWidget(form)

        btns = QHBoxLayout()
        self.btn_new = QPushButton("Νέος")
        self.btn_add = QPushButton("Προσθήκη")
        self.btn_update = QPushButton("Ενημέρωση")
        self.btn_delete = QPushButton("Διαγραφή")
        self.btn_pay_history = QPushButton("Μισθοδοσία ανά μήνα…")
        style_action_buttons(self.btn_new, self.btn_add, self.btn_update, self.btn_delete)
        self.btn_new.clicked.connect(self.clear_form)
        self.btn_add.clicked.connect(self.add_driver)
        self.btn_update.clicked.connect(self.update_driver)
        self.btn_delete.clicked.connect(self.delete_driver)
        self.btn_pay_history.clicked.connect(self.open_pay_history)
        btns.addWidget(self.btn_new); btns.addWidget(self.btn_add); btns.addWidget(self.btn_update); btns.addWidget(self.btn_delete); btns.addSpacing(12); btns.addWidget(self.btn_pay_history); btns.addStretch(1)
        v.addLayout(btns)

        self.tabs.addTab(tab_details, "Στοιχεία Οδηγού")

        # --- Tab 2: Ιστορικό ---
        tab_hist = QWidget()
        hv = QVBoxLayout(tab_hist)

        self.hist_table = QTableWidget(0, 7)
        self.hist_table.setHorizontalHeaderLabels(["ID", "Ημ/νία", "Φορτηγό", "Από", "Προς", "Km", "Έσοδο"])
        self.hist_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.hist_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.hist_table.verticalHeader().setVisible(False)
        self.hist_table.setColumnHidden(0, True)
        hv.addWidget(self.hist_table, 1)

        foot = QHBoxLayout()
        self.lbl_hist_km = QLabel("Σύνολο km (περίοδος): 0")
        self.lbl_hist_salary_km = QLabel("Μισθός / km (περίοδος): —")
        set_label_role(self.lbl_hist_salary_km, "metric")
        foot.addWidget(self.lbl_hist_km)
        foot.addStretch(1)
        foot.addWidget(self.lbl_hist_salary_km)
        hv.addLayout(foot)

        self.tabs.addTab(tab_hist, "Ιστορικό Δρομολογίων")

        # Συνδέουμε την Περίοδο ΑΦΟΥ έχουν δημιουργηθεί τα widgets
        self.period_bar.on_changed = self.set_period
        # Default: τρέχον έτος, όλοι οι μήνες (ώστε να δουλεύει το Ιστορικό & Μισθός/km)
        self.period_bar.set_all_year()

        self.refresh()

    def set_period(self, year: int, month: int):
        self.period_year = year
        self.period_month = int(month or 0)
        self.refresh_history_and_metrics()

    def _in_period(self, d: date) -> bool:
        if self.period_year is None:
            return True
        if d.year != self.period_year:
            return False
        if self.period_month in (0, None):
            return True
        return d.month == self.period_month

    def refresh(self):
        self.table.setRowCount(0)
        for d in sorted(self.model.drivers, key=lambda x: x.did):
            r = self.table.rowCount(); self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(str(d.did)))
            self.table.setItem(r, 1, QTableWidgetItem(d.name))
            self.table.setItem(r, 2, QTableWidgetItem(d.phone))
            mode = str(getattr(d, "pay_mode", "monthly") or "monthly")
            mode_label = "Ανά δρομ." if mode == "per_trip" else "Μηνιαίος"
            self.table.setItem(r, 3, QTableWidgetItem(mode_label))
            self.table.setItem(r, 4, QTableWidgetItem(f"{float(getattr(d,'pay_per_trip',0.0) or 0.0):.2f}"))
            self.table.setItem(r, 5, QTableWidgetItem(f"{getattr(d,'salary',0.0):.2f}"))
            self.table.setItem(r, 6, QTableWidgetItem(f"{getattr(d,'stamp_cost',0.0):.2f}"))
            self.table.setItem(r, 7, QTableWidgetItem("Ναι" if d.active else "Όχι"))
            self.table.setItem(r, 8, QTableWidgetItem(d.notes))
        self.table.resizeColumnsToContents()
        self.refresh_history_and_metrics()

    def on_row_clicked(self, row, col):
        did = int(self.table.item(row, 0).text())
        d = self.model.driver_by_id(did)
        if not d:
            return
        self.selected_did = did
        self.ed_name.setText(d.name)
        self.ed_phone.setText(d.phone)
        mode = str(getattr(d, "pay_mode", "monthly") or "monthly")
        self.cb_pay_mode.setCurrentIndex(1 if mode == "per_trip" else 0)
        self.sp_per_trip.setValue(float(getattr(d, "pay_per_trip", 0.0) or 0.0))
        self.sp_salary.setValue(float(getattr(d, "salary", 0.0) or 0.0))
        self.sp_stamp.setValue(float(getattr(d, "stamp_cost", 0.0) or 0.0))
        self._update_pay_mode_ui()
        self.cb_active.setCurrentIndex(0 if d.active else 1)
        self.ed_notes.setText(d.notes)
        self.refresh_history_and_metrics()

    def clear_form(self):
        self.selected_did = None
        self.ed_name.clear()
        self.ed_phone.clear()
        self.cb_pay_mode.setCurrentIndex(0)
        self.sp_per_trip.setValue(0.0)
        self.sp_salary.setValue(0.0)
        self.sp_stamp.setValue(0.0)
        self._update_pay_mode_ui()
        self.cb_active.setCurrentIndex(0)
        self.ed_notes.clear()
        self.lbl_salary_per_km.setText("Μισθός / km: —")
        self.hist_table.setRowCount(0)
        self.lbl_hist_km.setText("Σύνολο km (περίοδος): 0")
        self.lbl_hist_salary_km.setText("Μισθός / km (περίοδος): —")
        self.table.clearSelection()

    def _update_pay_mode_ui(self):
        per_trip = (self.cb_pay_mode.currentIndex() == 1)
        self.sp_per_trip.setEnabled(per_trip)
        self.sp_salary.setEnabled(not per_trip)



    def add_driver(self):
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Σφάλμα", "Το όνομα είναι υποχρεωτικό.")
            return
        d = Driver(
            did=self.model.next_driver_id,
            name=name,
            phone=self.ed_phone.text().strip(),
            salary=float(self.sp_salary.value()),
            stamp_cost=float(self.sp_stamp.value()),
            pay_mode=("per_trip" if self.cb_pay_mode.currentIndex() == 1 else "monthly"),
            pay_per_trip=float(self.sp_per_trip.value()),
            active=(self.cb_active.currentIndex() == 0),
            notes=self.ed_notes.text().strip(),
        )
        self.model.next_driver_id += 1
        self.model.drivers.append(d)
        self.model.schedule_save()
        self.refresh()
        self.clear_form()

    def update_driver(self):
        if self.selected_did is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε οδηγό για ενημέρωση.")
            return
        d = self.model.driver_by_id(self.selected_did)
        if not d:
            return
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Σφάλμα", "Το όνομα είναι υποχρεωτικό.")
            return
        d.name = name
        d.phone = self.ed_phone.text().strip()
        d.pay_mode = ("per_trip" if self.cb_pay_mode.currentIndex() == 1 else "monthly")
        d.pay_per_trip = float(self.sp_per_trip.value())
        d.salary = float(self.sp_salary.value())
        d.stamp_cost = float(self.sp_stamp.value())
        d.active = (self.cb_active.currentIndex() == 0)
        d.notes = self.ed_notes.text().strip()
        self.model.schedule_save()
        self.refresh()


    def current_driver(self) -> Optional['Driver']:
        """Return currently selected driver object, or None."""
        if self.selected_did is None:
            return None
        return self.model.driver_by_id(self.selected_did)

    def open_pay_history(self):
        d = self.current_driver()
        if not d:
            QMessageBox.warning(self, "Οδηγοί", "Επίλεξε οδηγό.")
            return
        try:
            dlg = PayHistoryDialog(self, d)
            dlg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Σφάλμα", f"Αποτυχία ανοίγματος μισθοδοσίας ανά μήνα:\n{e}")
            return
        # refresh legacy fields display
        _sync_driver_legacy_from_history(d)
        self.model.save()
        self.refresh()

    def delete_driver(self):
        if self.selected_did is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε οδηγό για διαγραφή.")
            return
        did = self.selected_did
        # Απαγόρευση διαγραφή αν υπάρχει συσχέτιση σε δρομολόγια
        if any(getattr(tr, "driver_id", None) == did for tr in self.model.trips):
            QMessageBox.warning(self, "Απαγορεύεται", "Υπάρχουν δρομολόγια για αυτόν τον οδηγό.")
            return
        self.model.drivers = [x for x in self.model.drivers if x.did != did]
        self.model.schedule_save()
        self.refresh()
        self.clear_form()

    def refresh_history_and_metrics(self):
        # Υπολογισμός/Ιστορικό μόνο όταν έχει επιλεγεί οδηγός
        if self.selected_did is None:
            if hasattr(self, 'lbl_salary_per_km'):
                self.lbl_salary_per_km.setText("Μισθός / km: —")
            self.hist_table.setRowCount(0)
            self.lbl_hist_km.setText("Σύνολο km (περίοδος): 0")
            self.lbl_hist_salary_km.setText("Μισθός / km (περίοδος): —")
            return

        d = self.model.driver_by_id(self.selected_did)
        if not d:
            return

        # trips οδηγού στην περίοδο
        trips = [tr for tr in self.model.trips if getattr(tr, "driver_id", None) == self.selected_did and self._in_period(tr.trip_date)]
        trips.sort(key=lambda tr: tr.trip_date, reverse=True)

        # γεμίζουμε ιστορικό
        self.hist_table.setRowCount(0)
        total_km = 0
        for tr in trips:
            total_km += int(getattr(tr, "trip_km", 0) or 0)
            r = self.hist_table.rowCount(); self.hist_table.insertRow(r)
            self.hist_table.setItem(r, 0, QTableWidgetItem(str(tr.trip_id)))
            self.hist_table.setItem(r, 1, QTableWidgetItem(tr.trip_date.strftime("%d/%m/%Y")))
            truck = self.model.truck_by_id(tr.truck_id)
            self.hist_table.setItem(r, 2, QTableWidgetItem(truck.plate if truck else ""))
            self.hist_table.setItem(r, 3, QTableWidgetItem(getattr(tr, "origin", "")))
            self.hist_table.setItem(r, 4, QTableWidgetItem(getattr(tr, "destination", "")))
            self.hist_table.setItem(r, 5, QTableWidgetItem(str(getattr(tr, "trip_km", 0) or 0)))
            self.hist_table.setItem(r, 6, QTableWidgetItem(f"{float(getattr(tr, 'revenue', 0.0) or 0.0):.2f}"))
        self.hist_table.resizeColumnsToContents()

        self.lbl_hist_km.setText(f"Σύνολο km (περίοδος): {total_km}")

        # μισθός / km (μόνο μισθός, όχι ένσημο)
        if total_km > 0 and float(getattr(d, "salary", 0.0) or 0.0) > 0:
            val = float(getattr(d, "salary", 0.0) or 0.0) / float(total_km)
            self.lbl_salary_per_km.setText(f"Μισθός / km: {val:.4f} €")
            self.lbl_hist_salary_km.setText(f"Μισθός / km (περίοδος): {val:.4f} €")
        else:
            self.lbl_salary_per_km.setText("Μισθός / km: —")
            self.lbl_hist_salary_km.setText("Μισθός / km (περίοδος): —")
@dataclass
class Truck:
    tid: int
    plate: str
    odometer_km: int = 0  # ένδειξη χιλιομετρητή (ανεξάρτητη από τα χλμ δρομολογίου)
    active: bool = True
    main_driver_id: Optional[int] = None
    fixed_monthly_expenses: float = 0.0  # €/μήνα
    wear_rate_per_km: float = 0.0  # €/χλμ (0=χρήση προεπιλογής)
    notes: str = ""
@dataclass
class Trip:
    trip_id: int
    truck_id: int
    trip_date: date
    driver_id: Optional[int] = None
    origin: str = ""
    destination: str = ""
    trip_km: int = 0      # χλμ δρομολογίου (ΔΕΝ ενημερώνουν αυτόματα το odometer_km)
    revenue: float = 0.0
    commission_percent: float = 0.0
    toll_amount: float = 0.0  # διόδια δρομολογίου (έξοδο)
    driver_pay: float = 0.0  # αμοιβή οδηγού για αυτό το δρομολόγιο (μόνο για per_trip)
    notes: str = ""


@dataclass
class FuelExpense:
    fuel_id: int
    truck_id: int
    fuel_date: date
    driver_id: Optional[int] = None
    liters: float = 0.0
    cost: float = 0.0
    odometer_km: int = 0  # προαιρετική ένδειξη κατά τον ανεφοδιασμό
    station: str = ""
    receipt: str = ""
    notes: str = ""

@dataclass
class Driver:
    did: int
    name: str
    phone: str = ""
    salary: float = 0.0
    stamp_cost: float = 0.0
    pay_mode: str = "monthly"  # monthly | per_trip
    pay_per_trip: float = 0.0
    pay_history: list = field(default_factory=list)
    active: bool = True
    notes: str = ""




def _ym_from_date(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"

def _ym_today() -> str:
    return _ym_from_date(date.today())

def driver_pay_for_month(driver: 'Driver', ym: str):
    hist = list(getattr(driver, "pay_history", []) or [])
    if not hist:
        return {
            "pay_mode": getattr(driver, "pay_mode", "monthly"),
            "salary": float(getattr(driver, "salary", 0.0) or 0.0),
            "stamp_cost": float(getattr(driver, "stamp_cost", 0.0) or 0.0),
            "pay_per_trip": float(getattr(driver, "pay_per_trip", 0.0) or 0.0),
        }
    hist = sorted([h for h in hist if "month" in h], key=lambda x: x["month"])
    chosen = hist[0]
    for h in hist:
        if h["month"] <= ym:
            chosen = h
    return chosen



def _ym_from_date(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"

def _ym_from_year_month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"

def _ym_today() -> str:
    return _ym_from_date(date.today())

def driver_pay_for_month(driver: 'Driver', ym: str) -> dict:
    """Latest pay record with month <= ym (YYYY-MM). Falls back to legacy fields."""
    hist = list(getattr(driver, "pay_history", []) or [])
    if not hist:
        return {
            "month": "1900-01",
            "pay_mode": str(getattr(driver, "pay_mode", "monthly") or "monthly"),
            "salary": float(getattr(driver, "salary", 0.0) or 0.0),
            "stamp_cost": float(getattr(driver, "stamp_cost", 0.0) or 0.0),
            "pay_per_trip": float(getattr(driver, "pay_per_trip", 0.0) or 0.0),
        }
    norm = []
    for r in hist:
        if not isinstance(r, dict):
            continue
        m = str(r.get("month", "")).strip()
        if not m:
            continue
        norm.append({
            "month": m,
            "pay_mode": str(r.get("pay_mode", "monthly") or "monthly"),
            "salary": float(r.get("salary", 0.0) or 0.0),
            "stamp_cost": float(r.get("stamp_cost", 0.0) or 0.0),
            "pay_per_trip": float(r.get("pay_per_trip", 0.0) or 0.0),
        })
    if not norm:
        return {
            "month": "1900-01",
            "pay_mode": str(getattr(driver, "pay_mode", "monthly") or "monthly"),
            "salary": float(getattr(driver, "salary", 0.0) or 0.0),
            "stamp_cost": float(getattr(driver, "stamp_cost", 0.0) or 0.0),
            "pay_per_trip": float(getattr(driver, "pay_per_trip", 0.0) or 0.0),
        }
    norm.sort(key=lambda x: x["month"])
    chosen = norm[0]
    for r in norm:
        if r["month"] <= ym:
            chosen = r
        else:
            break
    return chosen

def _sync_driver_legacy_from_history(driver: 'Driver'):
    snap = driver_pay_for_month(driver, _ym_today())
    driver.pay_mode = snap["pay_mode"]
    driver.salary = float(snap["salary"])
    driver.stamp_cost = float(snap["stamp_cost"])
    driver.pay_per_trip = float(snap["pay_per_trip"])

def set_driver_pay_for_month(driver: 'Driver', ym: str, pay_mode: str, salary: float, stamp_cost: float, pay_per_trip: float):
    hist = list(getattr(driver, "pay_history", []) or [])
    ym = str(ym).strip()
    hist = [h for h in hist if not (isinstance(h, dict) and str(h.get("month","")).strip() == ym)]
    hist.append({
        "month": ym,
        "pay_mode": str(pay_mode or "monthly"),
        "salary": float(salary or 0.0),
        "stamp_cost": float(stamp_cost or 0.0),
        "pay_per_trip": float(pay_per_trip or 0.0),
    })
    hist.sort(key=lambda x: str(x.get("month","")))
    driver.pay_history = hist
    _sync_driver_legacy_from_history(driver)



def trucks_data_file() -> str:
    # Προσπαθεί να βρει το σωστό trucks_data.json με προτεραιότητα:
    # 1) δίπλα στο .py που τρέχεις
    # 2) στον τρέχοντα φάκελο (cwd)
    # 3) σε υποφακέλους βάθους 2 (π.χ. Downloads/ΤΟΚΟΙ)
    # 4) fallback στο AppDataLocation
    try:
        script_dir = os.path.dirname(os.path.abspath(sys.argv[0] or __file__))
    except Exception:
        script_dir = None

    roots = []
    if script_dir:
        roots.append(script_dir)
    try:
        roots.append(os.getcwd())
    except Exception:
        pass

    # 1) trucks_data.json με σωστό όνομα
    for root in roots:
        try:
            cand = os.path.join(root, "trucks_data.json")
            if os.path.exists(cand):
                return cand
        except Exception:
            pass

    # 2) ψάξε για JSON που μοιάζει με trucks data (έχει trucks/trips/fuels/next_ids)
    try:
        patterns = []
        for root in roots:
            if not root:
                continue
            patterns += [
                os.path.join(root, "*.json"),
                os.path.join(root, "*", "*.json"),
                os.path.join(root, "*", "*", "*.json"),
            ]
        for pat in patterns:
            for cand in sorted(glob.glob(pat)):
                try:
                    obj = safe_read_json(cand)
                    if not obj:
                        continue
                    if all(k in obj for k in ("trucks", "trips", "fuels", "next_ids")):
                        return cand
                except Exception:
                    continue
    except Exception:
        pass

    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not base:
        base = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "trucks_data.json")

class TrucksModel:
    """Ανεξάρτητη αποθήκευση Φορτηγών σε ξεχωριστό JSON (trucks_data.json)."""
    def __init__(self):
        self.path = trucks_data_file()
        if DEBUG: print('[Trucks] using data file:', self.path)
        self.trucks: List[Truck] = []
        self.trips: List[Trip] = []
        self.fuels: List[FuelExpense] = []
        self.next_truck_id: int = 1
        self.next_trip_id: int = 1
        self.next_fuel_id: int = 1

        self.drivers: List[Driver] = []
        self.next_driver_id: int = 1

        # Ρύθμιση: προεπιλεγμένη φθορά €/χλμ (override ανά φορτηγό)
        self.wear_rate_per_km: float = 0.10

        self._save_timer: Optional[QTimer] = None

    def attach_autosave_timer(self, owner_widget: QWidget, save_now_cb: Callable[[], None]):
        t = QTimer(owner_widget)
        t.setSingleShot(True)
        t.timeout.connect(save_now_cb)
        self._save_timer = t

    def schedule_save(self, ms: int = 300):
        if self._save_timer is not None:
            self._save_timer.start(ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trucks": [
                {
                    "tid": t.tid,
                    "plate": t.plate,
                    "odometer_km": t.odometer_km,
                    "active": t.active,
                "fixed_monthly_expenses": t.fixed_monthly_expenses,
                "wear_rate_per_km": float(getattr(t, "wear_rate_per_km", 0.0) or 0.0),
                "main_driver_id": getattr(t, "main_driver_id", None),
                "notes": t.notes,
                } for t in self.trucks
            ],
            "trips": [
                {
                    "trip_id": tr.trip_id,
                    "truck_id": tr.truck_id,
                    "trip_date": tr.trip_date.strftime("%Y-%m-%d"),
                    "origin": tr.origin,
                    "destination": tr.destination,
                    "trip_km": tr.trip_km,
                    "revenue": tr.revenue,
                    "commission_percent": tr.commission_percent,
                    "toll_amount": getattr(tr, "toll_amount", 0.0) or 0.0,
                    "driver_id": getattr(tr, "driver_id", None),
                    "driver_pay": float(getattr(tr, "driver_pay", 0.0) or 0.0),
                    "notes": tr.notes,
                } for tr in self.trips
            ],
            "fuels": [
                {
                    "fuel_id": fu.fuel_id,
                    "truck_id": fu.truck_id,
                    "fuel_date": fu.fuel_date.strftime("%Y-%m-%d"),
                    "liters": fu.liters,
                    "driver_id": getattr(fu, "driver_id", None),
                    # cost = κόστος/λίτρο (€/L)
                    "cost": fu.cost,
                    "cost_per_liter": fu.cost,
                    "total_cost": float(fu.liters or 0.0) * float(fu.cost or 0.0),
                    "odometer_km": fu.odometer_km,
                    "station": fu.station,
                    "receipt": fu.receipt,
                    "notes": fu.notes,
                } for fu in self.fuels
            ],
            "drivers": [
                {"did": d.did, "name": d.name, "phone": d.phone, "salary": d.salary, "stamp_cost": d.stamp_cost, "pay_mode": getattr(d,"pay_mode","monthly"), "pay_per_trip": float(getattr(d,"pay_per_trip",0.0) or 0.0), "pay_history": getattr(d,"pay_history", []) or [], "active": d.active, "notes": d.notes}
                for d in self.drivers
            ],
            "settings": {
                "wear_rate_per_km": float(getattr(self, "wear_rate_per_km", 0.10) or 0.10),
            },
            "next_ids": {
                "truck": self.next_truck_id,
                "trip": self.next_trip_id,
                "fuel": self.next_fuel_id,
                "driver": self.next_driver_id,
            }
        }

    def load(self) -> None:
        data = safe_read_json(self.path)
        if not data:
            if DEBUG: print('[Trucks][load] no data at path', self.path)
            return
        if isinstance(data, dict):
            if DEBUG: print('[Trucks][load] top-level keys:', list(data.keys()
))
        else:
            if DEBUG: print('[Trucks][load] unexpected JSON type:', type(data)
)

        
        # --- Settings (schema-tolerant) ---
        try:
            st = data.get("settings", {}) if isinstance(data, dict) else {}
            rate = st.get("wear_rate_per_km", None)
            if rate is None:
                rate = data.get("wear_rate_per_km", None) if isinstance(data, dict) else None
            self.wear_rate_per_km = float(rate) if rate is not None else float(getattr(self, "wear_rate_per_km", 0.10) or 0.10)
        except Exception:
            self.wear_rate_per_km = float(getattr(self, "wear_rate_per_km", 0.10) or 0.10)

        # --- Load trucks (schema-tolerant) ---
        self.trucks = []
        _trucks_src = data.get("trucks", []) or data.get("truck_list", []) or data.get("fleet", []) or []
        for t in _trucks_src:
            try:
                if not isinstance(t, dict):
                    if DEBUG: print('[Trucks][load] skip non-dict truck item:', t)
                    continue
                tid = int(t.get("tid") or t.get("truck_id") or t.get("id"))
                plate = str(t.get("plate") or t.get("license_plate") or t.get("license") or t.get("Πινακίδα") or "").strip()
                odometer_km = int(t.get("odometer_km", 0) or t.get("odometer", 0) or 0)
                active = bool(t.get("active", True))
                notes = str(t.get("notes", "") or t.get("Σχόλια", "") or "").strip()
                main_driver_id = t.get("main_driver_id") or t.get("driver_id")
                fixed_monthly_expenses = float(t.get("fixed_monthly_expenses", 0.0) or t.get("fixed_cost", 0.0) or 0.0)
                wear_rate_per_km = float(t.get("wear_rate_per_km", 0.0) or t.get("wear_per_km", 0.0) or t.get("wear", 0.0) or 0.0)
                if not plate:
                    if DEBUG: print(f"[Trucks][load] warning: empty plate for truck id {tid}")
                self.trucks.append(Truck(
                    tid=tid,
                    plate=plate,
                    odometer_km=odometer_km,
                    active=active,
                    notes=notes,
                    main_driver_id=main_driver_id,
                    fixed_monthly_expenses=fixed_monthly_expenses,
                    wear_rate_per_km=wear_rate_per_km,
                ))
            except Exception as e:
                try:
                    if DEBUG: print('[Trucks][load] failed to load truck item:', t)
                except Exception:
                    if DEBUG: print('[Trucks][load] failed to load truck item: <unprintable>')
                print('   reason:', repr(e))
                continue


        self.trips = []
        for tr in data.get("trips", []):
            try:
                self.trips.append(Trip(
                    trip_id=int(tr.get("trip_id")),
                    truck_id=int(tr.get("truck_id")),
                    trip_date=parse_date(str(tr.get("trip_date", "")).strip() or date.today().strftime("%Y-%m-%d")),
                    origin=str(tr.get("origin", "") or ""),
                    destination=str(tr.get("destination", "") or ""),
                    trip_km=int(tr.get("trip_km", 0) or 0),
                    revenue=float(tr.get("revenue", 0.0) or 0.0),
                    commission_percent=float(tr.get("commission_percent", 0.0) or 0.0),
                    toll_amount=float(tr.get("toll_amount", 0.0) or 0.0),
                    driver_id=(int(tr.get("driver_id")) if tr.get("driver_id") is not None else None),
                    driver_pay=float(tr.get("driver_pay", tr.get("driver_fee", 0.0)) or 0.0),
                    notes=str(tr.get("notes", "") or ""),
                ))
            except Exception as e:
                try:
                    if DEBUG: print('[Trucks][load] failed to load truck item:', t)
                except Exception:
                    if DEBUG: print('[Trucks][load] failed to load truck item: <unprintable>')
                print('   reason:', repr(e))
                continue

        self.fuels = []
        for fu in data.get("fuels", []):
            try:
                liters = float(fu.get("liters", 0.0) or 0.0)
                # Νέο νόημα: cost = κόστος/λίτρο (€/L). Για παλιά δεδομένα που είχαν συνολικό ποσό,
                # κάνουμε αυτόματη μετατροπή με βάση τα λίτρα.
                raw_cost = fu.get("cost_per_liter", None)
                if raw_cost is None:
                    raw_cost = fu.get("unit_cost", None)
                if raw_cost is None:
                    raw_cost = fu.get("cost", 0.0)
                unit_cost = float(raw_cost or 0.0)

                # Heuristic migration: αν φαίνεται σαν "συνολικό κόστος" (πολύ μεγαλύτερο από €/L),
                # το μετατρέπουμε σε €/L διαιρώντας με τα λίτρα.
                if liters > 0 and unit_cost > 10.0:
                    unit_cost = unit_cost / liters

                self.fuels.append(FuelExpense(
                    fuel_id=int(fu.get("fuel_id")),
                    truck_id=int(fu.get("truck_id")),
                    fuel_date=parse_date(str(fu.get("fuel_date", "")).strip() or date.today().strftime("%Y-%m-%d")),
                    liters=liters,
                    cost=unit_cost,
                    driver_id=(int(fu.get("driver_id")) if fu.get("driver_id") is not None else None),
                    odometer_km=int(fu.get("odometer_km", 0) or 0),
                    station=str(fu.get("station", "") or ""),
                    receipt=str(fu.get("receipt", "") or ""),
                    notes=str(fu.get("notes", "") or ""),
                ))
            except Exception as e:
                try:
                    if DEBUG: print('[Trucks][load] failed to load truck item:', t)
                except Exception:
                    if DEBUG: print('[Trucks][load] failed to load truck item: <unprintable>')
                print('   reason:', repr(e))
                continue

        # drivers
        self.drivers = []
        _drivers_src = (data.get("drivers", []) or [])
        if not _drivers_src:
            try:
                _drivers_src = (data.get("next_ids", {}) or {}).get("drivers", []) or []
            except Exception:
                _drivers_src = []
        for d in _drivers_src:
            try:
                drv = Driver(did=int(d.get("did")), name=str(d.get("name", "")).strip(), phone=str(d.get("phone", "")).strip(), salary=float(d.get("salary", 0.0) or 0.0), stamp_cost=float(d.get("stamp_cost", d.get("ensimo", 0.0)) or 0.0), pay_mode=str(d.get("pay_mode", d.get("payment_mode", "monthly")) or "monthly"), pay_per_trip=float(d.get("pay_per_trip", d.get("per_trip", 0.0)) or 0.0), active=bool(d.get("active", True)), notes=str(d.get("notes", "")).strip())
                ph = d.get("pay_history") or []
                if not ph:
                    ph = [{"month":"1900-01", "pay_mode": getattr(drv,'pay_mode','monthly'), "salary": getattr(drv,'salary',0.0), "stamp_cost": getattr(drv,'stamp_cost',0.0), "pay_per_trip": getattr(drv,'pay_per_trip',0.0)}]
                drv.pay_history = ph
                _sync_driver_legacy_from_history(drv)
                self.drivers.append(drv)
            except Exception as e:
                try:
                    if DEBUG: print('[Trucks][load] failed to load truck item:', t)
                except Exception:
                    if DEBUG: print('[Trucks][load] failed to load truck item: <unprintable>')
                print('   reason:', repr(e))
                continue

        ids = data.get("next_ids", {}) or {}
        self.next_truck_id = int(ids.get("truck", max([t.tid for t in self.trucks], default=0) + 1))
        self.next_trip_id = int(ids.get("trip", max([t.trip_id for t in self.trips], default=0) + 1))
        self.next_fuel_id = int(ids.get("fuel", max([f.fuel_id for f in self.fuels], default=0) + 1))
        self.next_driver_id = int(ids.get("driver", max([d.did for d in self.drivers], default=0) + 1))

        print(f"[Trucks] loaded: {len(self.trucks)} trucks, {len(self.trips)} trips, {len(self.fuels)} fuels")

    def save(self) -> bool:
        return safe_write_json(self.path, self.to_dict())

    # --- lookup helpers

    def truck_by_id(self, tid: int) -> Optional[Truck]:
        for t in self.trucks:
            if t.tid == tid:
                return t
        return None

    def truck_label(self, tid: int) -> str:
        t = self.truck_by_id(tid)
        return t.plate if t and t.plate else f"Φορτηγό #{tid}"

    def wear_rate_for_truck(self, truck_id: Optional[int]) -> float:
        """Φθορά €/χλμ: override φορτηγού (>0) αλλιώς default."""
        try:
            tid = int(truck_id) if truck_id is not None else None
        except Exception:
            tid = None
        if tid is not None:
            t = self.truck_by_id(tid)
            try:
                ov = float(getattr(t, "wear_rate_per_km", 0.0) or 0.0) if t else 0.0
            except Exception:
                ov = 0.0
            if ov > 0:
                return ov
        try:
            return float(getattr(self, "wear_rate_per_km", 0.10) or 0.10)
        except Exception:
            return 0.10


    
    def driver_by_id(self, did: int) -> Optional[Driver]:
        for d in self.drivers:
            if d.did == did:
                return d
        return None

    def driver_label(self, did: Optional[int]) -> str:
        if did is None:
            return "-"
        d = self.driver_by_id(did)
        return d.name if d else f"#{did}"

    def active_drivers(self) -> list[Driver]:
        return [d for d in self.drivers if d.active]

    def active_trucks(self) -> List[Truck]:
        return [t for t in self.trucks if t.active]

def active_trucks(self) -> List[Truck]:
        return [t for t in self.trucks if t.active]



# -----------------------------
# Περίοδος εργασίας (Μήνας/Έτος)
# -----------------------------

MONTH_LABELS_GR = [
    "Ιανουάριος", "Φεβρουάριος", "Μάρτιος", "Απρίλιος", "Μάιος", "Ιούνιος",
    "Ιούλιος", "Αύγουστος", "Σεπτέμβριος", "Οκτώβριος", "Νοέμβριος", "Δεκέμβριος"
]


class PeriodBar(QWidget):
    """
    Μπάρα επιλογής περιόδου (Έτος/Μήνας) για φιλτράρισμα προβολής.
    month = 0 => Όλοι οι μήνες του έτους
    """
    def __init__(self, on_changed: Callable[[int, int], None]):
        super().__init__()
        self.on_changed = on_changed

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        lbl = QLabel("Περίοδος:")
        set_label_role(lbl, "metric")
        root.addWidget(lbl)

        self.cb_year = QComboBox()
        self.cb_month = QComboBox()

        self.btn_current = QPushButton("Τρέχων μήνας")
        self.btn_all_year = QPushButton("Όλοι (έτος)")
        set_button_role(self.btn_current, "soft")
        set_button_role(self.btn_all_year, "soft")

        # style small + tidy
        self.cb_year.setFixedWidth(120)
        self.cb_month.setFixedWidth(170)

        root.addWidget(self.cb_year)
        root.addWidget(self.cb_month)
        root.addWidget(self.btn_current)
        root.addWidget(self.btn_all_year)
        root.addStretch(1)

        self.cb_year.currentIndexChanged.connect(self._emit)
        self.cb_month.currentIndexChanged.connect(self._emit)
        self.btn_current.clicked.connect(self.set_current_month)
        self.btn_all_year.clicked.connect(self.set_all_year)

        self._building = False
        self.populate_years()
        self.set_current_month()

    def populate_years(self, years: Optional[List[int]] = None):
        self._building = True
        self.cb_year.clear()

        if not years:
            y = date.today().year
            years = list(range(y - 2, y + 3))

        for y in years:
            self.cb_year.addItem(str(y), y)
        self._building = False

        self._build_months()

    def _build_months(self):
        self._building = True
        self.cb_month.clear()
        self.cb_month.addItem("Όλοι οι μήνες", 0)
        for i, name in enumerate(MONTH_LABELS_GR, start=1):
            self.cb_month.addItem(name, i)
        self._building = False

    def set_period(self, year: int, month: int):
        # year
        idx = self.cb_year.findData(year)
        if idx >= 0:
            self.cb_year.setCurrentIndex(idx)
        else:
            # add year if missing
            self.cb_year.addItem(str(year), year)
            self.cb_year.setCurrentIndex(self.cb_year.count() - 1)

        # month
        idxm = self.cb_month.findData(month)
        if idxm >= 0:
            self.cb_month.setCurrentIndex(idxm)
        else:
            self.cb_month.setCurrentIndex(0)

        self._emit()

    def set_current_month(self):
        today = date.today()
        self.set_period(today.year, today.month)

    def set_all_year(self):
        y = int(self.cb_year.currentData())
        self.set_period(y, 0)

    def current_period(self) -> tuple[int, int]:
        y = int(self.cb_year.currentData())
        m = int(self.cb_month.currentData())
        return y, m

    def _emit(self):
        if self._building:
            return
        y, m = self.current_period()
        if self.on_changed:
            self.on_changed(y, m)

def make_trucks_nav(on_go_registry: Callable[[], None],
                    on_go_trips: Callable[[], None],
                    on_go_fuel: Callable[[], None],
                    on_go_summary: Callable[[], None],
                    on_go_drivers: Callable[[], None],
                    current: str) -> QWidget:
    bar = QWidget()
    bar.setObjectName("ModuleNavBar")
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(8)

    btn_registry = QPushButton("🚚 Μητρώο")
    btn_trips = QPushButton("🗺️ Δρομολόγια")
    btn_fuel = QPushButton("⛽ Καύσιμα/Έξοδα")
    btn_summary = QPushButton("📊 Σύνοψη")
    btn_drivers = QPushButton("👨‍✈️ Οδηγοί")
    set_button_role(btn_registry, "nav")
    set_button_role(btn_trips, "nav")
    set_button_role(btn_fuel, "nav")
    set_button_role(btn_summary, "nav")
    set_button_role(btn_drivers, "nav")

    btn_registry.setEnabled(current != "registry")
    btn_trips.setEnabled(current != "trips")
    btn_fuel.setEnabled(current != "fuel")
    btn_summary.setEnabled(current != "summary")
    btn_drivers.setEnabled(current != "drivers")

    btn_registry.clicked.connect(on_go_registry)
    btn_trips.clicked.connect(on_go_trips)
    btn_fuel.clicked.connect(on_go_fuel)
    btn_summary.clicked.connect(on_go_summary)
    btn_drivers.clicked.connect(on_go_drivers)

    layout.addWidget(btn_registry)
    layout.addWidget(btn_drivers)
    layout.addWidget(btn_trips)
    layout.addWidget(btn_fuel)
    layout.addWidget(btn_summary)
    layout.addStretch(1)

    return bar


class TruckRegistryPage(QWidget):
    def __init__(self, model: TrucksModel, on_changed: Callable[[], None]):
        super().__init__()
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0
        self.on_changed = on_changed
        self.selected_tid: Optional[int] = None

        root = QVBoxLayout(self)

        title = QLabel("Μητρώο Φορτηγών")
        set_label_role(title, "section-title")
        root.addWidget(title)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "Πινακίδα", "Χιλιομετρητής (χλμ)", "Ενεργό", "Πάγια €/μήνα", "Φθορά €/χλμ", "Κύριος οδηγός"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(0, True)
        self.table.cellClicked.connect(self.on_row_clicked)
        root.addWidget(self.table, 1)

        form = QGroupBox("Στοιχεία")
        grid = QGridLayout(form)

        self.ed_plate = QLineEdit()
        self.sp_odometer = QSpinBox()
        self.sp_odometer.setRange(0, 2_000_000)
        self.cb_active = QComboBox()
        self.cb_active.addItems(["Ναι", "Όχι"])
        self.ed_notes = QLineEdit()

        grid.addWidget(QLabel("Πινακίδα:"), 0, 0)
        grid.addWidget(self.ed_plate, 0, 1)
        grid.addWidget(QLabel("Χιλιομετρητής (χλμ):"), 0, 2)
        grid.addWidget(self.sp_odometer, 0, 3)

        self.sp_fixed = QDoubleSpinBox()
        self.sp_fixed.setRange(0, 1000000)
        self.sp_fixed.setDecimals(2)
        self.sp_fixed.setSingleStep(10.0)
        grid.addWidget(QLabel("Πάγια €/μήνα:"), 2, 0)
        grid.addWidget(self.sp_fixed, 2, 1)

        self.sp_wear = QDoubleSpinBox()
        self.sp_wear.setRange(0, 10.0)
        self.sp_wear.setDecimals(3)
        self.sp_wear.setSingleStep(0.01)
        self.sp_wear.setToolTip("Φθορά ανά χλμ για αυτό το φορτηγό (0 = χρήση προεπιλογής).")
        grid.addWidget(QLabel("Φθορά €/χλμ (override):"), 3, 0)
        grid.addWidget(self.sp_wear, 3, 1)


        grid.addWidget(QLabel("Ενεργό:"), 1, 0)
        grid.addWidget(self.cb_active, 1, 1)
        grid.addWidget(QLabel("Σημειώσεις:"), 1, 2)
        grid.addWidget(self.ed_notes, 1, 3)

        self.cb_main_driver = QComboBox()
        grid.addWidget(QLabel("Κύριος οδηγός:"), 2, 2)
        grid.addWidget(self.cb_main_driver, 2, 3)

        root.addWidget(form)

        btns = QHBoxLayout()
        self.btn_new = QPushButton("Νέο")
        self.btn_add = QPushButton("Προσθήκη")
        self.btn_update = QPushButton("Ενημέρωση")
        self.btn_delete = QPushButton("Διαγραφή")
        self.btn_pay_history = QPushButton("Μισθοδοσία ανά μήνα…")
        style_action_buttons(self.btn_new, self.btn_add, self.btn_update, self.btn_delete)

        self.btn_new.clicked.connect(self.clear_form)
        self.btn_add.clicked.connect(self.add_truck)
        self.btn_update.clicked.connect(self.update_truck)
        self.btn_delete.clicked.connect(self.delete_truck)

        btns.addWidget(self.btn_new)
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_update)
        btns.addWidget(self.btn_delete)
        btns.addStretch(1)
        root.addLayout(btns)

        self.refresh()

    def refresh(self):
        # fill drivers combo
        current = self.cb_main_driver.currentData() if hasattr(self, 'cb_main_driver') else None
        if hasattr(self, 'cb_main_driver'):
            self.cb_main_driver.blockSignals(True)
            self.cb_main_driver.clear()
            self.cb_main_driver.addItem('— κανένας —', None)
            for d in self.model.active_drivers():
                self.cb_main_driver.addItem(d.name, d.did)
            if current is not None:
                idx = self.cb_main_driver.findData(current)
                if idx >= 0:
                    self.cb_main_driver.setCurrentIndex(idx)
            self.cb_main_driver.blockSignals(False)
        self.table.setRowCount(0)
        for t in sorted(self.model.trucks, key=lambda x: x.tid):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(str(t.tid)))
            self.table.setItem(r, 1, QTableWidgetItem(t.plate))
            self.table.setItem(r, 2, QTableWidgetItem(str(t.odometer_km)))
            self.table.setItem(r, 3, QTableWidgetItem("Ναι" if t.active else "Όχι"))
            self.table.setItem(r, 4, QTableWidgetItem(f"{getattr(t, 'fixed_monthly_expenses', 0.0):.2f}"))
            wr = float(getattr(t, "wear_rate_per_km", 0.0) or 0.0)
            self.table.setItem(r, 5, QTableWidgetItem("—" if wr <= 0 else f"{wr:.3f}"))
            self.table.setItem(r, 6, QTableWidgetItem(self.model.driver_label(getattr(t, "main_driver_id", None))))

        self.table.resizeColumnsToContents()
        try:
            self._hide_odometer_column_if_exists()
        except Exception:
            pass

    def on_row_clicked(self, row: int, col: int):
        tid = int(self.table.item(row, 0).text())
        t = self.model.truck_by_id(tid)
        if not t:
            return
        self.selected_tid = tid
        self.ed_plate.setText(t.plate)
        self.sp_odometer.setValue(t.odometer_km)
        self.cb_active.setCurrentIndex(0 if t.active else 1)
        self.ed_notes.setText(t.notes)
        try:
            idx = self.cb_main_driver.findData(getattr(t, "main_driver_id", None))
            if idx >= 0:
                self.cb_main_driver.setCurrentIndex(idx)
            else:
                self.cb_main_driver.setCurrentIndex(0)
        except Exception:
            pass
        try:
            self.sp_fixed.setValue(float(getattr(t, "fixed_monthly_expenses", 0.0)))
        except Exception:
            self.sp_fixed.setValue(0.0)
        try:
            self.sp_wear.setValue(float(getattr(t, "wear_rate_per_km", 0.0) or 0.0))
        except Exception:
            self.sp_wear.setValue(0.0)

    def clear_form(self):
        self.selected_tid = None
        self.ed_plate.clear()
        self.sp_odometer.setValue(0)
        self.cb_active.setCurrentIndex(0)
        self.ed_notes.clear()
        try:
            self.sp_fixed.setValue(0.0)
        except Exception:
            pass
        try:
            self.sp_wear.setValue(0.0)
        except Exception:
            pass
        self.table.clearSelection()

    def add_truck(self):
        plate = self.ed_plate.text().strip()
        if not plate:
            QMessageBox.warning(self, "Σφάλμα", "Η πινακίδα είναι υποχρεωτική.")
            return
        if any(t.plate.lower() == plate.lower() for t in self.model.trucks):
            QMessageBox.warning(self, "Σφάλμα", "Υπάρχει ήδη φορτηγό με αυτή την πινακίδα.")
            return
        t = Truck(
            tid=self.model.next_truck_id,
            plate=plate,
            odometer_km=int(self.sp_odometer.value()),
            active=(self.cb_active.currentIndex() == 0),
            fixed_monthly_expenses=float(self.sp_fixed.value()) if hasattr(self, 'sp_fixed') else 0.0,
            wear_rate_per_km=float(self.sp_wear.value()) if hasattr(self, "sp_wear") else 0.0,
            notes=self.ed_notes.text().strip(),
            main_driver_id=self.cb_main_driver.currentData() if hasattr(self, "cb_main_driver") else None,
        )
        self.model.next_truck_id += 1
        self.model.trucks.append(t)
        self.model.schedule_save()
        self.on_changed()
        self.refresh()
        self.clear_form()

    def update_truck(self):
        if self.selected_tid is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε γραμμή για ενημέρωση.")
            return
        t = self.model.truck_by_id(self.selected_tid)
        if not t:
            return
        plate = self.ed_plate.text().strip()
        if not plate:
            QMessageBox.warning(self, "Σφάλμα", "Η πινακίδα είναι υποχρεωτική.")
            return
        if any(o.tid != t.tid and o.plate.lower() == plate.lower() for o in self.model.trucks):
            QMessageBox.warning(self, "Σφάλμα", "Υπάρχει ήδη άλλο φορτηγό με αυτή την πινακίδα.")
            return
        t.plate = plate
        t.odometer_km = int(self.sp_odometer.value())
        t.active = (self.cb_active.currentIndex() == 0)
        t.notes = self.ed_notes.text().strip()
        try:
            t.main_driver_id = self.cb_main_driver.currentData()
        except Exception:
            t.main_driver_id = None
        try:
            t.fixed_monthly_expenses = float(self.sp_fixed.value())
        except Exception:
            t.fixed_monthly_expenses = 0.0
        try:
            t.wear_rate_per_km = float(self.sp_wear.value())
        except Exception:
            t.wear_rate_per_km = 0.0
        self.model.schedule_save()
        self.on_changed()
        self.refresh()

    def delete_truck(self):
        if self.selected_tid is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε γραμμή για διαγραφή.")
            return
        tid = self.selected_tid
        if any(tr.truck_id == tid for tr in self.model.trips) or any(fu.truck_id == tid for fu in self.model.fuels):
            QMessageBox.warning(self, "Απαγορεύεται", "Το φορτηγό χρησιμοποιείται σε δρομολόγια/καύσιμα και δεν μπορεί να διαγραφεί.")
            return
        self.model.trucks = [t for t in self.model.trucks if t.tid != tid]
        self.selected_tid = None
        self.model.schedule_save()
        self.on_changed()
        self.refresh()
        self.clear_form()


class TripsPage(QWidget):
    def __init__(self, model: TrucksModel, on_changed: Callable[[], None]):
        super().__init__()
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0
        self.on_changed = on_changed
        self.selected_trip_id: Optional[int] = None
        self.period_year: Optional[int] = None
        self.period_month: int = 0  # 0=all months

        root = QVBoxLayout(self)

        title = QLabel("Δρομολόγια / Έσοδα")
        set_label_role(title, "section-title")
        root.addWidget(title)
        # Περίοδος (μόνο για Δρομολόγια)
        self.period_bar = PeriodBar(None)

        # Φίλτρο ανά Φορτηγό (μόνο ενεργά)
        self.cb_truck_filter = QComboBox()
        self.cb_truck_filter.addItem('Όλα τα φορτηγά', None)
        for t in [x for x in self.model.trucks if getattr(x, 'active', True)]:
            self.cb_truck_filter.addItem(t.plate, t.tid)
        try:
            self.cb_truck_filter.currentIndexChanged.connect(lambda _=None: self.refresh())
        except Exception:
            pass
        root.addWidget(self.period_bar)
        # Γραμμή φίλτρου φορτηγού + σύνολο κέρδους (καθαρό/φορτηγό)
        truck_row = QHBoxLayout()
        truck_row.addWidget(self.cb_truck_filter)
        truck_row.addStretch(1)
        self.lbl_total_net_profit_truck = QLabel("Σύνολο Κέρδος φορτηγού: 0,00 €")
        set_label_role(self.lbl_total_net_profit_truck, "metric")
        self.lbl_total_net_profit_truck.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        truck_row.addWidget(self.lbl_total_net_profit_truck)
        root.addLayout(truck_row)
        # Default: τρέχον έτος, όλοι οι μήνες (για να φαίνονται όλα τα δρομολόγια του έτους)
        # self.period_bar.set_all_year()  # moved after widgets init

        self.table = QTableWidget(0, 15)
        self.table.setHorizontalHeaderLabels(["ID", "Ημερομηνία", "Φορτηγό", "Οδηγός", "Από", "Προς", "Χλμ δρομολογίου", "Έσοδο", "Προμήθεια %", "Προμήθεια €", "Διόδια €", "Φθορά €", "Κέρδος δρομολογίου", "Κέρδος/σύγκριση", "Κέρδος φορτηγού"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(0, True)
        self.table.cellClicked.connect(self.on_row_clicked)
        root.addWidget(self.table, 1)

        form = QGroupBox("Καταχώρηση")
        grid = QGridLayout(form)

        self.ed_date = QLineEdit(date.today().strftime("%d/%m/%Y"))
        self.cb_truck = QComboBox()
        # Συνδέουμε την Περίοδο αφού έχουν φτιαχτεί τα widgets (ώστε να μην σκάει στο init)
        self.period_bar.on_changed = self.set_period
        # Default: τρέχον έτος, όλοι οι μήνες (να φαίνονται τα δρομολόγια)
        self.period_bar.set_all_year()
        self.ed_from = QLineEdit()
        self.ed_to = QLineEdit()
        self.sp_trip_km = QSpinBox()
        self.sp_trip_km.setRange(0, 200_000)
        self.sp_revenue = QDoubleSpinBox()
        self.sp_revenue.setRange(0, 1_000_000_000)
        self.sp_revenue.setDecimals(2)
        self.sp_commission = QDoubleSpinBox()
        self.sp_commission.setRange(0, 100)
        self.sp_commission.setDecimals(2)
        self.sp_commission.setSingleStep(0.5)
        self.sp_tolls = QDoubleSpinBox()
        self.sp_tolls.setRange(0, 1_000_000_000)
        self.sp_tolls.setDecimals(2)
        self.sp_tolls.setSingleStep(1.0)
        self.ed_notes = QLineEdit()

        grid.addWidget(QLabel("Ημ/νία:"), 0, 0)
        grid.addWidget(self.ed_date, 0, 1)
        grid.addWidget(QLabel("Φορτηγό:"), 0, 2)
        grid.addWidget(self.cb_truck, 0, 3)
        self.cb_driver = QComboBox()
        grid.addWidget(QLabel("Οδηγός:"), 0, 4)
        grid.addWidget(self.cb_driver, 0, 5)

        grid.addWidget(QLabel("Από:"), 1, 0)
        grid.addWidget(self.ed_from, 1, 1)
        grid.addWidget(QLabel("Προς:"), 1, 2)
        grid.addWidget(self.ed_to, 1, 3)

        grid.addWidget(QLabel("Χλμ δρομολογίου:"), 2, 0)
        grid.addWidget(self.sp_trip_km, 2, 1)
        grid.addWidget(QLabel("Έσοδο (€):"), 2, 2)
        grid.addWidget(self.sp_revenue, 2, 3)

        grid.addWidget(QLabel("Προμήθεια (%):"), 3, 0)
        grid.addWidget(self.sp_commission, 3, 1)
        self.lbl_commission = QLabel("Προμήθεια: 0,00 €")
        set_label_role(self.lbl_commission, "metric")
        grid.addWidget(self.lbl_commission, 3, 2, 1, 2)

        grid.addWidget(QLabel("Διόδια (€):"), 4, 0)
        grid.addWidget(self.sp_tolls, 4, 1)
        self.lbl_tolls = QLabel("Διόδια: 0,00 €")
        set_label_role(self.lbl_tolls, "metric")
        grid.addWidget(self.lbl_tolls, 4, 2, 1, 2)

        grid.addWidget(QLabel("Σημειώσεις:"), 5, 0)
        grid.addWidget(self.ed_notes, 5, 1, 1, 3)

        root.addWidget(form)

        btns = QHBoxLayout()
        self.btn_new = QPushButton("Νέο")
        self.btn_add = QPushButton("Προσθήκη")
        self.btn_update = QPushButton("Ενημέρωση")
        self.btn_delete = QPushButton("Διαγραφή")
        self.btn_pay_history = QPushButton("Μισθοδοσία ανά μήνα…")
        style_action_buttons(self.btn_new, self.btn_add, self.btn_update, self.btn_delete)

        self.btn_new.clicked.connect(self.clear_form)
        self.btn_add.clicked.connect(self.add_trip)
        self.btn_update.clicked.connect(self.update_trip)
        self.btn_delete.clicked.connect(self.delete_trip)

        btns.addWidget(self.btn_new)
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_update)
        btns.addWidget(self.btn_delete)
        btns.addStretch(1)
        root.addLayout(btns)

        self.sp_revenue.valueChanged.connect(self._update_amount_labels)
        self.sp_commission.valueChanged.connect(self._update_amount_labels)
        self.sp_tolls.valueChanged.connect(self._update_amount_labels)

        self.refresh_truck_combo()
        self.refresh_driver_combo()
        self.refresh()

    def refresh_driver_combo(self, prefer_truck_id: Optional[int] = None, prefer_driver_id: Optional[int] = None):
        if not hasattr(self, 'cb_driver'):
            return
        current = self.cb_driver.currentData()
        self.cb_driver.blockSignals(True)
        self.cb_driver.clear()
        self.cb_driver.addItem('— Χωρίς οδηγό —', None)
        for d in self.model.active_drivers():
            self.cb_driver.addItem(d.name, d.did)
        # decide preferred selection
        did = None
        if prefer_driver_id is not None:
            did = prefer_driver_id
        elif prefer_truck_id is not None:
            t = self.model.truck_by_id(prefer_truck_id)
            if t:
                did = getattr(t, 'main_driver_id', None)
        if did is not None:
            idx = self.cb_driver.findData(did)
            if idx >= 0:
                self.cb_driver.setCurrentIndex(idx)
        self.cb_driver.blockSignals(False)

    def set_period(self, year: int, month: int):
        self.period_year = year
        self.period_month = int(month or 0)
        self.refresh()

    def _in_period(self, d: date) -> bool:
        if self.period_year is None:
            return True
        if d.year != self.period_year:
            return False
        if self.period_month in (0, None):
            return True
        return d.month == self.period_month

    def suggest_date_for_period(self, year: int, month: int):
        # Μην αλλάζεις αν ο χρήστης έχει ήδη βάλει κάτι μη-κενό
        if month and self.ed_date.text().strip() == "":
            self.ed_date.setText(date(year, month, 1).strftime("%d/%m/%Y"))

    def set_period(self, year: int, month: int):
        self.period_year = year
        self.period_month = int(month or 0)
        self.refresh()

    def _in_period(self, d: date) -> bool:
        if self.period_year is None:
            return True
        if d.year != self.period_year:
            return False
        if self.period_month in (0, None):
            return True
        return d.month == self.period_month

    def suggest_date_for_period(self, year: int, month: int):
        if month and self.ed_date.text().strip() == "":
            self.ed_date.setText(date(year, month, 1).strftime("%d/%m/%Y"))

    def refresh_truck_combo(self):
        current = self.cb_truck.currentData()
        self.cb_truck.blockSignals(True)
        self.cb_truck.clear()
        for t in sorted(self.model.trucks, key=lambda x: x.plate.lower()):
            self.cb_truck.addItem(f"{t.plate} {'(ανενεργό)' if not t.active else ''}".strip(), t.tid)
        self.cb_truck.blockSignals(False)

        if current is not None:
            idx = self.cb_truck.findData(current)
            if idx >= 0:
                self.cb_truck.setCurrentIndex(idx)
    def refresh_truck_filter_combo(self):
        """Combo φίλτρου: μόνο ενεργά φορτηγά + 'Όλα' (data=None)."""
        if not hasattr(self, 'cb_truck_filter'):
            return
        current = self.cb_truck_filter.currentData()
        self.cb_truck_filter.blockSignals(True)
        self.cb_truck_filter.clear()
        self.cb_truck_filter.addItem('Όλα τα φορτηγά', None)
        for t in sorted(self.model.trucks, key=lambda x: x.plate.lower()):
            if getattr(t, 'active', True):
                self.cb_truck_filter.addItem(t.plate, t.tid)
        self.cb_truck_filter.blockSignals(False)
        try:
            if current is None:
                self.cb_truck_filter.setCurrentIndex(0)
            else:
                idx = self.cb_truck_filter.findData(current)
                self.cb_truck_filter.setCurrentIndex(idx if idx >= 0 else 0)
        except Exception:
            pass


    def refresh(self):
        self.refresh_truck_combo()
        try:
            self.refresh_truck_filter_combo()
        except Exception:
            pass
        sel_tid = None
        try:
            if hasattr(self, 'cb_truck_filter'):
                data = self.cb_truck_filter.currentData()
                sel_tid = int(data) if data is not None else None
        except Exception:
            sel_tid = None

        # Αν υπάρχει επιλεγμένο φορτηγό στο φίλτρο, "κλείδωσε" τη φόρμα καταχώρησης εκεί
        # ώστε να μην εμφανίζονται μηνύματα τύπου "δεν υπάρχει επιλεγμένο φορτηγό"
        try:
            if sel_tid is not None and hasattr(self, 'cb_truck'):
                idx = self.cb_truck.findData(sel_tid)
                if idx >= 0:
                    self.cb_truck.setCurrentIndex(idx)
                self.cb_truck.setEnabled(False)
            elif hasattr(self, 'cb_truck'):
                self.cb_truck.setEnabled(True)
        except Exception:
            pass

        self.table.setRowCount(0)

        # --- Υπολογισμός παγίων ανά χλμ (για "καθαρό" κέρδος ανά δρομολόγιο) ---
        # Scope: τα trips που προβάλλονται (με τα τρέχοντα φίλτρα περιόδου & φορτηγού)
        # Πάγια ανά μήνα: fixed_monthly_expenses φορτηγών + ένσημα (όλων) + μισθοί (μόνο monthly)
        def _month_key(d: date):
            return (d.year, d.month)

        # μηνιαία κόστη οδηγών (για όλους/μόνο monthly)
        stamps_per_month = 0.0
        salary_per_month = 0.0
        for d in getattr(self.model, 'drivers', []):
            if not getattr(d, 'active', True):
                continue
            stamps_per_month += float(getattr(d, 'stamp_cost', 0.0) or 0.0)
            mode = str(getattr(d, 'pay_mode', 'monthly') or 'monthly')
            if mode == 'monthly':
                salary_per_month += float(getattr(d, 'salary', 0.0) or 0.0)

        # fixed φορτηγών (ανά μήνα) ανάλογα με το φίλτρο
        if sel_tid is not None:
            t = self.model.truck_by_id(sel_tid)
            fixed_trucks_per_month = float(getattr(t, 'fixed_monthly_expenses', 0.0) or 0.0) if t and getattr(t, 'active', True) else 0.0
        else:
            fixed_trucks_per_month = 0.0
            for t in getattr(self.model, 'trucks', []):
                if not getattr(t, 'active', True):
                    continue
                fixed_trucks_per_month += float(getattr(t, 'fixed_monthly_expenses', 0.0) or 0.0)

        fixed_per_month_total = fixed_trucks_per_month + stamps_per_month + salary_per_month

        # km ανά μήνα (μόνο για τα trips που θα εμφανιστούν)
        km_by_month = {}
        for _tr in self.model.trips:
            if not self._in_period(_tr.trip_date):
                continue
            if sel_tid is not None and int(_tr.truck_id) != int(sel_tid):
                continue
            mk = _month_key(_tr.trip_date)
            km_by_month[mk] = km_by_month.get(mk, 0) + int(getattr(_tr, 'trip_km', 0) or 0)

        fixed_per_km_by_month = {}
        for mk, km in km_by_month.items():
            if km and km > 0:
                fixed_per_km_by_month[mk] = fixed_per_month_total / float(km)
            else:
                fixed_per_km_by_month[mk] = 0.0

        # --- Truck-specific πάγια/χλμ (κατανομή μισθών & ενσήμων ανά φορτηγό με βάση τα χλμ) ---
        # Για κάθε μήνα και φορτηγό, μοιράζουμε:
        # - fixed_monthly_expenses του ίδιου φορτηγού (100% στο φορτηγό)
        # - stamp_cost όλων των οδηγών (pro-rata στα φορτηγά που δούλεψαν, με βάση χλμ)
        # - salary μόνο των μηνιαίων οδηγών (pro-rata στα φορτηγά που δούλεψαν, με βάση χλμ)
        km_truck_month = {}  # (mk, truck_id) -> km
        km_driver_month = {}  # (mk, driver_id) -> km
        km_driver_truck_month = {}  # (mk, driver_id, truck_id) -> km

        for _tr in self.model.trips:
            if not self._in_period(_tr.trip_date):
                continue
            if sel_tid is not None and int(_tr.truck_id) != int(sel_tid):
                continue
            mk = _month_key(_tr.trip_date)
            tid = int(getattr(_tr, 'truck_id', 0) or 0)
            kmv = int(getattr(_tr, 'trip_km', 0) or 0)
            km_truck_month[(mk, tid)] = km_truck_month.get((mk, tid), 0) + kmv
            did = getattr(_tr, 'driver_id', None)
            if did is not None:
                did = int(did)
                km_driver_month[(mk, did)] = km_driver_month.get((mk, did), 0) + kmv
                km_driver_truck_month[(mk, did, tid)] = km_driver_truck_month.get((mk, did, tid), 0) + kmv

        # fixed ανά φορτηγό (μήνα) + κατανεμημένα ένσημα/μισθοί
        payroll_truck_month = {}  # (mk, truck_id) -> cost
        for d in getattr(self.model, 'drivers', []):
            if not getattr(d, 'active', True):
                continue
            did = int(getattr(d, 'did', 0) or 0)
            # κόστος οδηγού ανά μήνα (ένσημα πάντα, μισθός μόνο monthly)
            stamps = float(getattr(d, 'stamp_cost', 0.0) or 0.0)
            mode = str(getattr(d, 'pay_mode', 'monthly') or 'monthly')
            sal = float(getattr(d, 'salary', 0.0) or 0.0) if mode == 'monthly' else 0.0
            driver_month_cost = stamps + sal
            if driver_month_cost == 0.0:
                continue

            # κατανομή ανά μήνα με βάση τα χλμ του οδηγού
            # Για κάθε μήνα που εμφανίζεται σε km_driver_month:
            for (mk, _did), km_total in list(km_driver_month.items()):
                if _did != did:
                    continue
                if not km_total:
                    continue
                # find all trucks for this driver-month
                for (mk2, did2, tid2), km_part in km_driver_truck_month.items():
                    if mk2 != mk or did2 != did:
                        continue
                    if not km_part:
                        continue
                    share = driver_month_cost * (float(km_part) / float(km_total))
                    payroll_truck_month[(mk, tid2)] = payroll_truck_month.get((mk, tid2), 0.0) + share

        fixed_per_km_truck_month = {}  # (mk, truck_id) -> €/km
        for (mk, tid), km in km_truck_month.items():
            # fixed του φορτηγού
            t = self.model.truck_by_id(int(tid))
            fixed_truck = float(getattr(t, 'fixed_monthly_expenses', 0.0) or 0.0) if t and getattr(t, 'active', True) else 0.0
            fixed_total = fixed_truck + float(payroll_truck_month.get((mk, tid), 0.0) or 0.0)
            if km and km > 0:
                fixed_per_km_truck_month[(mk, tid)] = fixed_total / float(km)
            else:
                fixed_per_km_truck_month[(mk, tid)] = 0.0

        total_net_profit_truck = 0.0
        trips_count = 0

        for tr in sorted(self.model.trips, key=lambda x: (x.trip_date, x.trip_id), reverse=True):
            if not self._in_period(tr.trip_date):
                continue
            if sel_tid is not None and int(tr.truck_id) != int(sel_tid):
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(str(tr.trip_id)))
            self.table.setItem(r, 1, QTableWidgetItem(fmt_date(tr.trip_date)))
            self.table.setItem(r, 2, QTableWidgetItem(self.model.truck_label(tr.truck_id)))
            self.table.setItem(r, 3, QTableWidgetItem(self.model.driver_label(getattr(tr, "driver_id", None))))
            self.table.setItem(r, 4, QTableWidgetItem(tr.origin))
            self.table.setItem(r, 5, QTableWidgetItem(tr.destination))
            self.table.setItem(r, 6, QTableWidgetItem(str(tr.trip_km)))
            self.table.setItem(r, 7, QTableWidgetItem(fmt_eur(tr.revenue)))
            self.table.setItem(r, 8, QTableWidgetItem(f"{tr.commission_percent:.2f}%"))
            comm_amount = tr.revenue * (tr.commission_percent / 100.0)
            self.table.setItem(r, 9, QTableWidgetItem(fmt_eur(comm_amount)))
            self.table.setItem(r, 10, QTableWidgetItem(fmt_eur(getattr(tr, "toll_amount", 0.0) or 0.0)))

            wear_rate = float(self.model.wear_rate_for_truck(getattr(tr, "truck_id", None)) or 0.0)
            wear_cost = float(getattr(tr, "trip_km", 0) or 0) * wear_rate
            self.table.setItem(r, 11, QTableWidgetItem(fmt_eur(wear_cost)))

            # --- Κέρδος δρομολογίου ---
            tolls = float(getattr(tr, "toll_amount", 0.0) or 0.0)
            commission = float(comm_amount or 0.0)
            driver_pay = float(getattr(tr, "driver_pay", 0.0) or 0.0)

            # καύσιμα: άθροισμα εξόδων καυσίμων για ίδιο φορτηγό & ίδια ημερομηνία
            fuel_cost_trip = 0.0
            for f in getattr(self.model, 'fuels', []):
                try:
                    if int(getattr(f, 'truck_id', -1)) == int(tr.truck_id) and getattr(f, 'fuel_date', None) == tr.trip_date:
                        fuel_cost_trip += float(getattr(f, 'cost', 0.0) or 0.0)
                except Exception:
                    pass

            gross_profit = float(tr.revenue or 0.0) - commission - tolls - wear_cost - fuel_cost_trip - driver_pay

            mk = (tr.trip_date.year, tr.trip_date.month)
            fixed_per_km = float(fixed_per_km_by_month.get(mk, 0.0) or 0.0)
            fixed_share = fixed_per_km * float(getattr(tr, 'trip_km', 0) or 0.0)

            net_profit = gross_profit - fixed_share

            self.table.setItem(r, 12, QTableWidgetItem(fmt_eur(gross_profit)))
            self.table.setItem(r, 13, QTableWidgetItem(fmt_eur(net_profit)))

            # Καθαρό κέρδος με πάγια/χλμ ανά φορτηγό (κατανομή μισθών & ενσήμων)
            tid = int(getattr(tr, 'truck_id', 0) or 0)
            fixed_per_km_truck = float(fixed_per_km_truck_month.get((mk, tid), 0.0) or 0.0)
            fixed_share_truck = fixed_per_km_truck * float(getattr(tr, 'trip_km', 0) or 0.0)
            net_profit_truck = gross_profit - fixed_share_truck
            self.table.setItem(r, 14, QTableWidgetItem(fmt_eur(net_profit_truck)))
            total_net_profit_truck += float(net_profit_truck or 0.0)
            trips_count += 1

        # Ενημέρωση KPI: σύνολο κέρδους (καθαρό/φορτηγό) για τα εμφανιζόμενα δρομολόγια
        try:
            if hasattr(self, "lbl_total_net_profit_truck"):
                self.lbl_total_net_profit_truck.setText(
                    f"Σύνολο Κέρδος φορτηγού: {fmt_eur(total_net_profit_truck)}  (Δρομολόγια: {trips_count})"
                )
        except Exception:
            pass

        self.table.resizeColumnsToContents()
        try:
            self._hide_odometer_column_if_exists()
        except Exception:
            pass

    def on_row_clicked(self, row: int, col: int):
        trip_id = int(self.table.item(row, 0).text())
        tr = next((x for x in self.model.trips if x.trip_id == trip_id), None)
        if not tr:
            return
        self.selected_trip_id = trip_id
        self.ed_date.setText(fmt_date(tr.trip_date))
        idx = self.cb_truck.findData(tr.truck_id)
        if idx >= 0:
            self.cb_truck.setCurrentIndex(idx)
        self.ed_from.setText(tr.origin)
        self.ed_to.setText(tr.destination)
        self.sp_trip_km.setValue(tr.trip_km)
        self.sp_revenue.setValue(tr.revenue)
        self.sp_commission.setValue(tr.commission_percent)
        self.sp_tolls.setValue(float(getattr(tr, "toll_amount", 0.0) or 0.0))
        self._update_amount_labels()
        self.ed_notes.setText(tr.notes)
        try:
            self.refresh_driver_combo(prefer_truck_id=tr.truck_id, prefer_driver_id=getattr(tr, 'driver_id', None))
        except Exception:
            pass

    def _update_amount_labels(self):
        comm_amount = float(self.sp_revenue.value()) * (float(self.sp_commission.value()) / 100.0)
        self.lbl_commission.setText(f"Προμήθεια: {fmt_eur(comm_amount)}")
        tolls_amount = float(self.sp_tolls.value())
        self.lbl_tolls.setText(f"Διόδια: {fmt_eur(tolls_amount)}")

    def clear_form(self):
        self.selected_trip_id = None
        self.ed_date.setText(date.today().strftime("%d/%m/%Y"))
        self.ed_from.clear()
        self.ed_to.clear()
        self.sp_trip_km.setValue(0)
        self.sp_revenue.setValue(0.0)
        self.sp_commission.setValue(0.0)
        self.sp_tolls.setValue(0.0)
        self._update_amount_labels()
        self.ed_notes.clear()
        try:
            self.sp_fixed.setValue(0.0)
        except Exception:
            pass
        self.table.clearSelection()

    def _get_selected_truck_id(self) -> Optional[int]:
        """Επιστρέφει το επιλεγμένο φορτηγό για την καταχώρηση.

        Αν το combo της φόρμας (cb_truck) είναι άδειο/μη-έγκυρο, κάνε fallback
        στο φίλτρο (cb_truck_filter) ώστε να μην μπλοκάρει η καταχώρηση όταν ο
        χρήστης δουλεύει με επιλεγμένο φορτηγό.
        """
        # 1) Προτεραιότητα: επιλογή από τη φόρμα
        try:
            if self.cb_truck.count() > 0:
                data = self.cb_truck.currentData()
                if data is not None:
                    return int(data)
        except Exception:
            pass

        # 2) Fallback: φίλτρο φορτηγού
        try:
            if hasattr(self, 'cb_truck_filter'):
                data = self.cb_truck_filter.currentData()
                if data is not None:
                    return int(data)
        except Exception:
            pass

        return None

    def _calc_driver_pay(self, driver_id: Optional[int]) -> float:
        """Υπολογίζει αμοιβή οδηγού ανά δρομολόγιο.
        - Αν ο οδηγός είναι per_trip => επιστρέφει pay_per_trip
        - Αν είναι monthly ή δεν υπάρχει οδηγός => 0
        """
        if driver_id is None:
            return 0.0
        d = self.model.driver_by_id(int(driver_id))
        if not d:
            return 0.0
        mode = str(getattr(d, 'pay_mode', 'monthly') or 'monthly')
        if mode != 'per_trip':
            return 0.0
        return float(getattr(d, 'pay_per_trip', 0.0) or 0.0)

    def add_trip(self):
        tid = self._get_selected_truck_id()
        if tid is None:
            QMessageBox.warning(self, "Σφάλμα", "Πρέπει να καταχωρήσεις πρώτα φορτηγό στο Μητρώο.")
            return
        try:
            d = parse_date(self.ed_date.text())
        except Exception as e:
            QMessageBox.warning(self, "Σφάλμα", str(e))
            return

        tr = Trip(
            trip_id=self.model.next_trip_id,
            truck_id=tid,
            trip_date=d,
            origin=self.ed_from.text().strip(),
            destination=self.ed_to.text().strip(),
            trip_km=int(self.sp_trip_km.value()),
            revenue=float(self.sp_revenue.value()),
            commission_percent=float(self.sp_commission.value()),
            toll_amount=float(self.sp_tolls.value()),
            notes=self.ed_notes.text().strip(),
            driver_id=self.cb_driver.currentData(),
            driver_pay=self._calc_driver_pay(self.cb_driver.currentData()),
        )
        self.model.next_trip_id += 1
        self.model.trips.append(tr)

        # ΣΗΜΑΝΤΙΚΟ: Δεν ενημερώνουμε αυτόματα το χιλιομετρητή του μητρώου.
        # Το odometer_km παραμένει ανεξάρτητο, όπως ζήτησες.

        self.model.schedule_save()
        self.on_changed()
        self.refresh()
        self.clear_form()

    def update_trip(self):
        if self.selected_trip_id is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε γραμμή για ενημέρωση.")
            return
        tr = next((x for x in self.model.trips if x.trip_id == self.selected_trip_id), None)
        if not tr:
            return
        tid = self._get_selected_truck_id()
        if tid is None:
            QMessageBox.warning(self, "Σφάλμα", "Δεν υπάρχει επιλεγμένο φορτηγό.")
            return
        try:
            d = parse_date(self.ed_date.text())
        except Exception as e:
            QMessageBox.warning(self, "Σφάλμα", str(e))
            return

        tr.truck_id = tid
        tr.trip_date = d
        tr.driver_id = self.cb_driver.currentData()
        tr.driver_pay = self._calc_driver_pay(tr.driver_id)
        tr.origin = self.ed_from.text().strip()
        tr.destination = self.ed_to.text().strip()
        tr.trip_km = int(self.sp_trip_km.value())
        tr.revenue = float(self.sp_revenue.value())
        tr.commission_percent = float(self.sp_commission.value())
        tr.toll_amount = float(self.sp_tolls.value())
        tr.notes = self.ed_notes.text().strip()

        self.model.schedule_save()
        self.on_changed()
        self.refresh()

    def delete_trip(self):
        if self.selected_trip_id is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε γραμμή για διαγραφή.")
            return
        self.model.trips = [x for x in self.model.trips if x.trip_id != self.selected_trip_id]
        self.selected_trip_id = None
        self.model.schedule_save()
        self.on_changed()
        self.refresh()
        self.clear_form()


class FuelPage(QWidget):
    def refresh_driver_combo(self, prefer_truck_id=None, prefer_driver_id=None):
        """Safe no-op: FuelPage doesn't use drivers directly.
        Kept to avoid AttributeError when called from init.
        If a cb_driver exists in future, fill it with active drivers.
        """
        try:
            cb = getattr(self, 'cb_driver', None)
            if cb is None:
                return
            cb.blockSignals(True)
            cb.clear()
            cb.addItem('— Χωρίς οδηγό —', None)
            for d in self.model.active_drivers():
                cb.addItem(d.name, d.did)
            cb.blockSignals(False)
        except Exception:
            pass

    def __init__(self, model: TrucksModel, on_changed: Callable[[], None]):
        super().__init__()
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0
        self.on_changed = on_changed
        self.selected_fuel_id: Optional[int] = None
        self.row_meta: List[Dict[str, Any]] = []  # [{"kind": "fuel|commission", "id": int}]
        self.selected_kind: str = "fuel"
        self.period_year: Optional[int] = None
        self.period_month: int = 0

        root = QVBoxLayout(self)

        title = QLabel("Καύσιμα / Έξοδα")
        set_label_role(title, "section-title")
        root.addWidget(title)
        # Hide odometer editor/column if present
        try:
            if hasattr(self, "sp_odometer"):
                self.sp_odometer.hide()
        except Exception:
            pass

        def _hide_odometer_column_if_exists():
            try:
                headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
                for i, h in enumerate(headers):
                    if "Χιλιομετρητ" in h or "odometer" in h.lower():
                        self.table.setColumnHidden(i, True)
            except Exception:
                pass
        self._hide_odometer_column_if_exists = _hide_odometer_column_if_exists


        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["ID", "Ημ/νία", "Φορτηγό", "Είδος", "Λίτρα", "Ποσό (€)", "Πηγή/Πρατήριο", "Παραστατικό"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(0, True)
        self.table.cellClicked.connect(self.on_row_clicked)
        root.addWidget(self.table, 1)

        form = QGroupBox("Καταχώρηση")
        grid = QGridLayout(form)

        self.ed_date = QLineEdit(date.today().strftime("%d/%m/%Y"))
        self.cb_truck = QComboBox()
        self.sp_liters = QDoubleSpinBox()
        self.sp_liters.setRange(0, 100000)
        self.sp_liters.setDecimals(2)
        self.sp_cost = QDoubleSpinBox()
        self.sp_cost.setRange(0, 1000)
        self.sp_cost.setDecimals(3)
        self.ed_station = QLineEdit()
        self.ed_receipt = QLineEdit()
        self.ed_notes = QLineEdit()

        grid.addWidget(QLabel("Ημ/νία:"), 0, 0)
        grid.addWidget(self.ed_date, 0, 1)
        grid.addWidget(QLabel("Φορτηγό:"), 0, 2)
        grid.addWidget(self.cb_truck, 0, 3)
        self.cb_driver = QComboBox()
        grid.addWidget(QLabel("Οδηγός:"), 0, 4)
        grid.addWidget(self.cb_driver, 0, 5)

        grid.addWidget(QLabel("Λίτρα:"), 1, 0)
        grid.addWidget(self.sp_liters, 1, 1)
        grid.addWidget(QLabel("Κόστος/Λίτρο (€/L):"), 1, 2)
        grid.addWidget(self.sp_cost, 1, 3)

        grid.addWidget(QLabel("Πρατήριο:"), 2, 0)
        grid.addWidget(self.ed_station, 2, 1)

        grid.addWidget(QLabel("Παραστατικό:"), 2, 2)
        grid.addWidget(self.ed_receipt, 2, 3)
        grid.addWidget(QLabel("Σημειώσεις:"), 3, 0)
        grid.addWidget(self.ed_notes, 3, 1)

        root.addWidget(form)

        btns = QHBoxLayout()
        self.btn_new = QPushButton("Νέο")
        self.btn_add = QPushButton("Προσθήκη")
        self.btn_update = QPushButton("Ενημέρωση")
        self.btn_delete = QPushButton("Διαγραφή")
        self.btn_pay_history = QPushButton("Μισθοδοσία ανά μήνα…")
        style_action_buttons(self.btn_new, self.btn_add, self.btn_update, self.btn_delete)

        self.btn_new.clicked.connect(self.clear_form)
        self.btn_add.clicked.connect(self.add_fuel)
        self.btn_update.clicked.connect(self.update_fuel)
        self.btn_delete.clicked.connect(self.delete_fuel)

        btns.addWidget(self.btn_new)
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_update)
        btns.addWidget(self.btn_delete)
        btns.addStretch(1)
        root.addLayout(btns)

        self.refresh_truck_combo()
        self.refresh_driver_combo()
        self.refresh()

    def set_period(self, year: int, month: int):
        self.period_year = year
        self.period_month = int(month or 0)
        self.refresh()

    def _in_period(self, d: date) -> bool:
        if self.period_year is None:
            return True
        if d.year != self.period_year:
            return False
        if self.period_month in (0, None):
            return True
        return d.month == self.period_month

    def suggest_date_for_period(self, year: int, month: int):
        # Μην αλλάζεις αν ο χρήστης έχει ήδη βάλει κάτι μη-κενό
        if month and self.ed_date.text().strip() == "":
            self.ed_date.setText(date(year, month, 1).strftime("%d/%m/%Y"))

    def refresh_truck_combo(self):
        current = self.cb_truck.currentData()
        self.cb_truck.blockSignals(True)
        self.cb_truck.clear()
        for t in sorted(self.model.trucks, key=lambda x: x.plate.lower()):
            self.cb_truck.addItem(f"{t.plate} {'(ανενεργό)' if not t.active else ''}".strip(), t.tid)
        self.cb_truck.blockSignals(False)

        if current is not None:
            idx = self.cb_truck.findData(current)
            if idx >= 0:
                self.cb_truck.setCurrentIndex(idx)

    def refresh(self):
        self.refresh_truck_combo()
        self.table.setRowCount(0)
        self.row_meta = []

        items: List[Dict[str, Any]] = []

        # Καύσιμα/έξοδα (καταχωρήσεις χρήστη)
        for fu in self.model.fuels:
            if not self._in_period(fu.fuel_date):
                continue
            items.append({
                "kind": "fuel",
                "id": fu.fuel_id,
                "date": fu.fuel_date,
                "truck_id": fu.truck_id,
                "type_label": "Καύσιμο",
                "liters": fu.liters,
                "amount": float(fu.liters or 0.0) * float(fu.cost or 0.0),
                "source": fu.station,
                "receipt": fu.receipt,
            })

        # Προμήθειες δρομολογίων (παράγονται από τα Δρομολόγια)
        for tr in self.model.trips:
            if not self._in_period(tr.trip_date):
                continue
            pct = float(getattr(tr, "commission_percent", 0.0) or 0.0)
            if pct <= 0 or tr.revenue <= 0:
                continue
            amount = float(tr.revenue) * (pct / 100.0)
            items.append({
                "kind": "commission",
                "id": tr.trip_id,  # αναφορά στο δρομολόγιο
                "date": tr.trip_date,
                "truck_id": tr.truck_id,
                "type_label": f"Προμήθεια ({pct:.2f}%)",
                "liters": None,
                "amount": amount,
                "source": f"Δρομολόγιο #{tr.trip_id}: {tr.origin} → {tr.destination}".strip(),
                "receipt": "",
            })


        # Διόδια δρομολογίων (παράγονται από τα Δρομολόγια)
        for tr in self.model.trips:
            toll = float(getattr(tr, "toll_amount", 0.0) or 0.0)
            if toll <= 0:
                continue
            items.append({
                "kind": "toll",
                "id": tr.trip_id,  # αναφορά στο δρομολόγιο
                "date": tr.trip_date,
                "truck_id": tr.truck_id,
                "type_label": "Διόδια",
                "liters": None,
                "amount": toll,
                "source": f"Δρομολόγιο #{tr.trip_id}: {tr.origin} → {tr.destination}".strip(),
                "receipt": "",
            })

        # Φθορές (€/χλμ) δρομολογίων (παράγονται από τα Δρομολόγια)
        for tr in self.model.trips:
            if not self._in_period(tr.trip_date):
                continue
            km = int(getattr(tr, "trip_km", 0) or 0)
            if km <= 0:
                continue
            rate = float(self.model.wear_rate_for_truck(getattr(tr, "truck_id", None)) or 0.0)
            if rate <= 0:
                continue
            amount = float(km) * rate
            items.append({
                "kind": "wear",
                "id": tr.trip_id,
                "date": tr.trip_date,
                "truck_id": tr.truck_id,
                "type_label": f"Φθορά ({rate:.3f} €/χλμ)",
                "liters": None,
                "amount": amount,
                "source": f"Δρομολόγιο #{tr.trip_id}: {tr.origin} → {tr.destination} ({km} χλμ)".strip(),
                "receipt": "",
            })

        items.sort(key=lambda x: (x["date"], x["kind"], x["id"]), reverse=True)

        for it in items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.row_meta.append({"kind": it["kind"], "id": it["id"]})

            # ID (κρυφό)
            shown_id = str(it["id"])
            self.table.setItem(r, 0, QTableWidgetItem(shown_id))

            self.table.setItem(r, 1, QTableWidgetItem(fmt_date(it["date"])))
            self.table.setItem(r, 2, QTableWidgetItem(self.model.truck_label(it["truck_id"])))
            self.table.setItem(r, 3, QTableWidgetItem(it["type_label"]))
            self.table.setItem(r, 4, QTableWidgetItem("" if it["liters"] is None else f"{float(it['liters']):.2f}"))
            self.table.setItem(r, 5, QTableWidgetItem(fmt_eur(float(it["amount"]))))
            self.table.setItem(r, 6, QTableWidgetItem(it["source"]))
            self.table.setItem(r, 7, QTableWidgetItem(it["receipt"]))

        self.table.resizeColumnsToContents()
        try:
            self._hide_odometer_column_if_exists()
        except Exception:
            pass

    def on_row_clicked(self, row: int, col: int):
        if row < 0 or row >= len(self.row_meta):
            return
        meta = self.row_meta[row]
        kind = meta.get("kind")
        self.selected_kind = str(kind)

        if kind == "commission":
            trip_id = int(meta.get("id"))
            self.selected_fuel_id = None
            self.clear_form()
            QMessageBox.information(
                self,
                "Προμήθεια",
                f"Η προμήθεια προέρχεται από το Δρομολόγιο #{trip_id}.\n"
                f"Για αλλαγή, πήγαινε στα Δρομολόγια και άλλαξε την Προμήθεια (%)."
            )
            return

        if kind == "toll":
            trip_id = int(meta.get("id"))
            self.selected_fuel_id = None
            self.clear_form()
            QMessageBox.information(
                self,
                "Διόδια",
                f"Τα διόδια προέρχονται από το Δρομολόγιο #{trip_id}.\n"
                f"Για αλλαγή, πήγαινε στα Δρομολόγια και άλλαξε τα Διόδια (€)."
            )
            return

        if kind == "wear":
            trip_id = int(meta.get("id"))
            self.selected_fuel_id = None
            self.clear_form()
            QMessageBox.information(
                self,
                "Φθορά",
                f"Η φθορά προέρχεται από το Δρομολόγιο #{trip_id} (χλμ × €/χλμ).\n"
                f"Για αλλαγή, πήγαινε στη Σύνοψη (default €/χλμ) ή στο Μητρώο φορτηγών (override €/χλμ)."
            )
            return

        fuel_id = int(meta.get("id"))
        fu = next((x for x in self.model.fuels if x.fuel_id == fuel_id), None)
        if not fu:
            return
        self.selected_fuel_id = fuel_id
        self.ed_date.setText(fmt_date(fu.fuel_date))
        idx = self.cb_truck.findData(fu.truck_id)
        if idx >= 0:
            self.cb_truck.setCurrentIndex(idx)
        self.sp_liters.setValue(fu.liters)
        self.sp_cost.setValue(fu.cost)
        self.ed_station.setText(fu.station)
        self.ed_receipt.setText(fu.receipt)
        self.ed_notes.setText(fu.notes)

    def clear_form(self):
        self.selected_fuel_id = None
        self.selected_kind = "fuel"
        self.ed_date.setText(date.today().strftime("%d/%m/%Y"))
        self.sp_liters.setValue(0.0)
        self.sp_cost.setValue(0.0)
        self.ed_station.clear()
        self.ed_receipt.clear()
        self.ed_notes.clear()
        try:
            self.sp_fixed.setValue(0.0)
        except Exception:
            pass
        self.table.clearSelection()

    def _get_selected_truck_id(self) -> Optional[int]:
        if self.cb_truck.count() == 0:
            return None
        return int(self.cb_truck.currentData())

    def _calc_driver_pay(self, driver_id: Optional[int]) -> float:
        """Υπολογίζει αμοιβή οδηγού ανά δρομολόγιο.
        - Αν ο οδηγός είναι per_trip => επιστρέφει pay_per_trip
        - Αν είναι monthly ή δεν υπάρχει οδηγός => 0
        """
        if driver_id is None:
            return 0.0
        d = self.model.driver_by_id(int(driver_id))
        if not d:
            return 0.0
        mode = str(getattr(d, 'pay_mode', 'monthly') or 'monthly')
        if mode != 'per_trip':
            return 0.0
        return float(getattr(d, 'pay_per_trip', 0.0) or 0.0)

    def add_fuel(self):
        tid = self._get_selected_truck_id()
        if tid is None:
            QMessageBox.warning(self, "Σφάλμα", "Πρέπει να καταχωρήσεις πρώτα φορτηγό στο Μητρώο.")
            return
        try:
            d = parse_date(self.ed_date.text())
        except Exception as e:
            QMessageBox.warning(self, "Σφάλμα", str(e))
            return

        fu = FuelExpense(
            fuel_id=self.model.next_fuel_id,
            truck_id=tid,
            fuel_date=d,
            liters=float(self.sp_liters.value()),
            cost=float(self.sp_cost.value()),
            station=self.ed_station.text().strip(),
            receipt=self.ed_receipt.text().strip(),
            notes=self.ed_notes.text().strip(),
            driver_id=(self.cb_driver.currentData() if hasattr(self, 'cb_driver') else None),
        )
        self.model.next_fuel_id += 1
        self.model.fuels.append(fu)
        self.model.schedule_save()
        self.on_changed()
        self.refresh()
        self.clear_form()

    def update_fuel(self):
        if self.selected_fuel_id is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε γραμμή για ενημέρωση.")
            return
        fu = next((x for x in self.model.fuels if x.fuel_id == self.selected_fuel_id), None)
        if not fu:
            return
        tid = self._get_selected_truck_id()
        if tid is None:
            QMessageBox.warning(self, "Σφάλμα", "Δεν υπάρχει επιλεγμένο φορτηγό.")
            return
        try:
            d = parse_date(self.ed_date.text())
        except Exception as e:
            QMessageBox.warning(self, "Σφάλμα", str(e))
            return

        fu.truck_id = tid
        fu.fuel_date = d
        fu.driver_id = (self.cb_driver.currentData() if hasattr(self, 'cb_driver') else None)
        fu.liters = float(self.sp_liters.value())
        fu.cost = float(self.sp_cost.value())
        fu.station = self.ed_station.text().strip()
        fu.receipt = self.ed_receipt.text().strip()
        fu.notes = self.ed_notes.text().strip()

        self.model.schedule_save()
        self.on_changed()
        self.refresh()

    def delete_fuel(self):
        if self.selected_fuel_id is None:
            QMessageBox.information(self, "Επιλογή", "Διάλεξε γραμμή για διαγραφή.")
            return
        self.model.fuels = [x for x in self.model.fuels if x.fuel_id != self.selected_fuel_id]
        self.selected_fuel_id = None
        self.model.schedule_save()
        self.on_changed()
        self.refresh()
        self.clear_form()


class TrucksSummaryPage(QWidget):
    def _clear_totals(self):
        try:
            while self._totals_grid.count():
                item = self._totals_grid.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
        except Exception:
            pass

    def _add_section(self, row: int, title: str) -> int:
        lbl = QLabel(title)
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lbl.setStyleSheet("font-weight: 700; margin-top: 8px;")
        self._totals_grid.addWidget(lbl, row, 0, 1, 2)
        return row + 1

    def _add_kv(self, row: int, k: str, v: str, *, bold_value: bool = False) -> int:
        lk = QLabel(k)
        lv = QLabel(v)
        lk.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lv.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        if bold_value:
            lv.setStyleSheet("font-weight: 700;")
        self._totals_grid.addWidget(lk, row, 0)
        self._totals_grid.addWidget(lv, row, 1)
        return row + 1

    def _on_period_changed(self, year: int, month: int):
        self.period_year = year
        self.period_month = month
        try:
            self.refresh()
        except Exception:
            pass

    def refresh_driver_combo(self, prefer_truck_id=None, prefer_driver_id=None):
        """Safe no-op for compatibility with pages calling this at init.
        Summary currently doesn't use a driver combo; if added later,
        this method can populate it from self.model.active_drivers().
        """
        return

    def __init__(self, model: TrucksModel):
        super().__init__()
        self.model = model
        self.period_year: Optional[int] = None
        self.period_month: int = 0

        root = QVBoxLayout(self)

        title = QLabel("Σύνοψη")
        set_label_role(title, "section-title")
        root.addWidget(title)

        # Μπάρα Περιόδου για τη Σύνοψη
        self.period_bar = PeriodBar(on_changed=self._on_period_changed)
        root.addWidget(self.period_bar)

        filters = QGroupBox("Φίλτρα")
        grid = QGridLayout(filters)
        self.ed_from = QLineEdit("")
        self.ed_to = QLineEdit("")
        self.cb_truck = QComboBox()
        self.btn_apply = QPushButton("Εφαρμογή")
        set_button_role(self.btn_apply, "primary")

        grid.addWidget(QLabel("Από (ημ/νία):"), 0, 0)
        grid.addWidget(self.ed_from, 0, 1)
        grid.addWidget(QLabel("Έως (ημ/νία):"), 0, 2)
        grid.addWidget(self.ed_to, 0, 3)

        grid.addWidget(QLabel("Φορτηγό:"), 1, 0)
        grid.addWidget(self.cb_truck, 1, 1)
        grid.addWidget(self.btn_apply, 1, 3)

        self.sp_wear_default = QDoubleSpinBox()
        self.sp_wear_default.setRange(0, 10.0)
        self.sp_wear_default.setDecimals(3)
        self.sp_wear_default.setSingleStep(0.01)
        try:
            self.sp_wear_default.setValue(float(getattr(self.model, "wear_rate_per_km", 0.10) or 0.10))
        except Exception:
            self.sp_wear_default.setValue(0.10)
        self.sp_wear_default.setToolTip("Προεπιλογή φθοράς ανά χλμ (ισχύει αν δεν υπάρχει override στο φορτηγό).")
        grid.addWidget(QLabel("Φθορά €/χλμ (default):"), 2, 0)
        grid.addWidget(self.sp_wear_default, 2, 1)
        grid.addWidget(QLabel("0 = απενεργοποίηση"), 2, 2)

        root.addWidget(filters)

        # Πάνω γραμμή: βασικά στοιχεία
        self.lbl_head = QLabel("")
        set_label_role(self.lbl_head, "summary-body")
        root.addWidget(self.lbl_head)

        # Κάτω μέρος: "όμορφη" σύνοψη τύπου KPI list (όχι table)
        self.summary_box = QGroupBox("Συγκεντρωτικά")
        vbox = QVBoxLayout(self.summary_box)

        self._totals_wrap = QWidget()
        self._totals_grid = QGridLayout(self._totals_wrap)
        self._totals_grid.setColumnStretch(0, 1)
        self._totals_grid.setColumnStretch(1, 0)
        self._totals_grid.setHorizontalSpacing(14)
        self._totals_grid.setVerticalSpacing(6)

        self._scroll_totals = QScrollArea()
        self._scroll_totals.setWidgetResizable(True)
        self._scroll_totals.setFrameShape(QFrame.NoFrame)
        self._scroll_totals.setWidget(self._totals_wrap)
        vbox.addWidget(self._scroll_totals)
        root.addWidget(self.summary_box, 1)

        self.btn_apply.clicked.connect(self.refresh)
        try:
            self.cb_truck.currentIndexChanged.connect(self.refresh)
        except Exception:
            pass
        try:
            self.sp_wear_default.valueChanged.connect(self._on_wear_changed)
        except Exception:
            pass
        self.refresh_truck_combo()
        self.refresh_driver_combo()
        self.refresh()

    def set_period(self, year: int, month: int):
        self.period_year = year
        self.period_month = int(month or 0)
        self.refresh()

    def _in_period(self, d: date) -> bool:
        if self.period_year is None:
            return True
        if d.year != self.period_year:
            return False
        if self.period_month in (0, None):
            return True
        return d.month == self.period_month

    def suggest_date_for_period(self, year: int, month: int):
        # Μην αλλάζεις αν ο χρήστης έχει ήδη βάλει κάτι μη-κενό
        if month and self.ed_date.text().strip() == "":
            self.ed_date.setText(date(year, month, 1).strftime("%d/%m/%Y"))

    def refresh_truck_combo(self):
        current = self.cb_truck.currentData()
        self.cb_truck.blockSignals(True)
        self.cb_truck.clear()
        self.cb_truck.addItem("Όλα", None)
        for t in sorted(self.model.trucks, key=lambda x: x.plate.lower()):
            self.cb_truck.addItem(t.plate, t.tid)
        # Επαναφορά επιλογής χωρίς να πυροδοτείται currentIndexChanged
        if current is not None:
            idx = self.cb_truck.findData(current)
            if idx >= 0:
                self.cb_truck.setCurrentIndex(idx)
        self.cb_truck.blockSignals(False)

    def refresh(self):
        d_from = None
        d_to = None
        # Αν έχουν συμπληρωθεί ρητά τα πεδία Από/Έως, αυτά υπερισχύουν.
        # Αλλιώς, χρησιμοποιούμε την επιλεγμένη περίοδο από τη μπάρα (year/month).
        if (not d_from) and (not d_to) and (self.period_year is not None):
            y = int(self.period_year)
            m = int(self.period_month or 0)
            if m == 0:
                # Όλο το έτος
                d_from = date(y, 1, 1)
                d_to = date(y, 12, 31)
            else:
                # Συγκεκριμένος μήνας
                import calendar as _cal
                last_day = _cal.monthrange(y, m)[1]
                d_from = date(y, m, 1)
                d_to = date(y, m, last_day)
        self.refresh_truck_combo()

        # Parse explicit date filters (override period bar)
        d_from = None
        d_to = None
        if self.ed_from.text().strip():
            try:
                d_from = parse_date(self.ed_from.text())
            except Exception:
                d_from = None
        if self.ed_to.text().strip():
            try:
                d_to = parse_date(self.ed_to.text())
            except Exception:
                d_to = None

        # If no explicit filters, use PeriodBar (year/month)
        if (not d_from) and (not d_to) and (self.period_year is not None):
            y = int(self.period_year)
            m = int(self.period_month or 0)
            if m == 0:
                d_from = date(y, 1, 1)
                d_to = date(y, 12, 31)
            else:
                import calendar as _cal
                last_day = _cal.monthrange(y, m)[1]
                d_from = date(y, m, 1)
                d_to = date(y, m, last_day)

        tid = self.cb_truck.currentData()
        if tid is not None:
            tid = int(tid)
        trips = self.model.trips
        fuels = self.model.fuels
        if tid is not None:
            trips = [t for t in trips if t.truck_id == tid]
            fuels = [f for f in fuels if f.truck_id == tid]

        def in_range(d: date) -> bool:
            if d_from and d < d_from:
                return False
            if d_to and d > d_to:
                return False
            return True

        trips = [t for t in trips if in_range(t.trip_date)]
        fuels = [f for f in fuels if in_range(f.fuel_date)]

        total_km = sum(t.trip_km for t in trips)
        total_rev = sum(t.revenue for t in trips)
        total_other_expenses = total_rev * 0.05
        total_commission = sum((t.revenue * (getattr(t, "commission_percent", 0.0) or 0.0) / 100.0) for t in trips)
        total_tolls = sum((getattr(t, "toll_amount", 0.0) or 0.0) for t in trips)
        total_wear = sum((float(getattr(t, "trip_km", 0) or 0) * float(self.model.wear_rate_for_truck(getattr(t, "truck_id", None)) or 0.0)) for t in trips)
        total_liters = sum(f.liters for f in fuels)
        total_cost_fuel = sum((float(f.liters or 0.0) * float(f.cost or 0.0)) for f in fuels)


        # --- Πάγια μηνιαία έξοδα ---
        def month_diff_inclusive(d1: date, d2: date) -> int:
            y1, m1 = d1.year, d1.month
            y2, m2 = d2.year, d2.month
            return (y2 - y1) * 12 + (m2 - m1) + 1

        # Προσδιορισμός εύρους μηνών
        if d_from and d_to:
            range_start, range_end = d_from, d_to
        else:
            # Αν δεν δόθηκε εύρος, παράγουμε από τα δεδομένα που φαίνονται στο φίλτρο
            dates = [t.trip_date for t in trips] + [f.fuel_date for f in fuels]
            if dates:
                range_start, range_end = min(dates), max(dates)
            else:
                # Χωρίς δεδομένα/φίλτρο, θεωρούμε τον τρέχοντα μήνα μόνο
                today = date.today()
                range_start = date(today.year, today.month, 1)
                range_end = range_start
        months_in_range = month_diff_inclusive(range_start, range_end)

        # Υπολογισμός παγίων για συγκεκριμένο φορτηγό ή για όλα
        if tid is not None:
            t = self.model.truck_by_id(tid)
            fixed_per_month = float(getattr(t, "fixed_monthly_expenses", 0.0) or 0.0) if t else 0.0
            # Επιλέγουμε να μετράμε τα πάγια μόνο για ενεργά φορτηγά
            if t and not t.active:
                fixed_per_month = 0.0
            total_fixed = fixed_per_month * months_in_range
        else:
            total_fixed = 0.0
            for t in self.model.trucks:
                if not t.active:
                    continue
                fixed_per_month = float(getattr(t, "fixed_monthly_expenses", 0.0) or 0.0)
                if fixed_per_month > 0:
                    total_fixed += fixed_per_month * months_in_range

        # --- Κόστος οδηγών ---
        # ΣΗΜΑΝΤΙΚΟ: οι μισθοί/ένσημα έχουν ιστορικό ανά μήνα (pay_history).
        # Άρα, όταν έχεις φίλτρο για συγκεκριμένο μήνα (ή εύρος που καλύπτει πολλούς μήνες),
        # πρέπει να υπολογίζονται *ανά μήνα* με βάση το ιστορικό.

        def _iter_months(start: date, end: date):
            y, m = start.year, start.month
            while (y, m) <= (end.year, end.month):
                yield y, m
                m += 1
                if m > 12:
                    m = 1
                    y += 1

        total_stamps = 0.0
        total_salary = 0.0

        # Αν έχει επιλεγεί συγκεκριμένο φορτηγό, μετράμε μισθό/ένσημα ΜΟΝΟ για τον οδηγό που είναι σεταρισμένος στο φορτηγό.
        # Αν δεν υπάρχει οδηγός (main_driver_id=None) → 0 (όπως ζήτησες).
        selected_driver = None
        if tid is not None:
            t_sel = self.model.truck_by_id(tid)
            did = getattr(t_sel, "main_driver_id", None) if t_sel else None
            if did is not None:
                for _d in self.model.drivers:
                    if _d.did == int(did):
                        if getattr(_d, 'active', True):
                            selected_driver = _d
                        break

        drivers_iter = [selected_driver] if selected_driver is not None else ([] if tid is not None else [d for d in self.model.drivers if getattr(d, 'active', True)])

        for yy, mm in _iter_months(range_start, range_end):
            ym = f"{yy:04d}-{mm:02d}"
            stamps_month = 0.0
            salary_month = 0.0
            for d in drivers_iter:
                snap = driver_pay_for_month(d, ym)
                stamps_month += float(snap.get('stamp_cost', 0.0) or 0.0)
                mode = str(snap.get('pay_mode', getattr(d, 'pay_mode', 'monthly')) or 'monthly')
                if mode == 'monthly':
                    salary_month += float(snap.get('salary', 0.0) or 0.0)
            total_stamps += stamps_month
            total_salary += salary_month

        # Αμοιβές ανά δρομολόγιο: έρχονται από τα trips
        total_per_trip_pay = sum(float(getattr(t, 'driver_pay', 0.0) or 0.0) for t in trips)

        total_cost = total_cost_fuel + total_commission + total_tolls + total_wear + total_fixed + total_other_expenses + total_stamps + total_salary + total_per_trip_pay
        net = total_rev - total_cost


        # Εμφάνιση (ανανεωμένη): κεφαλίδα + πίνακας συγκεντρωτικών
        name = "Όλα" if tid is None else self.model.truck_label(tid)
        period_txt = ""
        if d_from or d_to:
            p1 = d_from.strftime("%d/%m/%Y") if d_from else ""
            p2 = d_to.strftime("%d/%m/%Y") if d_to else ""
            period_txt = f" — Περίοδος: {p1} έως {p2}"
        self.lbl_head.setText(f"<b>Φορτηγό:</b> {name}{period_txt}")

        # --- Όμορφη εμφάνιση κάτω: grouped KPI list ---
        self._clear_totals()
        r = 0
        r = self._add_section(r, "Κίνηση")
        r = self._add_kv(r, "Δρομολόγια", f"{len(trips)}")
        r = self._add_kv(r, "Σύνολο χλμ", f"{total_km}")
        r = self._add_kv(r, "Σύνολο λίτρων", f"{total_liters:.2f}")

        r = self._add_section(r, "Έσοδα")
        r = self._add_kv(r, "Σύνολο εσόδων", fmt_eur(total_rev), bold_value=True)

        r = self._add_section(r, "Κόστη")
        r = self._add_kv(r, "Καύσιμα/Έξοδα", fmt_eur(total_cost_fuel))
        r = self._add_kv(r, "Λοιπά έξοδα (5%)", fmt_eur(total_other_expenses))
        r = self._add_kv(r, "Προμήθειες", fmt_eur(total_commission))
        r = self._add_kv(r, "Διόδια", fmt_eur(total_tolls))
        r = self._add_kv(r, "Φθορές (€/χλμ)", fmt_eur(total_wear))
        r = self._add_kv(r, "Πάγια μηνιαία", fmt_eur(total_fixed))
        r = self._add_kv(r, "Ένσημα οδηγών", fmt_eur(total_stamps))
        r = self._add_kv(r, "Μισθοί οδηγών (μηνιαίοι)", fmt_eur(total_salary))
        r = self._add_kv(r, "Αμοιβές οδηγών (ανά δρομολόγιο)", fmt_eur(total_per_trip_pay))
        r = self._add_kv(r, "Σύνολο κόστους", fmt_eur(total_cost), bold_value=True)

        r = self._add_section(r, "Αποτέλεσμα")
        r = self._add_kv(r, "Καθαρό (έσοδα - κόστος)", fmt_eur(net), bold_value=True)

class TruckWindow(QMainWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("YOURTRANS OIL — Φορτηγά")
        self.resize(1100, 700)

        self.model = TrucksModel()
        self.model.load()
        if DEBUG: print('[Trucks] loaded:', len(self.model.trucks)
, 'trucks,', len(self.model.trips), 'trips,', len(self.model.fuels), 'fuels')

        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        main.addWidget(make_section_bar(self.controller, current="trucks"))

        tools = QWidget()
        tools.setObjectName("ToolsBar")
        tools_layout = QHBoxLayout(tools)
        tools_layout.setContentsMargins(10, 8, 10, 8)
        tools_layout.setSpacing(8)

        open_folder_btn = QPushButton("🗂 Άνοιγμα φακέλου δεδομένων")
        set_button_role(open_folder_btn, "secondary")
        open_folder_btn.clicked.connect(open_data_folder_in_finder)

        tools_layout.addWidget(open_folder_btn)
        tools_layout.addStretch(1)
        main.addWidget(tools)

        # nav + pages
        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)

        self.page_registry = TruckRegistryPage(self.model, on_changed=self._on_data_changed)
        self.page_trips = TripsPage(self.model, on_changed=self._on_data_changed)
        self.page_fuel = FuelPage(self.model, on_changed=self._on_data_changed)
        self.page_summary = TrucksSummaryPage(self.model)
        self.page_drivers = DriversPage(self.model, on_changed=self._on_data_changed)

        self.stack.addWidget(self.page_registry)
        self.stack.addWidget(self.page_trips)
        self.stack.addWidget(self.page_fuel)
        self.stack.addWidget(self.page_summary)
        self.stack.addWidget(self.page_drivers)

        # top nav under section bar
        self.nav_container = QWidget()
        nav_layout = QVBoxLayout(self.nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav = make_trucks_nav(
            on_go_registry=self.go_registry,
            on_go_trips=self.go_trips,
            on_go_fuel=self.go_fuel,
            on_go_summary=self.go_summary,
            on_go_drivers=self.go_drivers,
            current="registry"
        )
        nav_layout.addWidget(self.nav)
        main.insertWidget(1, self.nav_container)




        self.model.attach_autosave_timer(self, self.save_now)
        self.go_registry()

    def _on_data_changed(self):
        # Μετά από αλλαγές, ανανεώνουμε και τη σύνοψη.
        self.page_summary.refresh()

        # Και ανανεώνουμε τα φίλτρα περιόδου στις λίστες
        self.page_trips.refresh()
        self.page_fuel.refresh()

    def on_period_changed(self, year: int, month: int):
        """Εφαρμόζει φίλτρο περιόδου σε Δρομολόγια/Έξοδα/Σύνοψη."""
        self._period_year, self._period_month = year, month
        self.page_trips.set_period(year, month)
        self.page_fuel.set_period(year, month)
        self.page_summary.set_period(year, month)
        # Προαιρετικά: προεπιλογή ημερομηνίας εισαγωγής μέσα στον μήνα
        self.page_trips.suggest_date_for_period(year, month)
        self.page_fuel.suggest_date_for_period(year, month)


    def _set_nav(self, current: str):
        # αντικατάσταση nav για να ενημερωθούν enabled states
        layout = self.nav_container.layout()
        old = self.nav
        self.nav = make_trucks_nav(
            on_go_registry=self.go_registry,
            on_go_trips=self.go_trips,
            on_go_fuel=self.go_fuel,
            on_go_summary=self.go_summary,
            on_go_drivers=self.go_drivers,
            current=current
        )
        layout.replaceWidget(old, self.nav)
        old.setParent(None)

    def go_registry(self):
        self._set_nav("registry")
        self.page_registry.refresh()
        self.stack.setCurrentWidget(self.page_registry)

    def go_trips(self):
        self._set_nav("trips")
        self.page_trips.refresh()
        self.stack.setCurrentWidget(self.page_trips)

    def go_fuel(self):
        self._set_nav("fuel")
        self.page_fuel.refresh()
        self.stack.setCurrentWidget(self.page_fuel)

    def go_summary(self):
        self._set_nav("summary")
        try:
            if hasattr(self, 'period_bar'):
                y, m = self.period_bar.current_period()
                if hasattr(self.page_summary, '_on_period_changed'):
                    self.page_summary._on_period_changed(y, m)
        except Exception:
            pass
        self.page_summary.refresh()
        self.stack.setCurrentWidget(self.page_summary)

    def go_drivers(self):
        self._set_nav("drivers")
        try:
            self.page_drivers.refresh()
        except Exception:
            pass
        self.stack.setCurrentWidget(self.page_drivers)

    def save_now(self):
        ok = self.model.save()
        if not ok:
            QMessageBox.warning(self, "Σφάλμα", "Αποτυχία αποθήκευσης δεδομένων Φορτηγών.")



# -----------------------------
# Theme
# -----------------------------

def apply_white_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget {
            background: #eef2f7;
            color: #162033;
            font-size: 13px;
            font-family: "Manrope", "Segoe UI", "Noto Sans", sans-serif;
        }

        QMainWindow, QDialog {
            background: qlineargradient(
                x1: 0, y1: 0, x2: 1, y2: 1,
                stop: 0 #f8fbff,
                stop: 1 #eef2f7
            );
        }

        QGroupBox {
            background: #ffffff;
            border: 1px solid #dbe3ee;
            border-radius: 14px;
            margin-top: 14px;
            padding: 10px 12px 12px 12px;
            font-weight: 700;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
            color: #0f3d7a;
            background: #ffffff;
        }

        QLineEdit,
        QSpinBox,
        QDoubleSpinBox,
        QComboBox {
            background: #ffffff;
            border: 1px solid #ccd8e7;
            border-radius: 10px;
            padding: 6px 10px;
            min-height: 20px;
            selection-background-color: #bcd6ff;
            selection-color: #101b2d;
        }

        QLineEdit:focus,
        QSpinBox:focus,
        QDoubleSpinBox:focus,
        QComboBox:focus {
            border: 2px solid #2f7cf6;
            padding: 5px 9px;
        }

        QComboBox QAbstractItemView {
            background: #ffffff;
            border: 1px solid #ccd8e7;
            outline: 0;
            selection-background-color: #d5e4ff;
            selection-color: #10213c;
        }

        QPushButton {
            background: #ffffff;
            color: #18365d;
            border: 1px solid #ccd8e7;
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }

        QPushButton:hover {
            background: #f3f7ff;
            border-color: #9ebcf3;
        }

        QPushButton:pressed {
            background: #dce9ff;
            border-color: #7da5f0;
        }

        QPushButton:disabled {
            color: #94a5bf;
            background: #f4f7fb;
            border-color: #dde5f1;
        }

        QPushButton[role="primary"] {
            background: #1565d8;
            color: #ffffff;
            border-color: #1158bc;
        }

        QPushButton[role="primary"]:hover {
            background: #0f58c2;
            border-color: #0c4da9;
        }

        QPushButton[role="primary"]:pressed {
            background: #0a4cae;
            border-color: #093f90;
        }

        QPushButton[role="secondary"] {
            background: #ecf3ff;
            color: #11418a;
            border-color: #c1d7ff;
        }

        QPushButton[role="soft"] {
            background: #f4f7fb;
            color: #1f3c61;
            border-color: #d9e3f2;
        }

        QPushButton[role="danger"] {
            background: #fff1f1;
            color: #a12626;
            border-color: #f2bcbc;
        }

        QPushButton[role="danger"]:hover {
            background: #ffdfe0;
            border-color: #e9a2a3;
        }

        QPushButton[role="nav"] {
            background: #ffffff;
            color: #20446f;
            border: 1px solid #d5e0ee;
            border-radius: 11px;
            padding: 7px 14px;
            font-weight: 600;
        }

        QPushButton[role="nav"]:disabled {
            background: #dbe9ff;
            color: #103d8a;
            border: 1px solid #9dbff5;
        }

        QPushButton[role="choice"] {
            font-size: 15px;
            font-weight: 700;
            padding: 12px 18px;
            border-radius: 14px;
            background: #edf4ff;
            border: 1px solid #bfd6fb;
            color: #133a6e;
        }

        QPushButton[role="choice"]:hover {
            background: #dceafe;
            border-color: #9ec0f5;
        }

        QLabel[role="section-title"] {
            font-size: 20px;
            font-weight: 800;
            color: #0f3d7a;
            background: transparent;
        }

        QLabel[role="subtitle"] {
            color: #4a5e7d;
            font-size: 13px;
            background: transparent;
        }

        QLabel[role="summary-total"] {
            font-size: 16px;
            font-weight: 800;
            color: #0d3e80;
            background: transparent;
        }

        QLabel[role="metric"] {
            color: #2a4f80;
            font-weight: 700;
            background: transparent;
        }

        QLabel[role="summary-body"] {
            color: #1f3557;
            background: transparent;
        }

        QTabWidget::pane {
            border: 1px solid #d9e3f1;
            border-radius: 12px;
            background: #ffffff;
            top: -1px;
        }

        QTabBar::tab {
            background: #ecf2fb;
            color: #385379;
            border: 1px solid #d3dfef;
            border-bottom: 0;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            padding: 8px 14px;
            margin-right: 4px;
            min-width: 120px;
            font-weight: 600;
        }

        QTabBar::tab:selected {
            background: #ffffff;
            color: #123f7f;
            border-color: #aec7ea;
        }

        QTabBar::tab:hover:!selected {
            background: #e4edf9;
            border-color: #bed0ea;
        }

        QTableWidget {
            background: #ffffff;
            border: 1px solid #d9e3f1;
            border-radius: 12px;
            gridline-color: #e8edf5;
            alternate-background-color: #f8fbff;
            selection-background-color: #d5e5ff;
            selection-color: #0f2746;
        }

        QTableWidget::item {
            padding: 4px;
        }

        QTableWidget::item:selected {
            background: #cfe1ff;
            color: #0f2746;
        }

        QHeaderView::section {
            background: #edf3fc;
            color: #173a67;
            border: 0;
            border-right: 1px solid #dde6f2;
            border-bottom: 1px solid #dde6f2;
            padding: 8px 6px;
            font-weight: 700;
        }

        QScrollBar:vertical {
            background: #eef3fa;
            width: 12px;
            margin: 2px;
            border-radius: 6px;
        }

        QScrollBar::handle:vertical {
            background: #c4d3e9;
            min-height: 24px;
            border-radius: 6px;
        }

        QScrollBar::handle:vertical:hover {
            background: #a9bfdc;
        }

        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            height: 0px;
        }

        QScrollBar:horizontal {
            background: #eef3fa;
            height: 12px;
            margin: 2px;
            border-radius: 6px;
        }

        QScrollBar::handle:horizontal {
            background: #c4d3e9;
            min-width: 24px;
            border-radius: 6px;
        }

        QScrollBar::handle:horizontal:hover {
            background: #a9bfdc;
        }

        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {
            width: 0px;
        }

        QToolTip {
            background: #ffffff;
            color: #15345f;
            border: 1px solid #bccde6;
            padding: 6px 8px;
        }

        #TopSwitchBar,
        #ModuleNavBar,
        #ToolsBar {
            background: #ffffff;
            border: 1px solid #d8e3f1;
            border-radius: 12px;
        }

        #StartDialog {
            background: #f3f7fd;
            border-radius: 14px;
        }
    """)


# -----------------------------
# App entry
# -----------------------------

def main():
    app = QApplication(sys.argv)
    # Ορισμός ονόματος εφαρμογής ώστε τα AppData paths να είναι σωστά (όχι "python3").
    QCoreApplication.setOrganizationName("YOURTRANS")
    QCoreApplication.setApplicationName(APP_NAME)
    apply_white_theme(app)

    controller = AppController()
    controller.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

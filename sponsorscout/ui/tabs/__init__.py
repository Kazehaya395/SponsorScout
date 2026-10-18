"""Per-tab UI builders for SponsorScout (PySide6 restart).

The planned split documented by the Tkinter-era placeholder is now real:
each tab is a self-contained QWidget that receives its dependencies through
constructor arguments (db_path / coordinator / refresh callbacks), so tabs
can be reasoned about and tested in isolation.  ``ui/app.py`` only wires
them together.
"""

from sponsorscout.ui.tabs.applications import ApplicationsTab
from sponsorscout.ui.tabs.dashboard import DashboardTab
from sponsorscout.ui.tabs.data_management import DataManagementTab
from sponsorscout.ui.tabs.search import SearchTab
from sponsorscout.ui.tabs.tailoring import TailoringTab
from sponsorscout.ui.tabs.tools import ToolsTab

__all__ = [
    "ApplicationsTab",
    "DashboardTab",
    "DataManagementTab",
    "SearchTab",
    "TailoringTab",
    "ToolsTab",
]

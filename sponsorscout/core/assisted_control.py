"""Worker-safe commands for an already explicitly opened Assisted browser.

UI callbacks set events only. All Playwright calls and verification reads stay
on its owning worker. Continue asks for a fresh check; it never certifies a
CAPTCHA, resets deadlines, performs a click, or silently starts a new worker.
"""
from __future__ import annotations
import threading


class AssistedControl:
    def __init__(self,on_attention=None):
        self.on_attention=on_attention
        self._show=threading.Event();self._check=threading.Event();self._save=threading.Event()
        self.waiting=False
    def show_browser(self):self._show.set()
    def continue_check(self):self._check.set()
    def save_partial(self):self._save.set()
    @property
    def save_requested(self):return self._save.is_set()
    def attention(self,info):
        self.waiting=bool(info.get('waiting'))
        if callable(self.on_attention):self.on_attention(dict(info))
    def poll(self,page):
        if self._show.is_set():
            self._show.clear()
            try:page.bring_to_front()
            except Exception:pass
        if self._check.is_set():self._check.clear();return 'check'
        return 'save'if self.save_requested else''

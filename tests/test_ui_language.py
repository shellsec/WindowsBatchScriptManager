import importlib
import re
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from ui_language import LANGUAGE_OPTIONS, LOCALE_MODULES, language_for_locale


class LanguageTests(unittest.TestCase):
    def test_system_language_mapping(self):
        cases = {
            "zh-TW": "zh-TW", "zh-HK": "zh-TW", "zh-Hant": "zh-TW",
            "zh_CN": "zh-CN", "zh-SG": "zh-CN", "ja": "ja-JP", "ko_KR": "ko-KR",
            "de-AT": "de-DE", "fr-CA": "fr-FR", "es-MX": "es-ES",
            "pt_BR.UTF-8": "pt-BR", "ru": "ru-RU", "it-IT": "en-US",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(language_for_locale(name), expected)

    def test_legacy_preference_and_follow_system(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "language.json"
            with patch.object(main, "LANGUAGE_CONFIG_PATH", path), patch.object(
                main, "detect_system_language", return_value="ja-JP"
            ):
                self.assertEqual(main.load_language(), "ja-JP")
                path.write_text('{"language":"zh_CN"}', encoding="utf-8")
                self.assertEqual(main.load_language(), "zh-CN")
                main.save_language("system")
                self.assertEqual(main.load_language(), "ja-JP")

    def test_translation_placeholders_and_fallback(self):
        pattern = re.compile(r"%(?:\([^)]+\))?[#0 +\-]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa%]")
        english = main.LANGUAGES["en-US"]
        for code, module_name in LOCALE_MODULES.items():
            translated = importlib.import_module(module_name).TRANSLATIONS
            for key, value in english.items():
                with self.subTest(language=code, key=key):
                    result = main.LANGUAGES[code][key]
                    self.assertEqual(result, translated.get(key, value))
                    self.assertEqual(pattern.findall(result), pattern.findall(value))

    def test_toolbar_wraps_long_labels(self):
        root = tk.Tk()
        try:
            root.geometry("310x160")
            row = tk.Frame(root)
            row.pack(fill=tk.X)
            widgets = []
            for code in ("ja-JP", "ko-KR", "de-DE", "ru-RU"):
                button = tk.Button(row, text=main.LANGUAGES[code]["btn_start_stopped"])
                button.pack(side=tk.LEFT)
                widgets.append(button)
            root.update()
            app = main.BatchManagerApp.__new__(main.BatchManagerApp)
            app._wrap_toolbar_row(row)
            root.update()
            self.assertGreater(widgets[-1].winfo_y(), widgets[0].winfo_y())
            for widget in widgets:
                self.assertEqual(widget.winfo_width(), widget.winfo_reqwidth())
                self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), row.winfo_width())
        finally:
            root.destroy()

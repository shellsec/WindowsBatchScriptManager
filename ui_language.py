"""Desktop locale selection and English fallback for translated UI strings."""
import importlib
import locale
import os
import sys

LANGUAGE_OPTIONS = {
    "en-US": "English",
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "ja-JP": "日本語",
    "ko-KR": "한국어",
    "de-DE": "Deutsch",
    "fr-FR": "Français",
    "es-ES": "Español",
    "pt-BR": "Português (Brasil)",
    "ru-RU": "Русский",
}
DEFAULT_LANGUAGE = "en-US"
LOCALE_MODULES = {
    code: f"locale_{code.lower().replace('-', '_')}"
    for code in LANGUAGE_OPTIONS
    if code not in ("en-US", "zh-CN")
}
_INLINE_CATALOGS = {}
_PRIMARY_LANGUAGES = {code.split("-")[0]: code for code in LANGUAGE_OPTIONS}
_PRIMARY_LANGUAGES["zh"] = "zh-CN"


def normalize_language_preference(value):
    """Read legacy underscore tags and canonicalize supported saved preferences."""
    if not isinstance(value, str):
        return "system"
    normalized = value.replace("_", "-").lower()
    return next((code for code in LANGUAGE_OPTIONS if code.lower() == normalized), "system")


def language_for_locale(value):
    """Map Windows and POSIX locale names to one of the supported languages."""
    parts = value.split(".", 1)[0].split("@", 1)[0].replace("_", "-").lower().split("-")
    if parts[0] == "zh":
        return "zh-TW" if set(parts[1:]).intersection(("tw", "hk", "mo", "hant", "cht")) else "zh-CN"
    return _PRIMARY_LANGUAGES.get(parts[0], DEFAULT_LANGUAGE)


def detect_system_language():
    if sys.platform == "win32":
        try:
            import ctypes

            language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(language_id)
            if name:
                return language_for_locale(name)
        except (AttributeError, OSError):
            pass
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(key):
            return language_for_locale(os.environ[key])
    return language_for_locale(locale.getlocale()[0] or "")


def load_ui_catalogs(catalogs):
    """Overlay available translations on English for every supported language."""
    english = catalogs[DEFAULT_LANGUAGE]
    result = {code: {**english, **catalog} for code, catalog in catalogs.items()}
    for code, module_name in LOCALE_MODULES.items():
        module = importlib.import_module(module_name)
        result[code] = {**english, **module.TRANSLATIONS}
        _INLINE_CATALOGS[code] = getattr(module, "INLINE", {})
    return result


def translate_inline(language, chinese, english):
    if language == "zh-CN":
        return chinese
    return _INLINE_CATALOGS.get(language, {}).get(english, english)

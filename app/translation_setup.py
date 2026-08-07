"""Install free Argos offline translation packages used by the RSS worker.

This is deliberately best-effort: platform installation must still succeed if a
model mirror is temporarily unavailable, and the collector will retain source
text until the next successful update/install.
"""
from __future__ import annotations

LANGUAGES = ("en", "fr", "de", "es", "ru", "ar", "ja", "ko")


def install_models() -> list[str]:
    import argostranslate.package
    import argostranslate.translate

    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    installed = {language.code for language in argostranslate.translate.get_installed_languages()}
    results: list[str] = []
    for source in LANGUAGES:
        if source in installed and "zh" in installed:
            results.append(f"{source}->zh: already available")
            continue
        package = next((item for item in available if item.from_code == source and item.to_code == "zh"), None)
        if not package:
            results.append(f"{source}->zh: package unavailable")
            continue
        try:
            argostranslate.package.install_from_path(package.download())
            results.append(f"{source}->zh: installed")
        except Exception as exc:
            results.append(f"{source}->zh: {type(exc).__name__}")
    return results


if __name__ == "__main__":
    for result in install_models():
        print(result)

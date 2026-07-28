"""Static Mini App accessibility, copy, and privacy contract."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

HTML_PATH = Path("frontend/index.html")
CSS_PATH = Path("frontend/app.css")
APP_JS_PATH = Path("frontend/app.js")


class ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self.text.append(stripped)


def parse_contract() -> tuple[str, ContractParser]:
    source = HTML_PATH.read_text(encoding="utf-8")
    parser = ContractParser()
    parser.feed(source)
    return source, parser


def test_semantic_shell_and_native_controls_exist() -> None:
    source, parser = parse_contract()
    tags = [tag for tag, _ in parser.tags]
    inputs = [attrs for tag, attrs in parser.tags if tag == "input"]
    buttons = [attrs for tag, attrs in parser.tags if tag == "button"]

    assert "main" in tags
    assert "h1" in tags
    assert "h2" in tags
    assert "fieldset" in tags
    assert "legend" in tags
    assert any(
        item.get("type") == "radio" and item.get("name") == "color_id"
        for item in inputs
    )
    assert all("checked" not in item for item in inputs)
    assert any("disabled" in item for item in buttons)
    assert 'role="status"' in source
    assert 'aria-live="polite"' in source
    assert 'role="alert"' in source


def test_exact_safe_copy_and_external_assets() -> None:
    source, parser = parse_contract()
    visible = " ".join(parser.text)
    for copy in (
        "Выберите цвет",
        "Цвет применится ко всем видимым окрашенным частям авто.",
        "Палитра",
        "Фото готово",
        "Используем последнее принятое фото из чата.",
        "Выберите один вариант",
        "Открыть чат",
        "Загрузить палитру снова",
    ):
        assert copy in visible

    assert "<style" not in source
    assert not any(
        tag == "script" and attrs.get("src") is None for tag, attrs in parser.tags
    )
    scripts = [attrs.get("src") for tag, attrs in parser.tags if tag == "script"]
    assert "https://telegram.org/js/telegram-web-app.js" in scripts
    assert "./app.js" in scripts
    assert 'href="./app.css"' in source


def test_prohibited_controls_and_phase3_ui_are_absent() -> None:
    source, parser = parse_contract()
    input_types = {attrs.get("type") for tag, attrs in parser.tags if tag == "input"}
    lowered = source.lower()

    assert not {"file", "text", "color"} & input_types
    for forbidden in (
        "mask",
        "canvas",
        "preview",
        "modal",
        "dialog",
        "textarea",
        "запрос принят",
        "результат придёт",
        "queued",
        "running",
        "cancel",
        "отмен",
        "закрыть",
    ):
        assert forbidden not in lowered
    for palette_id in (
        "pearl-white",
        "charcoal",
        "deep-blue",
        "warm-red",
        "forest-green",
        "copper",
        "bright-yellow",
        "violet",
    ):
        assert palette_id not in source


def test_css_matches_spacing_responsive_focus_and_motion_contract() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")
    for required in (
        "--space-xs: 4px",
        "--space-sm: 8px",
        "--space-md: 16px",
        "--space-lg: 24px",
        "--space-xl: 32px",
        "--space-2xl: 48px",
        "--space-3xl: 64px",
        "min-height: 44px",
        "min-height: 52px",
        "min-height: 72px",
        "position: sticky",
        "var(--tg-viewport-stable-height, 100svh)",
        "outline: 3px solid var(--color-accent)",
        "outline: 2px solid var(--color-accent)",
        "width: 24px",
        "@media (max-width: 319px)",
        "@media (min-width: 600px)",
        "@media (min-width: 720px)",
        "prefers-reduced-motion: reduce",
        "grid-template-columns: repeat(2, minmax(0, 1fr))",
        "grid-template-columns: repeat(4, minmax(0, 1fr))",
    ):
        assert required in css
    assert "position: fixed" not in css
    assert "100vh" not in css
    assert "backdrop-filter" not in css


def test_frontend_has_no_embedded_privileged_or_storage_values() -> None:
    combined = HTML_PATH.read_text(encoding="utf-8") + (
        APP_JS_PATH.read_text(encoding="utf-8") if APP_JS_PATH.exists() else ""
    )
    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "initDataUnsafe",
        "document.cookie",
        "innerHTML",
        "telegram_user_id",
        "chat_id",
        "file_id",
        "file_unique_id",
        "image_url",
        "openrouter",
    ):
        assert forbidden not in combined

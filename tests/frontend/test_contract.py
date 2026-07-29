"""Static Mini App accessibility, copy, privacy, and visual contracts."""

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
        if stripped := data.strip():
            self.text.append(stripped)


def sources() -> tuple[str, str, str, ContractParser]:
    html = HTML_PATH.read_text(encoding="utf-8")
    parser = ContractParser()
    parser.feed(html)
    return (
        html,
        CSS_PATH.read_text(encoding="utf-8"),
        APP_JS_PATH.read_text(encoding="utf-8"),
        parser,
    )


def test_exact_three_mode_navigation_and_surprise_panel() -> None:
    html, _, _, parser = sources()
    tabs = [
        attrs
        for tag, attrs in parser.tags
        if tag == "button" and attrs.get("role") == "tab"
    ]

    assert [item.get("data-mode") for item in tabs] == [
        "colors",
        "users",
        "surprise",
    ]
    assert [item.get("aria-controls") for item in tabs] == [
        "colors-panel",
        "users-panel",
        "surprise-panel",
    ]
    assert "Colors User Colors Surprise" in " ".join(parser.text)
    assert 'role="tablist"' in html
    assert 'id="surprise-panel"' in html
    assert "Доверьте выбор ИИ" in html
    assert "Выбрать Surprise" in html
    assert 'data-kind="surprise"' not in html
    assert "Разворачиваю веера" in " ".join(parser.text)
    assert "Проверяем сессию" not in " ".join(parser.text)


def test_native_card_surfaces_flip_without_coupling_selection() -> None:
    html, css, js, parser = sources()
    warning = "Цвет на экране может отличаться от реальной плёнки."
    flip_surfaces = [
        attrs
        for tag, attrs in parser.tags
        if tag == "button"
        and "card-flip-surface" in (attrs.get("class") or "").split()
    ]

    assert "Kokonut UI Card Flip" in js
    assert len(flip_surfaces) == 2
    assert [surface.get("data-face") for surface in flip_surfaces] == [
        "front",
        "back",
    ]
    assert all(surface.get("type") == "button" for surface in flip_surfaces)
    assert all(
        surface.get("aria-expanded") == "false" for surface in flip_surfaces
    )
    assert 'class="select-button"' in html
    assert 'class="flip-button"' not in html
    assert 'class="back-button"' not in html
    assert "<article class=\"palette-card\"" in html
    assert 'role="button"' not in html
    assert 'data-flipped="false"' in html
    assert 'class="card-face card-back" aria-hidden="true" inert' in html

    assert html.count('class="card-circle"') == 10
    assert 'class="floating-circles" aria-hidden="true"' in html
    assert "@keyframes card-circle-float" in css
    assert "animation-delay: calc(var(--circle-index) * 300ms)" in css
    assert "top: 24%" in css
    assert "left: 24%" in css
    assert "var(--swatch-color)" in css

    assert "grid.addEventListener(\"click\", handleCardAction)" in js
    assert 'button.dataset.action === "select"' in js
    assert 'button.dataset.action === "flip"' in js
    assert 'grid.addEventListener("keydown"' not in js
    assert "findCardSurface" in js

    assert "backface-visibility: hidden" in css
    assert "-webkit-backface-visibility: hidden" in css
    assert "transform-style: preserve-3d" in css
    assert "-webkit-transform-style: preserve-3d" in css
    assert "-webkit-transform: rotateY(180deg)" in css
    assert "rotateY(180deg)" in css
    assert "isolation: isolate" in css
    assert "perspective: 1600px" in css
    assert "aspect-ratio: 4 / 5" in css
    assert ":hover" in css
    assert ":hover .card-inner" not in css

    assert "Палитра" not in html
    assert '"Палитра"' not in js
    assert html.count(warning) == 1
    assert warning not in html.split('<template id="choice-template">', 1)[1]
    assert html.index("</form>") < html.index(warning)

    reduced_motion = css.split(
        "@media (prefers-reduced-motion: reduce)", 1
    )[1]
    assert ".card-circle" in reduced_motion
    assert "animation: none" in reduced_motion
    assert "transition: none" in reduced_motion


def test_dark_premium_tokens_and_media_safety() -> None:
    _, css, js, _ = sources()
    for token in (
        "--color-bg: #07080d",
        "--color-surface: #10141e",
        "--color-elevated: #171c29",
        "--color-text: #f7f9ff",
        "--color-accent: #35f2ff",
        "--color-violet: #9a6cff",
        "--color-destructive: #ff5c7a",
        "radial-gradient",
        "prefers-reduced-motion: reduce",
    ):
        assert token in css.lower()

    assert "filter:" not in css
    assert "backdrop-filter" not in css
    assert "innerHTML" not in js
    assert "textContent" in js


def test_add_color_sheet_has_exact_disclosure_and_iphone_formats() -> None:
    _, _, _, parser = sources()
    visible = " ".join(parser.text)
    file_inputs = [
        attrs
        for tag, attrs in parser.tags
        if tag == "input" and attrs.get("type") == "file"
    ]

    assert "Добавить свой цвет" in visible
    assert (
        "Образец будет сохранён в вашем аккаунте и после проверки станет "
        "доступен другим пользователям."
    ) in visible
    assert "Фото автомобиля и результат мы не сохраняем." in visible
    assert "Изображение обработают сервис модерации и AI-провайдер." in visible
    assert "JPEG, PNG, WebP или HEIC/HEIF · до 8 МБ" in visible
    assert "Отправить на проверку" in visible
    assert file_inputs == [
        {
            "id": "color-image",
            "name": "image",
            "type": "file",
            "accept": (
                "image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
            ),
            "required": None,
        }
    ]


def test_admin_review_requires_explicit_concealed_preview_action() -> None:
    _, css, js, parser = sources()

    assert "Посмотреть" in js
    assert "preview_concealed" in js
    assert "/preview?reveal=true" in js
    assert ".admin-preview" in css
    assert not any(
        tag == "img" and "admin-preview" in (attrs.get("class") or "")
        for tag, attrs in parser.tags
    )


def test_accessibility_responsive_and_privacy_boundaries() -> None:
    html, css, js, parser = sources()
    buttons = [attrs for tag, attrs in parser.tags if tag == "button"]
    combined = html + js

    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'role="alert"' in html
    assert "min-height: 44px" in css
    assert "min-width: 44px" in css
    assert "outline: 3px solid var(--color-accent)" in css
    assert "@media (max-width: 349px)" in css
    assert "@media (min-width: 600px)" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
    assert any(item.get("disabled") is None for item in buttons)

    for forbidden in (
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "initDataUnsafe",
        "document.cookie",
        "telegram_user_id",
        "chat_id",
        "file_id",
        "file_unique_id",
        "openrouter",
        "react",
        "tailwind",
        "lucide",
    ):
        assert forbidden not in combined.lower()

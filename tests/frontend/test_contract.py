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
        if tag == "button" and "card-flip-surface" in (attrs.get("class") or "").split()
    ]

    assert "Kokonut UI Card Flip" in js
    assert len(flip_surfaces) == 2
    assert [surface.get("data-face") for surface in flip_surfaces] == [
        "front",
        "back",
    ]
    assert all(surface.get("type") == "button" for surface in flip_surfaces)
    assert all(surface.get("aria-expanded") == "false" for surface in flip_surfaces)
    assert 'class="select-button"' in html
    assert 'class="flip-button"' not in html
    assert 'class="back-button"' not in html
    assert '<article class="palette-card"' in html
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
    assert 'card.dataset.kind = item.kindLabel ? "user" : "builtin"' in js
    assert '.palette-card[data-kind="user"] .floating-circles' in css
    user_circles_rule = css.split(
        '.palette-card[data-kind="user"] .floating-circles',
        1,
    )[1].split("}", 1)[0]
    assert "display: none" in user_circles_rule

    assert 'grid.addEventListener("click", handleCardAction)' in js
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
    assert "perspective: 900px" in css
    assert "aspect-ratio: 4 / 5" in css
    assert ":hover" in css
    assert ":hover .card-inner" not in css
    flip_branch = js.split('if (button.dataset.action === "flip") {', 1)[1].split(
        "\n  }\n}", 1
    )[0]
    assert "syncCardFlip(colorId)" in flip_branch
    assert "render()" not in flip_branch

    assert "Палитра" not in html
    assert '"Палитра"' not in js
    assert html.count(warning) == 1
    assert warning not in html.split('<template id="choice-template">', 1)[1]
    assert html.index("</form>") < html.index(warning)

    reduced_motion = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".card-circle" in reduced_motion
    assert "animation: none" in reduced_motion
    assert "transition: none" in reduced_motion


def test_confirmed_selection_and_matching_reverse_artwork() -> None:
    html, css, js, _ = sources()
    template = html.split('<template id="choice-template">', 1)[1]
    back = template.split(
        '<div class="card-face card-back" aria-hidden="true" inert>',
        1,
    )[1].split(
        "</article>",
        1,
    )[0]

    assert 'id="confirm-color-dialog"' in html
    assert 'id="close-confirm-color"' in html
    assert 'id="confirm-color-selection"' in html
    assert ">Подтвердить</button>" in html
    assert 'id="confirm-color-copy"' not in html
    assert "ПОДТВЕРЖДЕНИЕ" not in html
    assert "Применить цвет" not in html + js
    select_branch = js.split('if (button.dataset.action === "select") {', 1)[1].split(
        'if (button.dataset.action === "flip") {', 1
    )[0]
    assert "openConfirmDialog(colorId)" in select_branch
    assert "selectChoice" not in select_branch
    assert "state = selectChoice(state, colorId)" in js
    confirm_branch = js.split(
        'elements.confirmSelection.addEventListener("click", async () => {',
        1,
    )[1].split(
        "elements.closeConfirm.addEventListener",
        1,
    )[0]
    assert "await submitSelectedChoice()" in confirm_branch
    assert "submitSelectedChoice()" not in select_branch
    assert "openConfirmDialog(state.surprise.color_id)" in js
    assert 'elements.form.addEventListener("submit"' not in js
    assert 'elements.confirmDialog.addEventListener("cancel"' in js
    assert 'elements.closeConfirm.addEventListener("click"' in js
    assert ".focus({preventScroll: true})" in js

    assert template.count('class="card-visual"') == 2
    assert template.count('class="swatch-field"') == 2
    assert template.count('class="reference-image"') == 2
    assert "floating-circles" not in back
    assert template.count('class="floating-circles" aria-hidden="true"') == 1

    assert 'fragment.querySelectorAll(".swatch-field")' in js
    assert 'fragment.querySelectorAll(".reference-image")' in js
    overlay = css.split(".card-back::after", 1)[1].split("}", 1)[0]
    for rule in (
        "position: absolute",
        "inset: 0",
        "rgb(0 0 0 /",
        "pointer-events: none",
    ):
        assert rule in overlay


def test_palette_removes_requested_chrome_and_keeps_chat_return() -> None:
    html, css, js, _ = sources()
    combined = html + js

    for removed in (
        "CARWRAP STUDIO",
        "Фото готово",
        "Используем последнее принятое фото из чата.",
        "Оклеить авто в этот цвет",
        "Выберите один вариант",
        "ПОДТВЕРЖДЕНИЕ",
        "Применить цвет",
    ):
        assert removed not in combined

    assert 'id="colors-count"' not in html
    assert 'id="privacy-copy"' not in html
    assert 'id="submit-button"' not in html
    assert 'id="action-hint"' not in html
    assert "Открыть чат" not in html
    assert html.count("Вернуться в чат") == 4
    assert ".confirm-color-dialog .icon-button" in css
    assert "top: var(--space-sm)" in css
    assert "right: var(--space-sm)" in css


def test_active_photo_card_opens_full_photo_and_requests_replacement() -> None:
    html, css, js, _ = sources()

    assert 'class="source-photo-card"' in html
    assert 'id="source-photo-thumbnail"' in html
    assert "Генерация для этого фото" in html
    assert 'id="replace-source-photo"' in html
    assert ">Заменить" in html
    assert 'id="source-photo-dialog"' in html
    assert 'id="source-photo-full"' in html
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 3fr)" in css
    assert "aspect-ratio: 1" in css
    assert "object-fit: cover" in css
    assert 'fetchJson("/api/v1/active-source/replacement"' in js
    assert "elements.sourcePhotoDialog.showModal()" in js
    assert "telegram.openTelegramLink(url)" in js


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

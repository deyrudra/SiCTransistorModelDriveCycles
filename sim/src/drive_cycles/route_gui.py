from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pygame


PANEL_BG = (27, 30, 34, 238)
FIELD_BG = (47, 51, 56)
FIELD_ACTIVE_BG = (58, 63, 69)
FIELD_BORDER = (105, 112, 120)
TEXT = (244, 246, 247)
MUTED = (177, 184, 191)
ACCENT = (73, 134, 235)
BUTTON_BG = (56, 94, 155)
BUTTON_HOVER = (67, 111, 181)
BUTTON_DISABLED = (65, 68, 73)
SELECTED_BG = (58, 92, 71)
DANGER_BG = (126, 66, 62)


@dataclass(frozen=True)
class GuiAction:
    kind: str
    value: Any = None


class RoutePlannerPanel:
    WIDTH = 430
    MARGIN = 14
    PAD = 14
    FIELD_H = 36
    BUTTON_H = 34
    CANDIDATE_H = 46

    def __init__(
        self,
        *,
        font: pygame.font.Font,
        small_font: pygame.font.Font,
    ) -> None:
        self.font = font
        self.small_font = small_font

        self.start_text = ""
        self.end_text = ""
        self.active_field: str | None = None

        self.status = (
            "Type Stuttgart start/destination addresses, then Search & Plan."
        )

        self.start_match_text = ""
        self.end_match_text = ""

        self.candidates: list[dict[str, Any]] = []
        self.selected_candidate_index: int | None = None

        self.busy = False
        self.visible = True

        self._rects: dict[str, pygame.Rect] = {}

    @property
    def text_input_active(self) -> bool:
        return self.active_field is not None

    def set_status(
        self,
        text: str,
        *,
        busy: bool | None = None,
    ) -> None:
        self.status = str(text)

        if busy is not None:
            self.busy = bool(busy)

    def clear_candidates(self) -> None:
        self.candidates = []
        self.selected_candidate_index = None
        self.start_match_text = ""
        self.end_match_text = ""

    def set_matches(
        self,
        start_text: str,
        end_text: str,
    ) -> None:
        self.start_match_text = start_text
        self.end_match_text = end_text

    def set_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> None:
        self.candidates = list(candidates)

        if self.candidates:
            self.selected_candidate_index = int(
                self.candidates[0]["index"]
            )
        else:
            self.selected_candidate_index = None

    def select_candidate(
        self,
        index: int,
    ) -> None:
        if any(
            int(item["index"]) == int(index)
            for item in self.candidates
        ):
            self.selected_candidate_index = int(index)

    def _layout(
        self,
        screen_w: int,
        screen_h: int,
    ) -> pygame.Rect:
        width = min(
            self.WIDTH,
            max(340, screen_w - 2 * self.MARGIN),
        )

        x = screen_w - width - self.MARGIN
        y = self.MARGIN

        candidate_count = max(
            1,
            len(self.candidates),
        )

        height = (
            14
            + 26
            + 22 + self.FIELD_H
            + 22 + self.FIELD_H
            + 8
            + self.BUTTON_H
            + 12
            + 34
            + candidate_count * self.CANDIDATE_H
            + 10
            + self.BUTTON_H
            + 10
        )

        height = min(
            height,
            screen_h - 2 * self.MARGIN,
        )

        panel = pygame.Rect(
            x,
            y,
            width,
            height,
        )

        inner_x = x + self.PAD
        inner_w = width - 2 * self.PAD
        cursor_y = y + 12

        self._rects["panel"] = panel

        cursor_y += 28
        cursor_y += 20

        self._rects["start"] = pygame.Rect(
            inner_x,
            cursor_y,
            inner_w,
            self.FIELD_H,
        )

        cursor_y += self.FIELD_H + 22

        self._rects["end"] = pygame.Rect(
            inner_x,
            cursor_y,
            inner_w,
            self.FIELD_H,
        )

        cursor_y += self.FIELD_H + 10

        self._rects["search"] = pygame.Rect(
            inner_x,
            cursor_y,
            inner_w,
            self.BUTTON_H,
        )

        cursor_y += self.BUTTON_H + 10

        self._rects["candidate_top"] = pygame.Rect(
            inner_x,
            cursor_y,
            inner_w,
            1,
        )

        cursor_y += 28

        for position, item in enumerate(
            self.candidates,
        ):
            index = int(
                item["index"]
            )

            self._rects[
                f"candidate_{index}"
            ] = pygame.Rect(
                inner_x,
                cursor_y
                + position * self.CANDIDATE_H,
                inner_w,
                self.CANDIDATE_H - 4,
            )

        cursor_y += (
            max(
                1,
                len(self.candidates),
            )
            * self.CANDIDATE_H
        )

        button_gap = 8
        half = (
            inner_w
            - button_gap
        ) // 2

        self._rects["drive"] = pygame.Rect(
            inner_x,
            cursor_y,
            half,
            self.BUTTON_H,
        )

        self._rects["clear"] = pygame.Rect(
            inner_x + half + button_gap,
            cursor_y,
            inner_w - half - button_gap,
            self.BUTTON_H,
        )

        return panel

    def handle_event(
        self,
        event: pygame.event.Event,
        screen_size: tuple[int, int],
    ) -> tuple[bool, GuiAction | None]:
        if not self.visible:
            return False, None

        self._layout(
            *screen_size
        )

        panel = self._rects["panel"]

        if event.type == pygame.MOUSEBUTTONDOWN:
            if event.button != 1:
                return (
                    panel.collidepoint(
                        event.pos
                    ),
                    None,
                )

            if not panel.collidepoint(
                event.pos
            ):
                self.active_field = None
                return False, None

            if self._rects["start"].collidepoint(
                event.pos
            ):
                self.active_field = "start"
                return True, None

            if self._rects["end"].collidepoint(
                event.pos
            ):
                self.active_field = "end"
                return True, None

            self.active_field = None

            if self._rects["search"].collidepoint(
                event.pos
            ):
                if not self.busy:
                    return True, GuiAction(
                        "search"
                    )
                return True, None

            for item in self.candidates:
                index = int(
                    item["index"]
                )

                rect = self._rects.get(
                    f"candidate_{index}"
                )

                if (
                    rect is not None
                    and rect.collidepoint(
                        event.pos
                    )
                ):
                    self.selected_candidate_index = index
                    return True, GuiAction(
                        "select_candidate",
                        index,
                    )

            if self._rects["drive"].collidepoint(
                event.pos
            ):
                if (
                    not self.busy
                    and self.selected_candidate_index
                    is not None
                ):
                    return True, GuiAction(
                        "drive_selected",
                        self.selected_candidate_index,
                    )

                return True, None

            if self._rects["clear"].collidepoint(
                event.pos
            ):
                return True, GuiAction(
                    "clear"
                )

            return True, None

        if event.type == pygame.KEYDOWN:
            if self.active_field is None:
                return False, None

            if event.key == pygame.K_TAB:
                self.active_field = (
                    "end"
                    if self.active_field == "start"
                    else "start"
                )
                return True, None

            if event.key in (
                pygame.K_RETURN,
                pygame.K_KP_ENTER,
            ):
                self.active_field = None

                if not self.busy:
                    return True, GuiAction(
                        "search"
                    )

                return True, None

            if event.key == pygame.K_ESCAPE:
                self.active_field = None
                return True, None

            target = (
                self.start_text
                if self.active_field == "start"
                else self.end_text
            )

            if event.key == pygame.K_BACKSPACE:
                target = target[:-1]

            elif (
                event.key == pygame.K_a
                and event.mod & pygame.KMOD_CTRL
            ):
                target = ""

            elif event.unicode and event.unicode.isprintable():
                if len(target) < 120:
                    target += event.unicode

            if self.active_field == "start":
                self.start_text = target
            else:
                self.end_text = target

            return True, None

        return False, None

    def _draw_text_clipped(
        self,
        surface: pygame.Surface,
        text: str,
        rect: pygame.Rect,
        *,
        color=TEXT,
        font: pygame.font.Font | None = None,
    ) -> None:
        font = font or self.small_font
        rendered = font.render(
            text,
            True,
            color,
        )

        clip = surface.get_clip()
        surface.set_clip(
            rect.inflate(
                -12,
                -4,
            )
        )

        surface.blit(
            rendered,
            (
                rect.x + 8,
                rect.centery
                - rendered.get_height() // 2,
            ),
        )

        surface.set_clip(
            clip
        )

    def _button(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        text: str,
        *,
        enabled: bool,
        danger: bool = False,
    ) -> None:
        hover = rect.collidepoint(
            pygame.mouse.get_pos()
        )

        if not enabled:
            color = BUTTON_DISABLED
        elif danger:
            color = DANGER_BG
        elif hover:
            color = BUTTON_HOVER
        else:
            color = BUTTON_BG

        pygame.draw.rect(
            surface,
            color,
            rect,
            border_radius=7,
        )

        label = self.small_font.render(
            text,
            True,
            TEXT,
        )

        surface.blit(
            label,
            label.get_rect(
                center=rect.center
            ),
        )

    def draw(
        self,
        surface: pygame.Surface,
    ) -> None:
        if not self.visible:
            return

        panel = self._layout(
            surface.get_width(),
            surface.get_height(),
        )

        panel_surface = pygame.Surface(
            panel.size,
            pygame.SRCALPHA,
        )

        panel_surface.fill(
            PANEL_BG
        )

        surface.blit(
            panel_surface,
            panel.topleft,
        )

        title = self.font.render(
            "ROUTE PLANNER",
            True,
            TEXT,
        )

        surface.blit(
            title,
            (
                panel.x + self.PAD,
                panel.y + 11,
            ),
        )

        labels = (
            (
                "START ADDRESS",
                "start",
                self.start_text,
            ),
            (
                "DESTINATION",
                "end",
                self.end_text,
            ),
        )

        for label_text, field, value in labels:
            rect = self._rects[field]

            label = self.small_font.render(
                label_text,
                True,
                MUTED,
            )

            surface.blit(
                label,
                (
                    rect.x,
                    rect.y - 18,
                ),
            )

            active = (
                self.active_field == field
            )

            pygame.draw.rect(
                surface,
                FIELD_ACTIVE_BG
                if active
                else FIELD_BG,
                rect,
                border_radius=6,
            )

            pygame.draw.rect(
                surface,
                ACCENT
                if active
                else FIELD_BORDER,
                rect,
                width=2
                if active
                else 1,
                border_radius=6,
            )

            placeholder = (
                "e.g. Schlossplatz, Stuttgart"
                if field == "start"
                else "e.g. Flughafen Stuttgart"
            )

            self._draw_text_clipped(
                surface,
                value or placeholder,
                rect,
                color=(
                    TEXT
                    if value
                    else MUTED
                ),
            )

        self._button(
            surface,
            self._rects["search"],
            "Searching / loading..."
            if self.busy
            else "Search & Plan",
            enabled=not self.busy,
        )

        candidate_top = self._rects[
            "candidate_top"
        ]

        status = self.status

        if len(status) > 62:
            status = status[:59] + "..."

        status_rendered = self.small_font.render(
            status,
            True,
            MUTED,
        )

        surface.blit(
            status_rendered,
            (
                candidate_top.x,
                candidate_top.y,
            ),
        )

        if self.candidates:
            for item in self.candidates:
                index = int(
                    item["index"]
                )

                rect = self._rects[
                    f"candidate_{index}"
                ]

                selected = (
                    index
                    == self.selected_candidate_index
                )

                pygame.draw.rect(
                    surface,
                    SELECTED_BG
                    if selected
                    else FIELD_BG,
                    rect,
                    border_radius=6,
                )

                pygame.draw.rect(
                    surface,
                    ACCENT
                    if selected
                    else FIELD_BORDER,
                    rect,
                    width=2
                    if selected
                    else 1,
                    border_radius=6,
                )

                line1 = (
                    f"Route {index}   "
                    f"{float(item['distance_km']):.2f} km   "
                    f"{float(item['time_min']):.1f} min"
                )

                overlap = float(
                    item.get(
                        "overlap",
                        0.0,
                    )
                )

                line2 = (
                    "Best-time route"
                    if index == 1
                    else f"Overlap with Route 1: {overlap * 100.0:.0f}%"
                )

                a = self.small_font.render(
                    line1,
                    True,
                    TEXT,
                )

                b = self.small_font.render(
                    line2,
                    True,
                    MUTED,
                )

                surface.blit(
                    a,
                    (
                        rect.x + 9,
                        rect.y + 5,
                    ),
                )

                surface.blit(
                    b,
                    (
                        rect.x + 9,
                        rect.y + 23,
                    ),
                )

        can_drive = (
            not self.busy
            and self.selected_candidate_index
            is not None
        )

        self._button(
            surface,
            self._rects["drive"],
            "Drive selected",
            enabled=can_drive,
        )

        self._button(
            surface,
            self._rects["clear"],
            "Clear route",
            enabled=True,
            danger=True,
        )

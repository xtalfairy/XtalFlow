from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AspectFitTransform:
    image_width: int
    image_height: int
    viewport_width: int
    viewport_height: int

    def __post_init__(self) -> None:
        if min(
            self.image_width,
            self.image_height,
            self.viewport_width,
            self.viewport_height,
        ) <= 0:
            raise ValueError("image and viewport dimensions must be positive")

    @property
    def scale(self) -> float:
        return min(
            self.viewport_width / self.image_width,
            self.viewport_height / self.image_height,
        )

    @property
    def rendered_width(self) -> float:
        return self.image_width * self.scale

    @property
    def rendered_height(self) -> float:
        return self.image_height * self.scale

    @property
    def offset_x(self) -> float:
        return (self.viewport_width - self.rendered_width) / 2

    @property
    def offset_y(self) -> float:
        return (self.viewport_height - self.rendered_height) / 2

    def image_to_viewport(self, x_px: float, y_px: float) -> tuple[float, float]:
        return self.offset_x + x_px * self.scale, self.offset_y + y_px * self.scale

    def viewport_to_image(self, x: float, y: float) -> tuple[float, float] | None:
        x_px = (x - self.offset_x) / self.scale
        y_px = (y - self.offset_y) / self.scale
        if not (0 <= x_px < self.image_width and 0 <= y_px < self.image_height):
            return None
        return x_px, y_px

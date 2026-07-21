import pytest

from xtalflow.presentation import AspectFitTransform


def test_aspect_fit_round_trip_with_horizontal_letterboxing() -> None:
    transform = AspectFitTransform(1200, 1000, 1000, 800)

    assert transform.scale == pytest.approx(0.8)
    assert transform.offset_x == pytest.approx(20)
    assert transform.offset_y == pytest.approx(0)
    viewport_point = transform.image_to_viewport(600, 500)
    assert viewport_point == pytest.approx((500, 400))
    assert transform.viewport_to_image(*viewport_point) == pytest.approx((600, 500))


def test_aspect_fit_rejects_clicks_in_letterbox() -> None:
    transform = AspectFitTransform(1200, 1000, 1000, 800)

    assert transform.viewport_to_image(10, 400) is None
    assert transform.viewport_to_image(990, 400) is None


def test_aspect_fit_requires_positive_dimensions() -> None:
    with pytest.raises(ValueError):
        AspectFitTransform(0, 1000, 1000, 800)


def test_zoom_and_pan_preserve_coordinate_round_trip() -> None:
    transform = AspectFitTransform(1200, 1000, 1000, 800, 3.0, 120, -80)

    viewport_point = transform.image_to_viewport(600, 500)
    assert transform.viewport_to_image(*viewport_point) == pytest.approx((600, 500))
    assert transform.scale == pytest.approx(2.4)


def test_zoom_must_not_be_smaller_than_fit_scale() -> None:
    with pytest.raises(ValueError):
        AspectFitTransform(1200, 1000, 1000, 800, 0.5)

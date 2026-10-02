"""Validated window geometry constrained to a monitor's visible work area."""


def valid_window_state(value):
    return (isinstance(value, dict)
            and all(type(value.get(k)) is int for k in ("width", "height", "x", "y"))
            and value["width"] > 0 and value["height"] > 0
            and type(value.get("maximized", False)) is bool)


def restore_window_state(value, work_area):
    left, top, right, bottom = work_area
    available_width = max(1, right - left - 32)
    available_height = max(1, bottom - top - 56)
    valid = valid_window_state(value)
    width = min(available_width, max(min(800, available_width), value["width"])) if valid else min(940, available_width)
    height = min(available_height, max(min(730, available_height), value["height"])) if valid else min(820, available_height)
    x = value["x"] if valid else left + (right - left - width - 16) // 2
    y = value["y"] if valid else top + (bottom - top - height - 40) // 2
    return {"width": width, "height": height,
            "x": max(left, min(x, right - width - 16)),
            "y": max(top, min(y, bottom - height - 40)),
            "maximized": value.get("maximized", False) if valid else False}

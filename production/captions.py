from pathlib import Path

import numpy as np

from PIL import (
    Image,
    ImageDraw,
    ImageFont
)

from moviepy import ImageClip
from moviepy.video.fx import FadeIn, FadeOut


class LocationCaptionError(RuntimeError):

    pass


# Font candidates for the location caption text. The first file that exists
# on the host machine wins. Candidates are grouped by visual weight so the
# caption's letter thickness is configurable via the "font_weight" setting in
# config/app.json ("light" | "semilight" | "normal" | "semibold" | "bold").
# Each group is ordered from the thinnest face available on that platform to
# the thickest.
TEXT_FONT_CANDIDATES = {
    "light": [
        "C:/Windows/Fonts/segoeuil.ttf",               # Segoe UI Light
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Light.ttf",
        "C:/Windows/Fonts/segoeuisl.ttf",              # Segoe UI Semilight
        "C:/Windows/Fonts/arial.ttf",                  # Arial Regular
        "C:/Windows/Fonts/segoeui.ttf",                # Segoe UI Regular
        "/System/Library/Fonts/SFNS.ttf",              # San Francisco (macOS)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
    "semilight": [
        "C:/Windows/Fonts/segoeuisl.ttf",              # Segoe UI Semilight
        "C:/Windows/Fonts/arial.ttf",                  # Arial Regular
        "C:/Windows/Fonts/segoeui.ttf",                # Segoe UI Regular
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
    "normal": [
        "C:/Windows/Fonts/arial.ttf",                  # Arial Regular
        "C:/Windows/Fonts/segoeui.ttf",                # Segoe UI Regular
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ],
    "semibold": [
        "C:/Windows/Fonts/seguisb.ttf",                # Segoe UI Semibold
        "C:/Windows/Fonts/arialbd.ttf",                # Arial Bold
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
    "bold": [
        "C:/Windows/Fonts/arialbd.ttf",                # Arial Bold
        "C:/Windows/Fonts/segoeuib.ttf",               # Segoe UI Bold
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
}

# The location pin in the caption text is the round pushpin unicode
# character (📍, U+1F4CD). The caption normalisation in _normalize_caption
# and the pin-identification check in _layout both key off this constant, so
# it is the single source of truth for the pin codepoint. The pin is never
# rendered with a platform emoji font: every render path draws it in code as
# the "lollipop" artwork (a hot-pink ball on a thin white stem with a white
# highlight dot) so the visual is identical on every machine. Nothing is
# stored in the repository and nothing is downloaded at render time.
LOCATION_PIN = "\U0001F4CD"

# Characters that should be replaced by the code-drawn pin artwork. The
# primary pin is the round pushpin; the map pin is included for tolerance so a
# caption that happened to carry the alternate glyph still renders the same
# artwork instead of falling back to a missing-glyph box.
PIN_CHARACTERS = (
    LOCATION_PIN,
    "\U0001F4CC",
)

# Proportions of the pin slot, measured from the sample captions in
# media/output/shorts (720x1280, text font about 46 px there), all
# normalised by the text font size so they hold at every resolution:
#
# * the pin ink (ball top to stem end) spans 0.74 of the font size
#   above the baseline and 0.02 below it,
# * the slot advance is 0.60 of the font size - the caption's own
#   space character after the pin provides the rest of the gap to the
#   text, which measures about 0.33 font sizes in the samples.
#
# The artwork itself is drawn so that its ball diameter is 0.72 of the
# artwork square, which works out to about 0.55 font sizes - matching
# the measured ball in the samples.
PIN_ASCENT_RATIO = 0.74

PIN_DESCENT_RATIO = 0.02

PIN_ADVANCE_RATIO = 0.60

# Fade-in duration for each text character during the typing phase,
# measured from the sample captions: the first character is fully opaque
# by about the third frame (~0.10 s at 30 fps), so the fade is roughly
# 0.07 s. This applies to every text character as it is revealed, one at
# a time, left to right. The pin and the space glyphs share the same
# fade duration.
CHARACTER_FADE_SECONDS = 0.07

# The pin fades in over roughly the first 0.07 s of the caption while
# the first characters type on (measured: fully opaque by ~0.07 s).
# The characters themselves fade in individually (see CHARACTER_FADE_SECONDS).
PIN_FADE_SECONDS = 0.07

# The white typewriter cursor that blinks on during the typing phase at
# the right edge of the last revealed character, then again (in reverse)
# during the retrace, and is hidden while the completed line holds.
# Measured from the samples: about 0.96 of the font size tall, 0.045
# wide, its bottom sitting 0.11 of the font size below the text baseline,
# with a quick 2-frame blink at each appearance.
CURSOR_HEIGHT_RATIO = 0.96

CURSOR_WIDTH_RATIO = 0.045

CURSOR_DROP_RATIO = 0.11

# How long the cursor stays visible each time it appears (typing + retrace).
# Measured: roughly 3 frames (~0.10 s at 30 fps) before it blinks away.
CURSOR_VISIBLE_SECONDS = 0.10

DEFAULT_DURATION_SECONDS = 5.0

MIN_FONT_SIZE = 26

DEFAULT_TEXT_COLOR = "white"
DEFAULT_STROKE_COLOR = "black"
DEFAULT_STROKE_WIDTH = 3

COLOR_NAMES = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "yellow": (255, 221, 51),
    # The canonical "round pushpin" emoji red (Twitter/Noto design).
    "red": (221, 46, 68),
}


class LocationCaptionBuilder:

    """
    Builds the opening location caption - the only text in the video.

    The caption is always ONE normal horizontal line, centered on the
    screen. It never stacks, never wraps and is never rendered
    vertically. The animation is a classic typewriter:

        1. letters reveal left -> right
        2. the completed line holds briefly
        3. letters retrace right -> left
        4. the caption is gone within `duration_seconds` of the start

    Every character is rendered as its own clip cropped out of a single
    PIL-rendered line image, so the layout (kerning, spacing, emoji pin)
    is exactly the final line and the reveal is genuinely character by
    character instead of a moving mask.
    """

    def __init__(
        self,
        config
    ):

        app_config = (
            config.get(
                "app",
                {}
            )
            if isinstance(config, dict)
            else {}
        )

        self.config = (
            app_config.get(
                "captions",
                {}
            )
        )

        self.project_root = (
            Path(
                __file__
            )
            .resolve()
            .parents[1]
        )

        # Cached code-drawn pin artwork, keyed by pixel size.
        self._pin_art = None

        self._pin_art_size = None

    def enabled(self):

        return bool(
            self.config.get(
                "enabled",
                True
            )
        )

    def duration(self):

        return max(
            0.5,
            float(
                self.config.get(
                    "duration_seconds",
                    DEFAULT_DURATION_SECONDS
                )
            )
        )

    def build(
        self,
        caption,
        width,
        height,
        start=0.0
    ):

        """
        Returns the list of character clips for the opening caption.

        caption : the on-screen line, e.g. "📍 New York City, USA"
        width   : output frame width (centering / auto-fit reference)
        height  : output frame height (vertical placement reference)
        start   : when the intro begins on the timeline (usually 0.0)
        """

        caption = self._normalize_caption(caption)

        if not caption or not self.enabled():

            return []

        font_size = self._font_size()

        text_font_path = self._resolve_text_font()

        stroke_width = self._stroke_width()

        letter_spacing = int(
            self.config.get(
                "letter_spacing",
                0
            )
        )

        max_line_width = max(
            200,
            int(
                width
                * (
                    1.0
                    - 2
                    * float(
                        self.config.get(
                            "horizontal_margin",
                            0.08
                        )
                    )
                )
            )
        )

        # Auto-fit: a long destination shrinks the font until the caption
        # fits on one line inside the safe horizontal margin. The line
        # never wraps and never overflows the frame.
        layout = None

        while font_size > MIN_FONT_SIZE:

            layout = self._layout(
                caption,
                font_size,
                text_font_path,
                stroke_width,
                letter_spacing
            )

            if (
                layout is not None
                and layout["line_width"] <= max_line_width
            ):

                break

            font_size -= 2

        if layout is None:

            layout = self._layout(
                caption,
                font_size,
                text_font_path,
                stroke_width,
                letter_spacing
            )

        if layout is None:

            raise LocationCaptionError(
                "The location caption could not be rendered."
            )

        line_image = layout["image"]

        line_left = int(
            (width - line_image.width) / 2
        )

        line_top = int(
            height
            * float(
                self.config.get(
                    "vertical_position",
                    0.5
                )
            )
            - line_image.height / 2
        )

        return self._build_character_clips(
            layout,
            line_left,
            line_top,
            start
        )

    # ------------------------------------------------------------------
    # Text / font helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_caption(caption):

        text = str(caption or "").strip()

        return " ".join(text.split())

    def _font_size(self):

        try:

            size = int(
                self.config.get(
                    "font_size",
                    66
                )
            )

        except (TypeError, ValueError):

            size = 58

        return max(
            MIN_FONT_SIZE,
            min(size, 200)
        )

    def _stroke_width(self):

        try:

            width = int(
                self.config.get(
                    "stroke_width",
                    DEFAULT_STROKE_WIDTH
                )
            )

        except (TypeError, ValueError):

            width = DEFAULT_STROKE_WIDTH

        return max(0, min(width, 12))

    def _resolve_text_font(self):

        configured = str(
            self.config.get(
                "font",
                ""
            )
            or ""
        ).strip()

        if configured:

            path = Path(configured)

            if not path.is_absolute():

                path = self.project_root / path

            if path.is_file():

                return path

        # No explicit font path: select from the weight-grouped candidate
        # lists. The requested weight is tried first, then the remaining
        # weights in order so the caption is always usable even when the
        # preferred weight is not installed on this machine.
        weight = (
            str(
                self.config.get(
                    "font_weight",
                    "light"
                )
                or ""
            )
            .strip()
            .lower()
        )

        order = ["light", "semilight", "normal", "semibold", "bold"]

        if weight in order:

            order.remove(weight)

            order.insert(0, weight)

        for w in order:

            for candidate in TEXT_FONT_CANDIDATES.get(w, []):

                if Path(candidate).is_file():

                    return Path(candidate)

        return None

    @staticmethod
    def _load_font(path, size):

        if path is None:

            return None

        try:

            return ImageFont.truetype(
                str(path),
                size
            )

        except Exception:

            return None


    def _pin_artwork(self, size):

        """
        Draws the location pin entirely in code - the sample
        captions' "lollipop" pin: a rounded ball with a small white
        highlight dot in its upper right, on a thin white stem - so
        nothing is stored in the repository and nothing is downloaded
        at render time. The caller places it centred in the pin's
        advance slot on the shared text baseline.

        The ball colour is RGB(243, 5, 133), measured from the sample
        captions. The pin is rendered 4x supersampled with numpy
        anti-aliasing and soft shading on the ball rim, then
        area-downscaled for smooth edges. The result is cached per
        pixel size.
        """

        size = max(12, int(size))

        if (
            self._pin_art is not None
            and self._pin_art_size == size
        ):

            return self._pin_art

        artwork = self._downscale_premultiplied(
            self._render_pin_artwork(size * 4),
            size
        )

        self._pin_art = artwork

        self._pin_art_size = size

        return artwork

    @staticmethod
    def _downscale_premultiplied(image, size, resample=Image.BOX):

        """
        Downscales an RGBA image without any halo.

        `size` is either one integer (a square) or a (width, height)
        pair. The colours are multiplied by alpha before the resize
        and divided by alpha afterwards, so transparent pixels cannot
        bleed black into the visible edge.

        `resample` picks the filter. The code-drawn artwork
        (_pin_artwork) has hard analytic edges, where a ringing filter
        such as LANCZOS produces a negative colour overshoot that clips
        asymmetrically and shows up as a bright magenta or cyan fringe.
        It uses the BOX (area average) filter, which has no negative
        lobes. With an integer downscale factor a box average is also
        exact.
        """

        if isinstance(size, int):

            target = (max(1, size), max(1, size))

        else:

            target = (
                max(1, int(size[0])),
                max(1, int(size[1])),
            )

        data = np.asarray(image).astype(np.float64)

        alpha = data[..., 3:4] / 255.0

        premultiplied = np.dstack(
            [data[..., :3] * alpha, data[..., 3:4]]
        )

        scaled = np.asarray(
            Image.fromarray(
                np.uint8(np.clip(premultiplied, 0.0, 255.0))
            ).resize(
                target,
                resample
            )
        ).astype(np.float64)

        scaled_alpha = scaled[..., 3:4] / 255.0

        # Because the average of (colour * alpha) can never exceed
        # 255 * the average of alpha, this division stays in range and
        # cannot shift the hue.
        rgb = (
            scaled[..., :3]
            / np.maximum(scaled_alpha, 1e-6)
        )

        return Image.fromarray(
            np.uint8(
                np.clip(
                    np.dstack([rgb, scaled[..., 3:4]]),
                    0.0,
                    255.0
                )
            )
        )

    @staticmethod
    def _render_pin_artwork(big):

        """
        Renders one RGBA pin of `big` x `big` pixels with numpy
        gradient shading:

            thin white stem first : a plain vertical stick
            ball on top           : flat-colour sphere with a soft
                                    shaded rim and one white
                                    highlight dot, like the pin in
                                    the sample captions. The ball
                                    colour is RGB(243, 5, 133).

        The ink fills the square frame, so the caller can size and
        place it exactly like an emoji glyph.
        """

        big = max(48, int(big))

        # Antialiasing half-width in pixels at this render resolution:
        # _pin_artwork renders at 4x the final size, so half a final
        # pixel is two pixels here and edges stay smooth after the
        # downscale.
        aa = 2.0

        ys, xs = np.mgrid[0:big, 0:big]

        px = xs + 0.5

        py = ys + 0.5

        head_cx = big * 0.500

        head_cy = big * 0.345

        head_r = big * 0.320

        # ----- thin white stem (drawn first, behind the ball) -----

        stem_top = head_cy

        stem_end = big * 0.985

        stem_half = big * 0.030

        dx_stem = px - head_cx

        dy_stem = py - stem_end

        stem_cover = (
            stem_half
            - np.abs(dx_stem)
        )

        stem_point = (
            stem_half
            - np.sqrt(
                dx_stem ** 2
                + dy_stem ** 2
            )
        )

        stem_inside = (
            stem_half
            - np.abs(dx_stem)
        )

        stem_cover = np.where(
            py <= stem_end,
            stem_cover,
            stem_point
        )

        stem_alpha = np.where(
            (py >= stem_top) & (py <= stem_end),
            np.clip(stem_cover + aa, 0.0, 1.0),
            np.clip(stem_inside + aa, 0.0, 1.0)
            * np.where(py > stem_end, 1.0, 0.0)
        )

        # White stick, barely shaded so it stays readable on bright
        # footage without turning into a grey bar.
        stem_rgb = np.full(
            (big, big, 3),
            255.0,
        )

        stem_rgb *= (
            0.94 + 0.06 * np.clip(dy_stem / max(1.0, stem_end - stem_top), 0.0, 1.0)[..., None]
        )

        # ----- hot-pink ball head (painted over the stem) -----

        offset_x = (px - head_cx) / head_r

        offset_y = (py - head_cy) / head_r

        distance_sq = offset_x ** 2 + offset_y ** 2

        ball_alpha = np.clip(
            (1.0 - np.sqrt(distance_sq)) * head_r + aa,
            0.0,
            1.0
        )

        # The sample pin is a flat hot-pink disc with a slightly
        # darkened rim - not a glossy 3D sphere. The colour was
        # measured from the sample caption: RGB(243, 5, 133).
        ball_rgb = np.empty(
            (big, big, 3),
            dtype=np.float64,
        )

        ball_rgb[..., 0] = 243.0

        ball_rgb[..., 1] = 5.0

        ball_rgb[..., 2] = 133.0

        rim = np.clip(
            1.0 - np.sqrt(
                np.clip(1.0 - distance_sq, 0.0, 1.0)
            ),
            0.0,
            1.0
        ) ** 1.4

        ball_rgb *= (
            1.0 - 0.30 * rim
        )[..., None]

        # ----- white highlight dot in the upper right -----

        dot_cx = head_cx + head_r * 0.38

        dot_cy = head_cy - head_r * 0.42

        dot_r = head_r * 0.30

        dot_distance = np.sqrt(
            (px - dot_cx) ** 2
            + (py - dot_cy) ** 2
        )

        dot_alpha = np.clip(
            (dot_r - dot_distance) + aa,
            0.0,
            1.0
        )

        # ----- composite: the dot is painted OVER the ball -----

        ball_with_dot_alpha = np.clip(
            dot_alpha
            + ball_alpha * (1.0 - dot_alpha),
            0.0,
            1.0
        )

        ball_with_dot_rgb = (
            255.0 * dot_alpha[..., None]
            + ball_rgb
            * ball_alpha[..., None]
            * (1.0 - dot_alpha)[..., None]
        ) / np.maximum(
            ball_with_dot_alpha,
            1e-6
        )[..., None]

        alpha = (
            stem_alpha * (1.0 - ball_with_dot_alpha)
            + ball_with_dot_alpha
        )

        rgb = (
            stem_rgb
            * (stem_alpha * (1.0 - ball_with_dot_alpha))[..., None]
            + ball_with_dot_rgb
            * ball_with_dot_alpha[..., None]
        ) / np.maximum(alpha, 1e-6)[..., None]

        rgba = np.dstack(
            [rgb, alpha * 255.0]
        )

        return Image.fromarray(
            np.uint8(np.clip(rgba, 0.0, 255.0))
        )

    @staticmethod
    def _color(value, default):

        name = str(value or "").strip().lower()

        if name in COLOR_NAMES:

            return COLOR_NAMES[name] + (255,)

        return COLOR_NAMES[default] + (255,)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _layout(
        self,
        caption,
        font_size,
        text_font_path,
        stroke_width,
        letter_spacing
    ):

        """
        Renders the whole caption as ONE horizontal PIL line image and
        returns it together with the horizontal span of every
        character, so each character can later be cropped back out of
        the exact same image (pixel-perfect reveal, single line).
        """

        text_font = self._load_font(
            text_font_path,
            font_size
        )

        if text_font is None:

            text_font = ImageFont.load_default()

        measure_image = Image.new(
            "RGBA",
            (1, 1),
            (0, 0, 0, 0)
        )

        measure_draw = ImageDraw.Draw(
            measure_image
        )

        entries = []

        x = 0.0

        for character in caption:

            is_pin = character in PIN_CHARACTERS

            if is_pin:

                # The pin is always the code-drawn "lollipop" - a
                # hot-pink ball with a white highlight dot on a thin
                # white stem (see _render_pin_artwork) - matching the
                # sample captions, whose pin is a custom graphic and
                # NOT the platform emoji glyph. The slot keeps the
                # generous emoji-style advance, so the layout does not
                # change between machines.
                advance = (
                    float(font_size)
                    * PIN_ADVANCE_RATIO
                )

                pin_font = text_font

                kind = "pin_artwork"

                entries.append(
                    {
                        "character": character,
                        "kind": kind,
                        "font": pin_font,
                        "x": x,
                        "advance": advance,
                    }
                )

            else:

                advance = float(
                    measure_draw.textlength(
                        character,
                        font=text_font
                    )
                )

                entries.append(
                    {
                        "character": character,
                        "kind": "text",
                        "font": text_font,
                        "x": x,
                        "advance": advance,
                    }
                )

            x += entries[-1]["advance"] + letter_spacing

        if not entries:

            return None

        # Metrics: the tallest ascender/descender of the fonts in use,
        # so the emoji pin and the text share one baseline.
        ascents = []

        descents = []

        for entry in entries:

            try:

                ascent, descent = entry["font"].getmetrics()

            except Exception:

                ascent, descent = font_size, int(font_size * 0.25)

            ascents.append(ascent)

            descents.append(descent)

        max_ascent = max(ascents)

        max_descent = max(descents)

        padding = stroke_width + 8

        line_width = int(
            round(
                sum(
                    entry["advance"]
                    for entry in entries
                )
                + letter_spacing
                * max(0, len(entries) - 1)
            )
        ) + 2 * padding

        line_height = (
            max_ascent
            + max_descent
            + 2 * padding
        )

        if line_width <= 0 or line_height <= 0:

            return None

        image = Image.new(
            "RGBA",
            (line_width, line_height),
            (0, 0, 0, 0)
        )

        draw = ImageDraw.Draw(
            image
        )

        baseline = padding + max_ascent

        text_color = self._color(
            self.config.get(
                "text_color",
                DEFAULT_TEXT_COLOR
            ),
            DEFAULT_TEXT_COLOR
        )

        stroke_color = self._color(
            self.config.get(
                "stroke_color",
                DEFAULT_STROKE_COLOR
            ),
            DEFAULT_STROKE_COLOR
        )

        for entry in entries:

            origin_x = padding + entry["x"]

            if entry["kind"] == "pin_artwork":

                # The code-drawn pin, composited centred in its
                # advance slot so it sits on the shared baseline
                # exactly where the font would have drawn it
                # (PIN_ASCENT_RATIO of the font size above the
                # baseline, PIN_DESCENT_RATIO below it). No stroke:
                # the artwork brings its own colours.

                pin_size = max(
                    12,
                    int(
                        round(
                            font_size
                            * (
                                PIN_ASCENT_RATIO
                                + PIN_DESCENT_RATIO
                            )
                        )
                    )
                )

                pin_image = self._pin_artwork(pin_size)

                if pin_image is not None:

                    paste_x = int(
                        round(
                            origin_x
                            + (
                                entry["advance"]
                                - pin_image.width
                            )
                            / 2.0
                        )
                    )

                    paste_y = int(
                        round(
                            baseline
                            - font_size * PIN_ASCENT_RATIO
                        )
                    )

                    image.paste(
                        pin_image,
                        (paste_x, paste_y),
                        pin_image,
                    )

                continue

            try:

                draw.text(
                    (origin_x, baseline),
                    entry["character"],
                    font=entry["font"],
                    fill=text_color,
                    anchor="ls",
                    stroke_width=stroke_width,
                    stroke_fill=stroke_color
                )

            except Exception:

                return None

        return {
            "image": image,
            "entries": entries,
            "padding": padding,
            "stroke_width": stroke_width,
            "line_width": line_width,
            "line_height": line_height,
        }

    # ------
    # Timeline
    # ------------------------------------------------------------------

    def _build_character_clips(
        self,
        layout,
        line_left,
        line_top,
        start
    ):

        """
        Crops each character out of the rendered line and gives it the
        typewriter timing:

            reveal  i : start + i * step_in
            retrace i : gone at start + typing + hold + (n - i) * step_out

        so the line reveals left -> right, holds completed, then
        disappears right -> left and is fully gone at start + duration.
        """

        image = layout["image"]

        entries = layout["entries"]

        padding = layout["padding"]

        line_height = layout["line_height"]

        duration = self.duration()

        total = len(entries)

        typing_fraction = self._fraction(
            "typing_fraction",
            0.22
        )

        hold_fraction = self._fraction(
            "hold_fraction",
            0.4
        )

        if typing_fraction + hold_fraction >= 1.0:

            hold_fraction = max(
                0.0,
                1.0 - typing_fraction - 0.2
            )

        typing_seconds = duration * typing_fraction

        hold_seconds = duration * hold_fraction

        delete_seconds = max(
            0.0,
            duration - typing_seconds - hold_seconds
        )

        step_in = (
            typing_seconds / total
            if total
            else 0.0
        )

        step_out = (
            delete_seconds / total
            if total
            else 0.0
        )

        # Build a white cursor bar once, then show/hide it by timing.
        font_size = self._font_size()
        cursor_w = max(1, int(font_size * CURSOR_WIDTH_RATIO))
        cursor_h = int(font_size * CURSOR_HEIGHT_RATIO)
        cursor_drop = font_size * CURSOR_DROP_RATIO
        cursor_y = line_height - cursor_h - int(cursor_drop)
        _cursor_img = Image.new("RGBA", (cursor_w, cursor_h), (0, 0, 0, 0))
        _cursor_draw = ImageDraw.Draw(_cursor_img)
        _cursor_draw.rectangle(
            (0, 0, cursor_w - 1, cursor_h - 1), fill=(255, 255, 255, 255)
        )
        cursor_piece = ImageClip(np.array(_cursor_img))

        clips = []

        for index, entry in enumerate(entries):

            # Every character is cropped out of the SAME rendered line,
            # and the crops tile the line exactly:
            #
            #   first : [0, x_1)                (keeps the left padding)
            #   middle: [x_i, x_{i+1})          (shared boundary)
            #   last  : [x_n, image.width)      (keeps the right padding)
            #
            # Tiling means the assembled clips reproduce the original
            # line pixel for pixel, and no pixel of a character that has
            # not been revealed yet can ever appear early.
            left = (
                0
                if index == 0
                else int(padding + entry["x"])
            )

            if index + 1 < total:
                right = int(
                    padding + entries[index + 1]["x"]
                )
            else:
                right = image.width

            if right <= left:
                continue

            piece = image.crop(
                (
                    left,
                    0,
                    right,
                    line_height
                )
            )

            clip_start = start + index * step_in

            clip_end = (
                start
                + typing_seconds
                + hold_seconds
                + (total - index) * step_out
            )

            is_pin = entry["kind"] == "pin_artwork"

            # Text characters fade in one at a time during typing, mirroring
            # the sample captions where each character ramps up over roughly
            # 2-3 frames. The pin fades in as one piece over PIN_FADE_SECONDS.
            clip = (
                ImageClip(np.array(piece))
                .with_start(clip_start)
                .with_duration(clip_end - clip_start)
                .with_position(
                    (
                        line_left + left,
                        line_top,
                    )
                )
            )

            if is_pin and step_in > 0:
                clip = clip.with_effects(
                    [FadeIn(min(step_in, PIN_FADE_SECONDS))]
                )
            elif not is_pin and step_in > 0:
                clip = clip.with_effects(
                    [FadeIn(min(step_in, CHARACTER_FADE_SECONDS))]
                )

            clips.append(clip)

            # Cursor: a thin white bar that sits at the right edge of the
            # last-typed character and blinks on briefly with each new
            # character during typing (and again during retrace). It is
            # hidden while the completed line holds.
            if entry["kind"] == "pin_artwork":
                continue

            cursor_x = (
                line_left + right - cursor_w
                if right > cursor_w
                else line_left
            )

            if index < total - 1:
                # Typing phase: cursor appears at the end of this
                # character and stays briefly before the next one.
                c_start = clip_start
                c_dur = min(step_in, CURSOR_VISIBLE_SECONDS)
            else:
                # Last text character: cursor blinks briefly when it
                # first appears (like every other character), then
                # disappears. It does NOT persist through the hold.
                c_start = clip_start
                c_dur = min(step_in, CURSOR_VISIBLE_SECONDS)

            # Retrace cursor: during the delete phase each character
            # disappears right-to-left and the cursor blinks at the
            # right edge of the character currently being erased,
            # mirroring the sample captions. This is hidden once the
            # last character is gone.
            if step_out > 0:
                r_start = clip_end - min(step_out, CURSOR_VISIBLE_SECONDS)
                r_dur = min(step_out, CURSOR_VISIBLE_SECONDS)
                if r_dur > 0:
                    cursor_clip = (
                        cursor_piece
                        .with_start(r_start)
                        .with_duration(r_dur)
                        .with_position(
                            (
                                cursor_x,
                                line_top + cursor_y,
                            )
                        )
                    )
                    clips.append(cursor_clip)

            if c_dur > 0:
                cursor_clip = (
                    cursor_piece
                    .with_start(c_start)
                    .with_duration(c_dur)
                    .with_position(
                        (
                            cursor_x,
                            line_top + cursor_y,
                        )
                    )
                )
                clips.append(cursor_clip)

        return clips

    def _fraction(self, name, default):

        try:

            value = float(
                self.config.get(
                    name,
                    default
                )
            )

        except (TypeError, ValueError):

            value = default

        if value <= 0:

            return default

        return min(value, 0.95)
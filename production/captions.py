from pathlib import Path

import numpy as np

from PIL import (
    Image,
    ImageDraw,
    ImageFont
)

from moviepy import ImageClip


class LocationCaptionError(RuntimeError):

    pass


# Bulky, clean sans-serif faces that stay readable over bright footage.
TEXT_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/seguisb.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

# The location pin is the real emoji glyph: it is drawn with the
# platform color emoji font (embedded_color=True) directly on the
# text baseline, so it renders in full color and in the machine's own
# emoji design. Nothing is stored in the repository and nothing is
# downloaded at render time. On a machine without a color emoji font
# the pin falls back to artwork drawn in code (see _pin_artwork),
# placed with the same proportions so the caption still reads right.
LOCATION_PIN = "\U0001F4CD"

PIN_CHARACTERS = (
    LOCATION_PIN,
    "\U0001F4CC",
)

# Platform color emoji fonts, tried in order. These are loaded with
# embedded_color=True, which is what makes Pillow paint the real
# full-color emoji instead of a washed-out monochrome outline.
EMOJI_FONT_CANDIDATES = [
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/Library/Fonts/Apple Color Emoji.ttc",
]

# Proportions of the emoji glyph, measured from the platform font: the
# pin ink spans 0.897 of the font size above the baseline and 0.086
# below it, and the glyph's natural advance is 1.38 of the font size.
# Emoji glyphs sit centred in a wide em slot, which is what gives the
# caption its generous gap between the pin and the text. The artwork
# fallback reproduces these numbers so both paths agree.
PIN_ASCENT_RATIO = 0.897
PIN_DESCENT_RATIO = 0.086
PIN_ADVANCE_RATIO = 1.38

# Emoji fonts are bitmap fonts, so drawing the pin straight at its
# final size (about 58 px) picks a small bitmap strike and the ball
# edge comes out noticeably jagged. The glyph is therefore rendered at
# this multiple of the final size and then area-downscaled back down,
# which recovers the smooth edge at no cost to the layout. 4x was
# measured to be the point of diminishing returns: edge detail is
# 12x higher than a direct render, and 6x is no sharper.
PIN_SUPERSAMPLE = 4

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

        # Cached platform color emoji font per pixel size. None is
        # cached too, so a machine without one only probes once.
        self._emoji_fonts = {}

        # Supersampled pin glyphs, cached per (character, font size).
        self._pin_glyphs = {}

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
                    58
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

        for candidate in TEXT_FONT_CANDIDATES:

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

    def _load_color_emoji_font(self, size):

        """
        Returns the platform color emoji font at the requested pixel
        size, or None when the machine has no color emoji font.
        """

        size = max(1, int(size))

        if size in self._emoji_fonts:

            return self._emoji_fonts[size]

        font = None

        for candidate in EMOJI_FONT_CANDIDATES:

            try:

                font = ImageFont.truetype(
                    candidate,
                    size
                )

                break

            except Exception:

                continue

        self._emoji_fonts[size] = font

        return font

    def _pin_glyph(self, character, font_size):

        """
        Renders one emoji pin glyph supersampled, and caches it per
        (character, font size).

        Returns (image, offset_x, offset_y), where the offsets place
        the image's top-left corner in final-size pixels relative to
        the glyph's pen position (origin_x, baseline) - exactly how
        the font itself would have drawn it. Returns None when the
        machine has no color emoji font.

        The supersampling is the whole point: drawn directly at its
        final size the emoji font uses a small bitmap strike and the
        ball edge comes out visibly jagged.
        """

        key = (character, font_size)

        if key in self._pin_glyphs:

            return self._pin_glyphs[key]

        result = None

        big = font_size * PIN_SUPERSAMPLE

        font = self._load_color_emoji_font(big)

        if font is not None:

            origin_x = big

            baseline = big

            canvas = Image.new(
                "RGBA",
                (big * 3, big * 3),
                (0, 0, 0, 0)
            )

            draw = ImageDraw.Draw(canvas)

            try:

                draw.text(
                    (origin_x, baseline),
                    character,
                    font=font,
                    embedded_color=True,
                    anchor="ls",
                )

            except Exception:

                result = None

            else:

                ink = canvas.getbbox()

                if ink:

                    # Snap the crop out to whole supersample cells so
                    # the downscale is an exact integer area average
                    # and the placement offsets stay whole pixels.
                    step = PIN_SUPERSAMPLE

                    left = (ink[0] // step) * step

                    top = (ink[1] // step) * step

                    right = -(-ink[2] // step) * step

                    bottom = -(-ink[3] // step) * step

                    piece = canvas.crop(
                        (left, top, right, bottom)
                    )

                    scaled = self._downscale_premultiplied(
                        piece,
                        (
                            piece.width // step,
                            piece.height // step,
                        ),
                        Image.LANCZOS
                    )

                    result = (
                        scaled,
                        (left - origin_x) // step,
                        (top - baseline) // step,
                    )

        self._pin_glyphs[key] = result

        return result

    def _pin_artwork(self, size):

        """
        Draws the fallback location pin, used only on machines that
        have no color emoji font.

        It is the classic round pushpin drawn entirely in code - a
        glossy red ball head on a tapered silver needle - so nothing
        is stored in the repository and nothing is downloaded at
        render time. The caller places it with the same proportions
        as the emoji glyph, so the caption keeps its layout either
        way.

        The pin is rendered 4x supersampled with numpy gradient
        shading - lambert diffuse plus a specular highlight on the
        ball head, cylindrical silver shading down the needle - and
        then area-downscaled for smooth edges. The result is cached
        per pixel size.
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

        `resample` picks the filter, and the two callers want
        different ones:

        * The code-drawn artwork (_pin_artwork) has hard analytic
          edges, where a ringing filter such as LANCZOS produces a
          negative colour overshoot that clips asymmetrically and
          shows up as a bright magenta or cyan fringe. It uses the BOX
          (area average) filter, which has no negative lobes. With an
          integer downscale factor a box average is also exact.
        * The emoji glyph (_pin_glyph) is already a soft photographic
          bitmap, so LANCZOS sharpens it substantially - 12x the edge
          detail of drawing it directly at final size - and stays
          halo-free because the source has no hard edge to overshoot.
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
        Renders one RGBA pushpin of `big` x `big` pixels with numpy
        gradient shading:

            needle first     : tapered silver shaft, lit down one side
            ball head on top : red sphere with sheen and a sparkle

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

        # ----- silver needle (drawn first, behind the ball) -----

        needle_top = head_cy

        shaft_end = big * 0.968

        top_half = big * 0.042

        bottom_half = big * 0.008

        span = max(1.0, shaft_end - needle_top)

        progress = np.clip(
            (py - needle_top) / span,
            0.0,
            1.0
        )

        half_width = (
            top_half
            + (bottom_half - top_half) * progress
        )

        dx_needle = px - head_cx

        shaft_distance = (
            half_width - np.abs(dx_needle)
        )

        point_distance = (
            bottom_half
            - np.sqrt(
                dx_needle ** 2
                + (py - shaft_end) ** 2
            )
        )

        cover = np.where(
            py <= shaft_end,
            shaft_distance,
            point_distance
        )

        needle_alpha = np.where(
            py >= needle_top,
            np.clip(cover + aa, 0.0, 1.0),
            0.0
        )

        # Cylindrical shading: darker at both edges of the shaft with
        # a bright core highlight a third of the way across.

        across = np.clip(
            (dx_needle + half_width)
            / np.maximum(2.0 * half_width, 1e-6),
            0.0,
            1.0
        )

        core = np.exp(
            -((across - 0.33) ** 2) / (2.0 * 0.17 ** 2)
        )

        near_edge = np.exp(
            -(across ** 2) / (2.0 * 0.12 ** 2)
        )

        light_level = np.clip(
            0.66 + 0.40 * core - 0.22 * near_edge,
            0.0,
            1.0
        )

        dark = np.array([88.0, 93.0, 102.0])

        mid = np.array([176.0, 182.0, 190.0])

        bright = np.array([250.0, 252.0, 255.0])

        low = np.clip(
            light_level / 0.5,
            0.0,
            1.0
        )[..., None]

        high = np.clip(
            (light_level - 0.5) / 0.5,
            0.0,
            1.0
        )[..., None]

        needle_rgb = dark * (1.0 - low) + mid * low

        needle_rgb = needle_rgb * (1.0 - high) + bright * high

        # Darken slightly toward the point, and let the ball cast a
        # soft contact shadow onto the needle below it.

        needle_rgb *= (
            1.0 - 0.22 * progress
        )[..., None]

        contact = np.clip(
            (
                py
                - (head_cy + head_r * 0.62)
            )
            / (big * 0.16),
            0.0,
            1.0
        )

        needle_rgb *= (
            1.0 - 0.35 * (1.0 - contact)
        )[..., None]

        # ----- glossy red ball head (painted over the needle) -----

        offset_x = (px - head_cx) / head_r

        offset_y = (py - head_cy) / head_r

        distance_sq = offset_x ** 2 + offset_y ** 2

        ball_alpha = np.clip(
            (1.0 - np.sqrt(distance_sq)) * head_r + aa,
            0.0,
            1.0
        )

        normal_z = np.sqrt(
            np.clip(1.0 - distance_sq, 0.0, 1.0)
        )

        light = np.array([-0.40, -0.52, 0.75])

        light = light / np.linalg.norm(light)

        lambert = np.clip(
            offset_x * light[0]
            + offset_y * light[1]
            + normal_z * light[2],
            0.0,
            1.0
        )

        base = np.array([223.0, 33.0, 55.0])

        ball_rgb = base * (
            0.48 + 0.68 * lambert
        )[..., None]

        # Darken the rim that faces away from the light.

        rim = np.clip(
            1.0 - normal_z,
            0.0,
            1.0
        ) ** 1.6

        ball_rgb *= (1.0 - 0.36 * rim)[..., None]

        # Broad sheen where the light lands, plus a tight sparkle.

        sheen = np.clip(
            (lambert - 0.55) / 0.45,
            0.0,
            1.0
        ) ** 1.5

        ball_rgb = (
            ball_rgb * (1.0 - 0.26 * sheen)[..., None]
            + 255.0 * (0.26 * sheen)[..., None]
        )

        sparkle = 0.95 * lambert ** 20.0

        ball_rgb = (
            ball_rgb * (1.0 - sparkle)[..., None]
            + 255.0 * sparkle[..., None]
        )

        # ----- composite: needle, then ball, clamp to 8-bit -----

        alpha = (
            needle_alpha * (1.0 - ball_alpha)
            + ball_alpha
        )

        rgb = (
            needle_rgb
            * (needle_alpha * (1.0 - ball_alpha))[..., None]
            + ball_rgb * ball_alpha[..., None]
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

                # The pin is the real emoji glyph, drawn with the
                # platform color emoji font on the shared text
                # baseline, so it uses that font's own advance (a
                # wide em slot). Without a color emoji font the pin
                # is composited as artwork in an equally wide slot.
                emoji_font = self._load_color_emoji_font(
                    font_size
                )

                if emoji_font is not None:

                    advance = float(
                        measure_draw.textlength(
                            character,
                            font=emoji_font
                        )
                    )

                    pin_font = emoji_font

                    kind = "pin"

                else:

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

            if entry["kind"] in ("pin", "pin_artwork"):

                drawn = False

                if entry["kind"] == "pin":

                    # The real emoji, in full color and supersampled
                    # for a smooth edge, pasted so it sits on the
                    # shared baseline exactly where the font would
                    # have drawn it. No stroke: the emoji brings its
                    # own artwork.

                    glyph = self._pin_glyph(
                        entry["character"],
                        font_size
                    )

                    if glyph is not None:

                        pin_image, offset_x, offset_y = glyph

                        image.paste(
                            pin_image,
                            (
                                int(round(origin_x)) + offset_x,
                                int(round(baseline)) + offset_y,
                            ),
                            pin_image,
                        )

                        drawn = True

                if not drawn:

                    # No color emoji font, or the glyph failed:
                    # composite the pin drawn in code, placed with
                    # the same proportions as the emoji glyph
                    # (0.897 of the font size above the baseline,
                    # 0.086 below it).

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
                                    - pin_size
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
            0.3
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

            clip = (
                ImageClip(
                    np.array(piece)
                )
                .with_start(
                    clip_start
                )
                .with_duration(
                    clip_end - clip_start
                )
                .with_position(
                    (
                        line_left + left,
                        line_top
                    )
                )
            )

            clips.append(
                clip
            )

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
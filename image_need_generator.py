from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


IMAGE_NEED_PATTERN = re.compile(r"\[이미지\s*필요\s*:\s*(.*?)\]", re.DOTALL)
CANVAS_SIZE = (900, 650)
BACKGROUND_DIR_NAME = "배경삽화"
BLUE = (30, 90, 180)
LIGHT_BLUE = (235, 244, 255)
RED = (210, 70, 50)
TEXT = (35, 35, 35)
SUPPORTED_BACKGROUND_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass(frozen=True)
class ImageRequest:
    index: int
    prompt: str
    output_path: Path


@dataclass(frozen=True)
class ExtractResult:
    requests: list[ImageRequest]
    numbered_text: str
    numbered_text_path: Path


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def extract_image_requests(txt_path: Path) -> ExtractResult:
    text = txt_path.read_text(encoding="utf-8-sig")
    base_name = txt_path.stem
    requests: list[ImageRequest] = []
    numbered_parts: list[str] = []
    last_end = 0

    for index, match in enumerate(IMAGE_NEED_PATTERN.finditer(text), start=1):
        prompt = " ".join(match.group(1).split())
        output_path = txt_path.with_name(f"{base_name}_이미지{index}.PNG")
        requests.append(ImageRequest(index=index, prompt=prompt, output_path=output_path))
        numbered_parts.append(text[last_end : match.start()])
        numbered_parts.append(f"[이미지 필요{index} : {prompt}]")
        last_end = match.end()

    numbered_parts.append(text[last_end:])
    numbered_text_path = txt_path.with_name(f"{base_name}_이미지번호매칭.txt")
    return ExtractResult(requests=requests, numbered_text="".join(numbered_parts), numbered_text_path=numbered_text_path)


def get_background_dir() -> Path:
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent
    return app_dir / BACKGROUND_DIR_NAME


def iter_background_files() -> list[Path]:
    background_dir = get_background_dir()
    if not background_dir.exists():
        return []
    return [
        path
        for path in background_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_BACKGROUND_SUFFIXES
    ]


def find_background_for_prompt(prompt: str) -> Path | None:
    backgrounds = iter_background_files()
    if not backgrounds:
        return None

    explicit = re.search(r"배경(?:삽화)?\s*[=:：]\s*([^\],\n]+)", prompt)
    if explicit:
        wanted = explicit.group(1).strip().strip("\"'")
        for path in backgrounds:
            if wanted == path.name or wanted == path.stem or wanted in path.stem:
                return path

    normalized_prompt = prompt.replace(" ", "").lower()
    for path in backgrounds:
        stem = path.stem.replace(" ", "").lower()
        if stem and stem in normalized_prompt:
            return path

    keyword_map = {
        "탑": ["탑", "tower"],
        "건물": ["건물", "빌딩", "building"],
        "나무": ["나무", "tree"],
        "사람": ["사람", "person"],
        "학교": ["학교", "school"],
        "산": ["산", "mountain"],
    }
    for prompt_keyword, filename_keywords in keyword_map.items():
        if prompt_keyword in normalized_prompt:
            for path in backgrounds:
                stem = path.stem.lower()
                if any(keyword in stem for keyword in filename_keywords):
                    return path

    return None


def cover_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_width, target_height = size
    source_width, source_height = image.size
    scale = max(target_width / source_width, target_height / source_height)
    resized_size = (int(source_width * scale), int(source_height * scale))
    resized = image.resize(resized_size, Image.Resampling.LANCZOS)
    left = (resized.width - target_width) // 2
    top = (resized.height - target_height) // 2
    return resized.crop((left, top, left + target_width, top + target_height))


def load_background_canvas(prompt: str | None, size: tuple[int, int]) -> Image.Image | None:
    if not prompt:
        return None
    background_path = find_background_for_prompt(prompt)
    if background_path is None:
        return None

    background = Image.open(background_path).convert("RGB")
    background = cover_resize(background, size)
    white = Image.new("RGB", size, "white")
    return Image.blend(background, white, 0.28)


def new_canvas(prompt: str | None = None, scale: int = 1) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    size = (CANVAS_SIZE[0] * scale, CANVAS_SIZE[1] * scale)
    image = load_background_canvas(prompt, size) or Image.new("RGB", size, "white")
    return image, ImageDraw.Draw(image)


def draw_label(
    draw: ImageDraw.ImageDraw,
    point: tuple[int, int],
    label: str,
    offset: tuple[int, int],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    x, y = point
    ox, oy = offset
    draw.text((x + ox, y + oy), label, fill=TEXT, font=font)


def draw_right_angle(draw: ImageDraw.ImageDraw, corner: tuple[int, int], size: int, quadrant: str) -> None:
    x, y = corner
    if quadrant == "upper_right":
        points = [(x, y - size), (x + size, y - size), (x + size, y)]
    elif quadrant == "upper_left":
        points = [(x, y - size), (x - size, y - size), (x - size, y)]
    elif quadrant == "lower_right":
        points = [(x, y + size), (x + size, y + size), (x + size, y)]
    else:
        points = [(x, y + size), (x - size, y + size), (x - size, y)]
    draw.line(points, fill=(40, 40, 40), width=3)


def add_caption(draw: ImageDraw.ImageDraw, prompt: str, image_size: tuple[int, int], scale: int = 1) -> None:
    font = load_font(22 * scale)
    _, height = image_size
    caption = f"요청: {prompt}".replace("_", " ").replace("−", "-").replace("–", "-")
    max_chars = 48
    lines = [caption[i : i + max_chars] for i in range(0, len(caption), max_chars)]
    y = height - 60 * scale - (len(lines) - 1) * 26 * scale
    for line in lines:
        draw.text((35 * scale, y), line, fill=(90, 90, 90), font=font)
        y += 26 * scale


def draw_right_triangle_b(prompt: str, output_path: Path) -> None:
    image, draw = new_canvas(prompt)
    label_font = load_font(34)
    small_font = load_font(24)

    b = (260, 470)
    a = (260, 150)
    c = (690, 470)

    draw.polygon([a, b, c], outline=BLUE, fill=LIGHT_BLUE)
    draw.line([a, b, c, a], fill=BLUE, width=5)
    draw_right_angle(draw, b, 55, "upper_right")

    draw_label(draw, a, "A", (-45, -35), label_font)
    draw_label(draw, b, "B", (-45, 5), label_font)
    draw_label(draw, c, "C", (18, 0), label_font)
    draw.text((310, 285), "AB", fill=(80, 80, 80), font=small_font)
    draw.text((460, 490), "BC", fill=(80, 80, 80), font=small_font)
    draw.text((485, 295), "AC", fill=(80, 80, 80), font=small_font)
    draw.text((280, 415), "90°", fill=TEXT, font=small_font)

    add_caption(draw, prompt, image.size)
    image.save(output_path)


def draw_generic_triangle(prompt: str, output_path: Path) -> None:
    image, draw = new_canvas(prompt)
    label_font = load_font(34)

    a = (450, 120)
    b = (220, 500)
    c = (700, 500)

    draw.polygon([a, b, c], outline=BLUE, fill=LIGHT_BLUE)
    draw.line([a, b, c, a], fill=BLUE, width=5)
    draw_label(draw, a, "A", (-10, -45), label_font)
    draw_label(draw, b, "B", (-45, 0), label_font)
    draw_label(draw, c, "C", (18, 0), label_font)

    add_caption(draw, prompt, image.size)
    image.save(output_path)


def draw_tower_height_triangle(prompt: str, output_path: Path) -> None:
    image, draw = new_canvas(prompt)
    label_font = load_font(34)
    small_font = load_font(24)

    top = (450, 145)
    base = (450, 470)
    observer = (720, 470)

    draw.line([top, base, observer, top], fill=BLUE, width=5)
    draw.polygon([top, base, observer], outline=BLUE, fill=None)
    draw_right_angle(draw, base, 45, "upper_right")

    draw_label(draw, top, "A", (-10, -45), label_font)
    draw_label(draw, base, "B", (-45, 0), label_font)
    draw_label(draw, observer, "C", (18, 0), label_font)
    draw.text((405, 300), "탑의 높이", fill=TEXT, font=small_font)
    draw.text((570, 490), "거리", fill=TEXT, font=small_font)
    draw.text((470, 415), "90°", fill=TEXT, font=small_font)

    add_caption(draw, prompt, image.size)
    image.save(output_path)


def draw_circle(prompt: str, output_path: Path) -> None:
    image, draw = new_canvas(prompt)
    label_font = load_font(34)
    small_font = load_font(24)

    center = (450, 315)
    radius = 190
    box = (
        center[0] - radius,
        center[1] - radius,
        center[0] + radius,
        center[1] + radius,
    )

    draw.ellipse(box, outline=BLUE, fill=LIGHT_BLUE, width=5)
    draw.ellipse((center[0] - 5, center[1] - 5, center[0] + 5, center[1] + 5), fill=TEXT)
    draw_label(draw, center, "O", (12, 8), label_font)

    if "지름" in prompt and "반지름" not in prompt:
        left = (center[0] - radius, center[1])
        right = (center[0] + radius, center[1])
        draw.line([left, right], fill=RED, width=4)
        draw_label(draw, left, "A", (-40, -14), label_font)
        draw_label(draw, right, "B", (18, -14), label_font)
        draw.text((405, 280), "지름", fill=RED, font=small_font)
    else:
        end = (center[0] + int(radius * 0.72), center[1] - int(radius * 0.70))
        draw.line([center, end], fill=RED, width=4)
        draw_label(draw, end, "A", (12, -35), label_font)
        draw.text((505, 205), "반지름", fill=RED, font=small_font)

    add_caption(draw, prompt, image.size)
    image.save(output_path)


def draw_quadrilateral(prompt: str, output_path: Path) -> None:
    image, draw = new_canvas(prompt)
    label_font = load_font(34)
    normalized = prompt.replace(" ", "")

    if "정사각형" in normalized:
        points = [(285, 155), (615, 155), (615, 485), (285, 485)]
    elif "직사각형" in normalized:
        points = [(210, 180), (690, 180), (690, 460), (210, 460)]
    elif "마름모" in normalized:
        points = [(450, 125), (690, 320), (450, 515), (210, 320)]
    elif "평행사변형" in normalized:
        points = [(285, 180), (705, 180), (615, 460), (195, 460)]
    else:
        points = [(255, 170), (660, 145), (710, 455), (230, 500)]

    draw.polygon(points, outline=BLUE, fill=LIGHT_BLUE)
    draw.line(points + [points[0]], fill=BLUE, width=5)
    for label, point, offset in zip(
        ["A", "B", "C", "D"],
        points,
        [(-35, -40), (18, -40), (18, 0), (-45, 0)],
    ):
        draw_label(draw, point, label, offset, label_font)

    if "직사각형" in normalized or "정사각형" in normalized:
        draw_right_angle(draw, points[3], 45, "upper_right")

    add_caption(draw, prompt, image.size)
    image.save(output_path)


def draw_regular_polygon(prompt: str, output_path: Path) -> None:
    image, draw = new_canvas(prompt)
    label_font = load_font(30)

    side_count = 5
    if "육각형" in prompt or "정육각형" in prompt:
        side_count = 6
    elif "오각형" in prompt or "정오각형" in prompt:
        side_count = 5

    center = (450, 315)
    radius = 210
    points = []
    for i in range(side_count):
        angle = -math.pi / 2 + 2 * math.pi * i / side_count
        points.append((int(center[0] + radius * math.cos(angle)), int(center[1] + radius * math.sin(angle))))

    draw.polygon(points, outline=BLUE, fill=LIGHT_BLUE)
    draw.line(points + [points[0]], fill=BLUE, width=5)
    for i, point in enumerate(points):
        label = chr(ord("A") + i)
        dx = -10 if point[0] < center[0] else 12
        dy = -40 if point[1] < center[1] else 8
        draw_label(draw, point, label, (dx, dy), label_font)

    add_caption(draw, prompt, image.size)
    image.save(output_path)


def draw_coordinate_axes(draw: ImageDraw.ImageDraw, width: int, height: int) -> tuple[int, int, int]:
    margin = 90
    origin = (margin, height - margin)
    axis_color = (45, 45, 45)
    grid_color = (225, 225, 225)
    graph_width = width - 2 * margin
    graph_height = height - 2 * margin

    for i in range(0, 11):
        x = margin + i * graph_width // 10
        y = margin + i * graph_height // 10
        draw.line([(x, margin), (x, height - margin)], fill=grid_color, width=1)
        draw.line([(margin, y), (width - margin, y)], fill=grid_color, width=1)

    draw.line([(margin, height - margin), (width - margin, height - margin)], fill=axis_color, width=3)
    draw.line([(margin, height - margin), (margin, margin)], fill=axis_color, width=3)
    draw.polygon(
        [(width - margin, height - margin), (width - margin - 14, height - margin - 8), (width - margin - 14, height - margin + 8)],
        fill=axis_color,
    )
    draw.polygon([(margin, margin), (margin - 8, margin + 14), (margin + 8, margin + 14)], fill=axis_color)

    return origin[0], origin[1], min(graph_width, graph_height)


def replace_superscripts(text: str) -> str:
    superscripts = {
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
    }
    result = []
    for char in text:
        if char in superscripts:
            result.append("**" + superscripts[char])
        else:
            result.append(char)
    return "".join(result)


def normalize_function_expression(prompt: str) -> tuple[str, str] | None:
    math_prompt = prompt.replace("_", " ")
    formula_match = re.search(r"(?:y|f\s*\(\s*x\s*\))\s*=\s*([^,\]\n]+)", math_prompt, re.IGNORECASE)
    if formula_match:
        label = formula_match.group(0).strip()
        expression = formula_match.group(1).strip()
    else:
        expression_match = re.search(r"([\-−]?\s*\d*\.?\d*\s*\*?\s*x(?:\s*[\^²³]\s*\d*)?(?:\s*[+\-−]\s*\d+\.?\d*)?)", math_prompt)
        if not expression_match:
            return None
        expression = expression_match.group(1).strip()
        label = f"y = {expression}"

    for trailing_word in ["함수", "그래프", "좌표"]:
        if trailing_word in expression:
            expression = expression.split(trailing_word, 1)[0].strip()
    label = re.sub(r"\s*(함수|그래프|좌표).*$", "", label).strip()

    expression = replace_superscripts(expression)
    replacements = {
        "−": "-",
        "–": "-",
        "×": "*",
        "·": "*",
        "^": "**",
        "π": "pi",
        "√": "sqrt",
        "÷": "/",
    }
    for old, new in replacements.items():
        expression = expression.replace(old, new)

    expression = re.sub(r"(?<=\d)\s*x", "*x", expression)
    expression = re.sub(r"(?<=\d)\s*(?=\()", "*", expression)
    expression = re.sub(r"(?<=\))\s*x", "*x", expression)
    expression = re.sub(r"(?<=\))\s*(?=\d)", "*", expression)
    expression = re.sub(r"x\s*(?=\()", "x*", expression)
    expression = re.sub(r"\b(sin|cos|tan|sqrt|log|ln)\s*x\b", r"\1(x)", expression)
    expression = expression.replace("ln(", "log(")

    allowed = set("0123456789.x+-*/() pi_sqrtincotalge")
    if any(char not in allowed for char in expression):
        return None
    return expression, label


def safe_eval_function(expression: str, x: float) -> float:
    allowed_names = {
        "x": x,
        "pi": math.pi,
        "e": math.e,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "sqrt": math.sqrt,
        "log": math.log,
        "abs": abs,
    }
    return float(eval(expression, {"__builtins__": {}}, allowed_names))


def draw_function_graph(prompt: str, output_path: Path, expression: str, label: str) -> None:
    scale = 3
    image, draw = new_canvas(prompt, scale=scale)
    font = load_font(24 * scale)
    small_font = load_font(20 * scale)
    width, height = image.size
    margin = 85 * scale
    xmin, xmax = -10.0, 10.0
    ymin, ymax = -10.0, 10.0
    graph_width = width - 2 * margin
    graph_height = height - 2 * margin

    def to_px(x: float, y: float) -> tuple[int, int]:
        px = margin + int((x - xmin) / (xmax - xmin) * graph_width)
        py = height - margin - int((y - ymin) / (ymax - ymin) * graph_height)
        return px, py

    grid_color = (225, 225, 225)
    axis_color = (45, 45, 45)
    for value in range(-10, 11):
        x0, y0 = to_px(value, ymin)
        x1, y1 = to_px(value, ymax)
        draw.line([(x0, y0), (x1, y1)], fill=grid_color, width=scale)
        x2, y2 = to_px(xmin, value)
        x3, y3 = to_px(xmax, value)
        draw.line([(x2, y2), (x3, y3)], fill=grid_color, width=scale)

    draw.line([to_px(xmin, 0), to_px(xmax, 0)], fill=axis_color, width=3 * scale)
    draw.line([to_px(0, ymin), to_px(0, ymax)], fill=axis_color, width=3 * scale)
    origin_px = to_px(0, 0)
    draw.ellipse(
        (
            origin_px[0] - 4 * scale,
            origin_px[1] - 4 * scale,
            origin_px[0] + 4 * scale,
            origin_px[1] + 4 * scale,
        ),
        fill=TEXT,
    )
    draw.text((origin_px[0] + 8 * scale, origin_px[1] + 6 * scale), "O", fill=TEXT, font=small_font)
    draw.text((width - margin + 10 * scale, origin_px[1] - 12 * scale), "x", fill=TEXT, font=font)
    draw.text((origin_px[0] + 8 * scale, margin - 35 * scale), "y", fill=TEXT, font=font)

    segments: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for step in range(801):
        x = xmin + (xmax - xmin) * step / 800
        try:
            y = safe_eval_function(expression, x)
            if not math.isfinite(y) or y < ymin or y > ymax:
                raise ValueError
            current.append(to_px(x, y))
        except Exception:
            if len(current) > 1:
                segments.append(current)
            current = []
    if len(current) > 1:
        segments.append(current)

    for segment in segments:
        draw.line(segment, fill=RED, width=4 * scale)

    if segments:
        longest_segment = max(segments, key=len)
        anchor = longest_segment[int(len(longest_segment) * 0.65)]
        label_text = label.replace("−", "-").replace("–", "-")
        text_box = draw.textbbox((0, 0), label_text, font=small_font)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]

        label_x = anchor[0] + 36 * scale
        if label_x + text_width > width - margin:
            label_x = anchor[0] - text_width - 36 * scale
        label_x = max(margin + 12 * scale, min(label_x, width - margin - text_width - 8 * scale))

        label_y = anchor[1] - text_height - 34 * scale
        if label_y < margin + 8 * scale:
            label_y = anchor[1] + 34 * scale
        label_y = max(margin + 8 * scale, min(label_y, height - margin - text_height - 8 * scale))

        label_anchor = (label_x, label_y + text_height // 2)
        draw.line([anchor, label_anchor], fill=RED, width=2 * scale)
        draw.ellipse(
            (
                anchor[0] - 4 * scale,
                anchor[1] - 4 * scale,
                anchor[0] + 4 * scale,
                anchor[1] + 4 * scale,
            ),
            fill=RED,
        )
        draw.text((label_x, label_y), label_text, fill=RED, font=small_font)

    add_caption(draw, prompt, image.size, scale=scale)
    image = image.resize(CANVAS_SIZE, Image.Resampling.LANCZOS)
    image.save(output_path)


def draw_basic_graph(prompt: str, output_path: Path) -> None:
    parsed = normalize_function_expression(prompt)
    if parsed is not None:
        expression, label = parsed
        draw_function_graph(prompt, output_path, expression, label)
        return

    draw_function_graph(prompt, output_path, "0.5*x+1", "y = 0.5x + 1")


def draw_placeholder(prompt: str, output_path: Path) -> None:
    image, draw = new_canvas(prompt)
    font = load_font(30)
    small_font = load_font(23)

    draw.rectangle((170, 140, 730, 455), outline=BLUE, width=5)
    draw.line((170, 455, 450, 140, 730, 455), fill=BLUE, width=4)
    draw.ellipse((405, 245, 495, 335), outline=RED, width=4)
    draw.text((245, 70), "이미지 요청을 해석하지 못했습니다", fill=TEXT, font=font)
    draw.text((135, 500), "지원 예: 원, 삼각형, 직각삼각형, 사각형, 정사각형, 그래프", fill=(80, 80, 80), font=small_font)

    add_caption(draw, prompt, image.size)
    image.save(output_path)


def render_image(request: ImageRequest) -> None:
    prompt = request.prompt
    normalized = prompt.replace(" ", "").lower()
    function_expression = normalize_function_expression(prompt)

    if "원" in normalized or "반지름" in normalized or "지름" in normalized:
        draw_circle(prompt, request.output_path)
    elif "탑" in normalized and ("삼각형" in normalized or "높이" in normalized):
        draw_tower_height_triangle(prompt, request.output_path)
    elif "직각삼각형" in normalized and ("각b" in normalized or "b가직각" in normalized or "b는직각" in normalized):
        draw_right_triangle_b(prompt, request.output_path)
    elif "직각삼각형" in normalized:
        draw_right_triangle_b(prompt, request.output_path)
    elif "삼각형" in normalized:
        draw_generic_triangle(prompt, request.output_path)
    elif any(word in normalized for word in ["정사각형", "직사각형", "사각형", "마름모", "평행사변형"]):
        draw_quadrilateral(prompt, request.output_path)
    elif any(word in normalized for word in ["오각형", "육각형", "다각형"]):
        draw_regular_polygon(prompt, request.output_path)
    elif function_expression is not None or "그래프" in normalized or "좌표" in normalized or "함수" in normalized:
        draw_basic_graph(prompt, request.output_path)
    else:
        draw_placeholder(prompt, request.output_path)


def render_all(requests: Iterable[ImageRequest]) -> list[Path]:
    outputs = []
    for request in requests:
        render_image(request)
        outputs.append(request.output_path)
    return outputs


def safe_filename_part(text: str, max_length: int = 24) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", text)
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    return (cleaned[:max_length] or "프롬프트")


def render_prompt(prompt: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_filename_part(prompt)}_이미지1.PNG"
    request = ImageRequest(index=1, prompt=prompt, output_path=output_path)
    render_image(request)
    return output_path


def select_txt_file() -> Path | None:
    try:
        from tkinter import Tk, filedialog
    except Exception:
        return select_txt_file_windows()

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askopenfilename(
        title="이미지 요청이 들어 있는 TXT 파일을 선택하세요",
        filetypes=[("텍스트 파일", "*.txt"), ("모든 파일", "*.*")],
    )
    root.destroy()
    if not selected:
        return None
    return Path(selected)


def select_txt_file_windows() -> Path | None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", wintypes.LPVOID),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", wintypes.LPVOID),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    file_buffer = ctypes.create_unicode_buffer(4096)
    ofn = OpenFileName()
    ofn.lStructSize = ctypes.sizeof(OpenFileName)
    ofn.lpstrFilter = "텍스트 파일\0*.txt\0모든 파일\0*.*\0"
    ofn.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    ofn.nMaxFile = len(file_buffer)
    ofn.lpstrTitle = "이미지 요청이 들어 있는 TXT 파일을 선택하세요"
    ofn.lpstrDefExt = "txt"
    ofn.Flags = 0x00080000 | 0x00001000 | 0x00000800

    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return Path(file_buffer.value)
    return None


def run_console_menu() -> None:
    print("이미지생성기")
    print("1. TXT 파일에서 [이미지 필요 : ...] 항목 생성")
    print("2. 프롬프트를 직접 입력해서 이미지 1장 생성")
    print("3. 배경삽화 폴더 위치 보기")
    choice = input("선택(1/2/3, 기본값 1): ").strip() or "1"

    if choice == "2":
        prompt = input("이미지 요청 문장: ").strip()
        if not prompt:
            print("프롬프트가 비어 있어 종료합니다.")
            return
        output = render_prompt(prompt, Path.cwd())
        print(output)
        return

    if choice == "3":
        print(get_background_dir())
        return

    txt_path = select_txt_file()
    if txt_path is None:
        print("TXT 파일 선택이 취소되었습니다.")
        return
    process_txt_file(txt_path.resolve())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TXT 파일 또는 직접 입력한 프롬프트로 수학 도형/그래프 PNG 이미지를 생성합니다."
    )
    parser.add_argument("txt_file", type=Path, nargs="?", help="이미지 요청이 들어 있는 텍스트 파일 경로")
    parser.add_argument("--prompt", "-p", help="TXT 파일 없이 바로 생성할 이미지 요청 문장")
    parser.add_argument("--output-dir", "-o", type=Path, default=Path.cwd(), help="프롬프트 모드 이미지 저장 폴더")
    parser.add_argument("--interactive", "-i", action="store_true", help="콘솔에서 프롬프트를 입력받아 이미지 생성")
    parser.add_argument("--background-dir", action="store_true", help="배경삽화 폴더 위치만 출력")
    return parser.parse_args()


def process_txt_file(txt_path: Path) -> None:
    if not txt_path.exists():
        raise SystemExit(f"파일을 찾을 수 없습니다: {txt_path}")
    if txt_path.suffix.lower() != ".txt":
        raise SystemExit("TXT 파일만 입력할 수 있습니다.")

    result = extract_image_requests(txt_path)
    if not result.requests:
        print("이미지 요청을 찾지 못했습니다. 예: [이미지 필요: 각 B가 직각인 직각삼각형]")
        return

    result.numbered_text_path.write_text(result.numbered_text, encoding="utf-8")
    outputs = render_all(result.requests)
    for output in outputs:
        print(output)
    print(result.numbered_text_path)


def main() -> None:
    args = parse_args()
    background_dir = get_background_dir()
    background_dir.mkdir(exist_ok=True)

    if args.background_dir:
        print(background_dir)
        return

    if args.prompt:
        output = render_prompt(args.prompt, args.output_dir.resolve())
        print(output)
        return

    if args.interactive:
        print("이미지 요청 문장을 입력하세요. 예: 탑 배경 위에 직각삼각형, y=2x+1 함수 그래프")
        prompt = input("> ").strip()
        if not prompt:
            print("프롬프트가 비어 있어 종료합니다.")
            return
        output = render_prompt(prompt, args.output_dir.resolve())
        print(output)
        return

    txt_path = args.txt_file
    if txt_path is None:
        run_console_menu()
        return

    txt_path = txt_path.resolve()
    process_txt_file(txt_path)


if __name__ == "__main__":
    main()

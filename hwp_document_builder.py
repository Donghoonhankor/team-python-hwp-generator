from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"\[이미지\s*필요\s*(\d+)\s*:\s*.*?\]", re.DOTALL)
BRACKET_FORMULA_PATTERN = re.compile(r"\[수식\s*:\s*(.*?)\]", re.DOTALL)
LEGACY_FORMULA_PATTERN = re.compile(r"#\$(.*?)\$#", re.DOTALL)
QUESTION_START_PATTERN = re.compile(r"^\s*문항\s*\d+\s*[\.\)]?", re.IGNORECASE)
SUPPORTED_IMAGE_SUFFIXES = (".PNG", ".png", ".JPG", ".jpg", ".JPEG", ".jpeg", ".BMP", ".bmp")


@dataclass(frozen=True)
class TextToken:
    kind: str
    value: str = ""


@dataclass(frozen=True)
class ImageToken:
    kind: str
    index: int


@dataclass(frozen=True)
class FormulaToken:
    kind: str
    value: str


Token = TextToken | ImageToken | FormulaToken


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def find_image_path(txt_path: Path, image_index: int) -> Path:
    stems = [txt_path.stem]
    if txt_path.stem.endswith("_이미지번호매칭"):
        stems.append(txt_path.stem[: -len("_이미지번호매칭")])

    for stem in stems:
        for suffix in SUPPORTED_IMAGE_SUFFIXES:
            candidate = txt_path.with_name(f"{stem}_이미지{image_index}{suffix}")
            if candidate.exists():
                return candidate

    searched = ", ".join(f"{stem}_이미지{image_index}.*" for stem in stems)
    raise FileNotFoundError(f"이미지 {image_index} 파일을 찾을 수 없습니다. 찾은 이름: {searched}")


def iter_inline_tokens(text: str) -> list[Token]:
    tokens: list[Token] = []
    pattern = re.compile(
        (
            f"{IMAGE_PLACEHOLDER_PATTERN.pattern}|"
            f"{BRACKET_FORMULA_PATTERN.pattern}|"
            f"{LEGACY_FORMULA_PATTERN.pattern}"
        ),
        re.DOTALL,
    )
    last_end = 0

    for match in pattern.finditer(text):
        if match.start() > last_end:
            tokens.append(TextToken("text", text[last_end : match.start()]))

        image_match = IMAGE_PLACEHOLDER_PATTERN.fullmatch(match.group(0))
        if image_match is not None:
            tokens.append(ImageToken("image", int(image_match.group(1))))
        else:
            formula_match = BRACKET_FORMULA_PATTERN.fullmatch(match.group(0))
            if formula_match is None:
                formula_match = LEGACY_FORMULA_PATTERN.fullmatch(match.group(0))
            formula = "" if formula_match is None else formula_match.group(1).strip()
            if formula:
                tokens.append(FormulaToken("formula", normalize_formula(formula)))

        last_end = match.end()

    if last_end < len(text):
        tokens.append(TextToken("text", text[last_end:]))

    return tokens


def normalize_formula(formula: str) -> str:
    replacements = {
        "−": "-",
        "–": "-",
        "×": "TIMES",
        "÷": "DIV",
        "≤": "<=",
        "≥": ">=",
        "≠": "!=",
        "π": "pi",
        "√": "sqrt",
        "²": "^2",
        "³": "^3",
        "⁰": "^0",
        "¹": "^1",
        "⁴": "^4",
        "⁵": "^5",
        "⁶": "^6",
        "⁷": "^7",
        "⁸": "^8",
        "⁹": "^9",
    }
    result = formula
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def split_question_lines(text: str) -> list[tuple[str, str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    output: list[tuple[str, str]] = []
    in_question = False

    for index, line in enumerate(lines):
        current_is_question = QUESTION_START_PATTERN.match(line) is not None
        next_is_question = index + 1 < len(lines) and QUESTION_START_PATTERN.match(lines[index + 1]) is not None

        if current_is_question:
            in_question = True

        output.append(("line", line))

        if index == len(lines) - 1:
            continue

        if in_question and line.strip() and not next_is_question:
            output.append(("softbreak", ""))
        else:
            output.append(("paragraph", ""))

    return output


def build_tokens(text: str) -> list[Token]:
    tokens: list[Token] = []
    for kind, value in split_question_lines(text):
        if kind == "line":
            tokens.extend(iter_inline_tokens(value))
        elif kind == "softbreak":
            tokens.append(TextToken("softbreak"))
        else:
            tokens.append(TextToken("paragraph"))
    return tokens


class HwpWriter:
    def __init__(self, visible: bool = False) -> None:
        try:
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("pywin32가 필요합니다. 먼저 `python -m pip install pywin32`를 실행하세요.") from exc

        self.hwp = win32com.client.gencache.EnsureDispatch("HWPFrame.HwpObject")
        try:
            self.hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
        except Exception:
            pass
        self.hwp.XHwpWindows.Item(0).Visible = visible
        self.hwp.Run("FileNew")

    def insert_text(self, text: str) -> None:
        if not text:
            return
        action = self.hwp.CreateAction("InsertText")
        param = action.CreateSet()
        action.GetDefault(param)
        param.SetItem("Text", text)
        action.Execute(param)

    def soft_break(self) -> None:
        self.hwp.HAction.Run("BreakLine")

    def paragraph(self) -> None:
        self.hwp.HAction.Run("BreakPara")

    def insert_formula(self, formula: str) -> None:
        action = self.hwp.CreateAction("EquationCreate")
        param = action.CreateSet()
        action.GetDefault(param)
        param.SetItem("String", formula)
        action.Execute(param)

    def insert_image(self, image_path: Path) -> None:
        image = str(image_path.resolve())
        try:
            self.hwp.InsertPicture(image, True, 1, False, False, 0, 0, 0)
            return
        except Exception:
            pass

        action = self.hwp.CreateAction("InsertPicture")
        param = action.CreateSet()
        action.GetDefault(param)
        param.SetItem("FileName", image)
        param.SetItem("Embed", True)
        action.Execute(param)

    def save_as(self, output_path: Path) -> None:
        output = str(output_path.resolve())
        self.hwp.SaveAs(output, "HWP", "")

    def quit(self) -> None:
        try:
            self.hwp.Quit()
        except Exception:
            pass


def create_hwp(txt_path: Path, visible: bool = False) -> Path:
    txt_path = txt_path.resolve()
    text = read_text(txt_path)
    output_path = txt_path.with_suffix(".hwp")
    tokens = build_tokens(text)
    writer = HwpWriter(visible=visible)

    try:
        for token in tokens:
            if isinstance(token, ImageToken):
                writer.insert_image(find_image_path(txt_path, token.index))
            elif isinstance(token, FormulaToken):
                writer.insert_formula(token.value)
            elif token.kind == "softbreak":
                writer.soft_break()
            elif token.kind == "paragraph":
                writer.paragraph()
            else:
                writer.insert_text(token.value)

        writer.save_as(output_path)
    finally:
        writer.quit()

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
        title="HWP로 변환할 TXT 파일을 선택하세요",
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
    ofn.lpstrTitle = "HWP로 변환할 TXT 파일을 선택하세요"
    ofn.lpstrDefExt = "txt"
    ofn.Flags = 0x00080000 | 0x00001000 | 0x00000800

    if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return Path(file_buffer.value)
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="문항 TXT와 번호 이미지 파일을 결합해 같은 이름의 HWP 파일을 생성합니다."
    )
    parser.add_argument("txt_file", type=Path, nargs="?", help="변환할 텍스트 파일 경로")
    parser.add_argument("--visible", action="store_true", help="한글 창을 보이게 실행")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    txt_path = args.txt_file or select_txt_file()
    if txt_path is None:
        print("TXT 파일 선택이 취소되었습니다.")
        return
    if not txt_path.exists():
        raise SystemExit(f"파일을 찾을 수 없습니다: {txt_path}")
    if txt_path.suffix.lower() != ".txt":
        raise SystemExit("TXT 파일만 입력할 수 있습니다.")

    output_path = create_hwp(txt_path, visible=args.visible)
    print(output_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"오류: {exc}", file=sys.stderr)
        raise

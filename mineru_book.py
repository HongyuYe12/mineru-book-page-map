#!/usr/bin/env python3
"""Convert a PDF with MinerU and keep a searchable PDF-page mapping.

The script deliberately treats MinerU as an external executable. MinerU has
changed its CLI flags between releases, so the executable and backend are
configurable instead of being hard-coded into a Python API integration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PAGE_MARKER_RE = re.compile(
    r"<!--\s*(?:pdf[- _]?page|page(?:\s*(?:number|no\.?)?)?)\s*[:=]?\s*(\d+)\s*-->",
    re.IGNORECASE,
)
WS_RE = re.compile(r"\s+")


@dataclass
class PageEntry:
    pdf_page: int
    markdown_line: int | None
    marker: str
    snippet: str
    matched: bool
    source: str


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def configure_console() -> None:
    """Keep Markdown text printable on Windows consoles using legacy encodings."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def normalize(value: str) -> str:
    """Normalize text enough to survive line wrapping and OCR whitespace."""
    value = (
        value.replace("\u00a0", " ")
        .replace("��", " ")
        .replace("–", "-")
        .replace("—", "-")
        .lower()
    )
    return WS_RE.sub(" ", value).strip()


def significant_text(value: str, minimum: int = 18) -> str:
    value = normalize(value)
    if len(value) <= minimum:
        return value
    # A short prefix is more likely to be found verbatim than a whole table.
    return value[: min(160, max(minimum, len(value)))]


def find_normalized(haystack: str, needle: str, start: int = 0) -> int | None:
    """Find normalized needle and return its raw-text offset."""
    needle_n = normalize(needle)
    if not needle_n:
        return None
    normalized_chars: list[str] = []
    raw_offsets: list[int] = []
    previous_space = False
    for index, char in enumerate(haystack[start:], start):
        char = char.lower().replace("\u00a0", " ")
        if char == "��":
            char = " "
        elif char in {"–", "—"}:
            char = "-"
        if char.isspace():
            if previous_space:
                continue
            char = " "
            previous_space = True
        else:
            previous_space = False
        normalized_chars.append(char)
        raw_offsets.append(index)
    normalized_haystack = "".join(normalized_chars).strip()
    offset = normalized_haystack.find(needle_n)
    if offset < 0:
        return None
    # Stripping the leading whitespace can shift this index by a small amount.
    leading = len("".join(normalized_chars)) - len("".join(normalized_chars).lstrip())
    offset += leading
    return raw_offsets[offset] if offset < len(raw_offsets) else None


def read_pdf_page_count(pdf_path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing pypdf. Install it with: python -m pip install pypdf") from exc
    return len(PdfReader(str(pdf_path)).pages)


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return [(page.extract_text() or "") for page in reader.pages]


def collect_json_items(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from collect_json_items(item)
    elif isinstance(value, dict):
        if "page_idx" in value and any(key in value for key in ("text", "content", "table_body")):
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from collect_json_items(child)


def flatten_text(value: Any) -> str:
    """Collect text leaves from a MiddleJson page/block structure."""
    parts: list[str] = []
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            text = flatten_text(item)
            if text:
                parts.append(text)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"text", "content", "table_body"}:
                text = flatten_text(item)
                if text:
                    parts.append(text)
            elif isinstance(item, (dict, list)):
                text = flatten_text(item)
                if text:
                    parts.append(text)
    return " ".join(parts)


def block_candidates(blocks: Any) -> list[str]:
    """Return reliable Markdown anchors from MinerU 4 page blocks."""
    candidates: list[str] = []
    if not isinstance(blocks, list):
        return candidates
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text = normalize(flatten_text(block))
        if len(text) < 20:
            continue
        for length in (160, 120, 80, 50, 30):
            anchor = text[:length].strip()
            if len(anchor) >= 20:
                candidates.append(anchor)
        # Some title blocks are short but highly distinctive.
        if text not in candidates:
            candidates.append(text)
    return candidates


def collect_page_items(value: Any) -> Iterable[dict[str, Any]]:
    """Yield page-level text from MinerU 4 MiddleJson pages."""
    if isinstance(value, list):
        for item in value:
            yield from collect_page_items(item)
    elif isinstance(value, dict):
        if "page_idx" in value and "blocks" in value:
            yield {
                "page_idx": value["page_idx"],
                "text": flatten_text(value["blocks"]),
                "anchors": block_candidates(value["blocks"]),
            }
            return
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from collect_page_items(child)


def item_text(item: dict[str, Any]) -> str:
    for key in ("text", "content"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    table = item.get("table_body")
    if isinstance(table, str):
        return table
    if isinstance(table, list):
        return " ".join(str(part) for part in table)
    return ""


def find_content_list(output_root: Path, stem: str) -> Path | None:
    # MinerU 4 ZIP: middle_json.json. MinerU 3: *_content_list.json.
    candidates = list(output_root.rglob("*_content_list.json"))
    candidates += list(output_root.rglob("content_list.json"))
    candidates += list(output_root.rglob("middle_json.json"))
    candidates += list(output_root.rglob("middle.json"))
    if not candidates:
        return None
    exact = [path for path in candidates if path.stem.removesuffix("_content_list") == stem]
    priority = {"middle_json.json": 0, "middle.json": 1, "content_list.json": 2}
    return sorted(exact or set(candidates), key=lambda path: (priority.get(path.name, 3), str(path)))[0]


def find_markdown(output_root: Path, stem: str) -> Path:
    candidates = [path for path in output_root.rglob("*.md") if path.name != "page_map.md"]
    if not candidates:
        raise RuntimeError(f"MinerU did not produce a Markdown file under {output_root}")
    exact = [path for path in candidates if path.stem == stem]
    return sorted(exact or candidates, key=lambda path: (len(path.parts), str(path)))[0]


def copy_images(mineru_root: Path, output_dir: Path) -> int:
    """Copy MinerU image assets next to the final Markdown."""
    sources = [path for path in mineru_root.rglob("images") if path.is_dir()]
    if not sources:
        return 0
    destination = output_dir / "images"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in sources:
        for item in source.rglob("*"):
            if not item.is_file():
                continue
            target = destination / item.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
    return copied


def markdown_image_paths(markdown: str) -> list[str]:
    return re.findall(r"!\[[^]]*\]\(([^)]+)\)", markdown)


def verify_output(output_dir: Path, *, strict: bool = False) -> dict[str, Any]:
    index, index_path = load_index(output_dir)
    markdown_path = Path(index["markdown"])
    markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
    pages = index.get("pages", [])
    unmatched = [int(item["pdf_page"]) for item in pages if not item.get("matched")]
    markers = [int(value) for value in PAGE_MARKER_RE.findall(markdown)]
    expected_pages = list(range(1, int(index.get("page_count", len(pages))) + 1))
    missing_markers = sorted(set(expected_pages) - set(markers))
    unexpected_markers = sorted(set(markers) - set(expected_pages))
    duplicate_markers = sorted({page for page in markers if markers.count(page) > 1})
    marker_order_valid = markers == expected_pages
    image_refs = markdown_image_paths(markdown)
    broken_images = sorted(
        path
        for path in image_refs
        if not path.startswith(("http://", "https://", "data:"))
        and not (markdown_path.parent / path).is_file()
    )
    matched = len(pages) - len(unmatched)
    report = {
        "index": str(index_path),
        "markdown": str(markdown_path),
        "page_count": len(pages),
        "matched_pages": matched,
        "mapping_rate": (matched / len(pages)) if pages else 0.0,
        "unmatched_pages": unmatched,
        "missing_markers": missing_markers,
        "unexpected_markers": unexpected_markers,
        "duplicate_markers": duplicate_markers,
        "marker_order_valid": marker_order_valid,
        "image_references": len(image_refs),
        "broken_images": broken_images,
        "ok": (
            not unmatched
            and not missing_markers
            and not unexpected_markers
            and not duplicate_markers
            and marker_order_valid
            and not broken_images
        ),
    }
    if strict and not report["ok"]:
        raise RuntimeError(
            "Verification failed: "
            f"unmatched pages={unmatched}, missing markers={missing_markers}, "
            f"unexpected markers={unexpected_markers}, duplicate markers={duplicate_markers}, "
            f"marker order valid={marker_order_valid}, broken images={broken_images}"
        )
    return report


def run_mineru(pdf_path: Path, mineru_root: Path, command: str, backend: str) -> str:
    command_parts = shlex.split(command, posix=os.name != "nt")
    if not command_parts:
        raise ValueError("--mineru-command cannot be empty")
    zip_path = mineru_root / f"{pdf_path.stem}.zip"
    # MinerU 4 stateless conversion. ZIP preserves Markdown, MiddleJson and images
    # from one parse, so page metadata and rendered text stay in sync.
    tier = "standard" if backend == "auto" else backend
    v4 = command_parts + ["parse", str(pdf_path), "-o", str(zip_path), "--format", "zip", "--tier", tier]
    # Legacy fallback for MinerU 3.x installations.
    legacy = command_parts + ["-p", str(pdf_path), "-o", str(mineru_root), "-m", backend]
    attempts = [v4, legacy]
    errors: list[str] = []
    for argv in attempts:
        print(f"Starting MinerU: {' '.join(argv)}", flush=True)
        output_lines: list[str] = []
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\r\n")
            output_lines.append(line)
            print(line, flush=True)
        returncode = process.wait()
        combined = "\n".join(output_lines).strip()
        if returncode == 0:
            if zip_path.exists():
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(mineru_root / "extracted")
            return combined
        errors.append(f"$ {' '.join(argv)}\n{combined}")
        # A second attempt is useful for older MinerU builds that do not have -m.
    raise RuntimeError("MinerU failed. Attempts:\n\n" + "\n\n".join(errors))


def existing_markers(markdown: str) -> dict[int, int]:
    markers: dict[int, int] = {}
    for match in PAGE_MARKER_RE.finditer(markdown):
        markers.setdefault(int(match.group(1)), match.start())
    return markers


def marker_line(markdown: str, position: int) -> int:
    return markdown.count("\n", 0, position) + 1


def insert_markers(markdown: str, positions: dict[int, int]) -> str:
    inserts: list[tuple[int, str]] = []
    for page, position in positions.items():
        line_start = markdown.rfind("\n", 0, position) + 1
        marker = f"<!-- pdf-page: {page} -->\n"
        if not markdown[line_start : line_start + len(marker)].startswith("<!-- pdf-page:"):
            inserts.append((line_start, marker))
    for position, value in sorted(inserts, reverse=True):
        markdown = markdown[:position] + value + markdown[position:]
    return markdown


def build_mapping(
    markdown: str,
    pdf_pages: list[str],
    content_list: Path | None,
) -> tuple[str, list[PageEntry]]:
    markers = existing_markers(markdown)
    positions: dict[int, int] = {}
    sources: dict[int, str] = {}
    snippets: dict[int, str] = {}

    if content_list:
        try:
            payload = json.loads(content_list.read_text(encoding="utf-8"))
            items = list(collect_page_items(payload)) or list(collect_json_items(payload))
        except (OSError, json.JSONDecodeError) as exc:
            eprint(f"Warning: cannot read {content_list}: {exc}")
            items = []
        cursor = 0
        for item in items:
            try:
                page = int(item["page_idx"]) + 1
            except (KeyError, TypeError, ValueError):
                continue
            if page < 1 or page > len(pdf_pages) or page in markers or page in positions:
                continue
            anchors = item.get("anchors") if isinstance(item.get("anchors"), list) else []
            text = significant_text(item_text(item))
            if len(text) >= 12:
                anchors = [text, *anchors]
            found: int | None = None
            matched_anchor = ""
            for anchor in anchors:
                found = find_normalized(markdown, str(anchor), cursor)
                if found is not None:
                    matched_anchor = str(anchor)
                    break
            # Only fall back to a global search when no ordered anchor matched.
            if found is None:
                for anchor in anchors:
                    found = find_normalized(markdown, str(anchor))
                    if found is not None:
                        matched_anchor = str(anchor)
                        break
            if found is not None:
                positions[page] = found
                snippets[page] = matched_anchor[:180]
                sources[page] = "mineru-block-anchor" if item.get("anchors") else "mineru-content-list"
                cursor = found + len(matched_anchor)

    # Existing markers are authoritative and do not need another insertion.
    for page, position in markers.items():
        positions.setdefault(page, position)
        sources.setdefault(page, "mineru-markdown-marker")

    # Fallback for MinerU versions that omit content_list.json and page markers.
    cursor = 0
    for page, text in enumerate(pdf_pages, start=1):
        if page in positions:
            continue
        snippet = significant_text(text)
        found = find_normalized(markdown, snippet, cursor) if len(snippet) >= 12 else None
        if found is None and len(snippet) >= 12:
            found = find_normalized(markdown, snippet)
        if found is not None:
            positions[page] = found
            snippets[page] = snippet[:180]
            sources[page] = "pdf-text-fallback"
            cursor = found + len(snippet)

    annotated = insert_markers(markdown, {page: pos for page, pos in positions.items() if page not in markers})
    entries: list[PageEntry] = []
    for page in range(1, len(pdf_pages) + 1):
        # Re-scan after insertion: earlier markers shift later raw offsets.
        final_position = annotated.find(f"<!-- pdf-page: {page} -->")
        entries.append(
            PageEntry(
                pdf_page=page,
                markdown_line=marker_line(annotated, final_position) if final_position >= 0 else None,
                marker=f"<!-- pdf-page: {page} -->",
                snippet=snippets.get(page, significant_text(pdf_pages[page - 1])[:180]),
                matched=final_position >= 0,
                source=sources.get(page, "unmatched"),
            )
        )
    return annotated, entries


def write_page_map(path: Path, pdf_path: Path, markdown_path: Path, entries: list[PageEntry]) -> None:
    lines = [
        f"# PDF to Markdown page map: {pdf_path.name}",
        "",
        "The converted Markdown contains invisible markers of the form `<!-- pdf-page: N -->`.",
        "Use the Markdown line number below to jump from a search hit back to the source PDF page.",
        "",
        f"- Source PDF: `{pdf_path.name}`",
        f"- Markdown: `{markdown_path.name}`",
        f"- PDF pages: `{len(entries)}`",
        "",
        "| PDF page | Markdown line | Mapping source | Match | Text sample |",
        "| ---: | ---: | --- | --- | --- |",
    ]
    for entry in entries:
        sample = entry.snippet.replace("|", "\\|").replace("\n", " ").strip()
        if len(sample) > 120:
            sample = sample[:117] + "..."
        line = str(entry.markdown_line) if entry.markdown_line is not None else "-"
        lines.append(
            f"| {entry.pdf_page} | {line} | {entry.source} | {'yes' if entry.matched else 'no'} | {sample or '-'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def locate_outputs(root: Path, pdf_path: Path) -> tuple[Path, Path | None]:
    mineru_root = root / "mineru_raw"
    mineru_root.mkdir(parents=True, exist_ok=True)
    return mineru_root, find_content_list(mineru_root, pdf_path.stem)


def convert(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_pages = extract_pdf_pages(pdf_path)
    if not pdf_pages:
        raise RuntimeError("The PDF has no pages")
    mineru_root = output_dir / "mineru_raw"
    if args.clean and mineru_root.exists():
        shutil.rmtree(mineru_root)
    mineru_root.mkdir(parents=True, exist_ok=True)
    log = run_mineru(pdf_path, mineru_root, args.mineru_command, args.backend)
    raw_markdown = find_markdown(mineru_root, pdf_path.stem)
    markdown = raw_markdown.read_text(encoding="utf-8", errors="replace")
    content_list = find_content_list(mineru_root, pdf_path.stem)
    annotated, entries = build_mapping(markdown, pdf_pages, content_list)
    markdown_path = output_dir / f"{pdf_path.stem}.md"
    map_path = output_dir / f"{pdf_path.stem}.page-map.md"
    json_path = output_dir / f"{pdf_path.stem}.page-map.json"
    markdown_path.write_text(annotated, encoding="utf-8")
    copied_images = copy_images(mineru_root, output_dir)
    write_page_map(map_path, pdf_path, markdown_path, entries)
    metadata = {
        "source_pdf": str(pdf_path),
        "markdown": str(markdown_path),
        "page_map_markdown": str(map_path),
        "page_count": len(pdf_pages),
        "content_list": str(content_list) if content_list else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mineru_command": args.mineru_command,
        "mineru_backend": args.backend,
        "pages": [asdict(entry) for entry in entries],
        "copied_images": copied_images,
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "mineru.log.txt").write_text(log + "\n", encoding="utf-8")
    matched = sum(entry.matched for entry in entries)
    print(f"Markdown: {markdown_path}")
    print(f"Page map: {map_path}")
    print(f"JSON index: {json_path}")
    print(f"Page mapping: {matched}/{len(entries)} pages matched")
    print(f"Images copied: {copied_images}")
    report = verify_output(output_dir, strict=True)
    print(
        "Verification: OK "
        f"({report['matched_pages']}/{report['page_count']} pages, "
        f"{report['image_references']} image references, 0 broken)"
    )
    return 0


def rebuild(args: argparse.Namespace) -> int:
    """Rebuild final files from existing MinerU raw output without inference."""
    pdf_path = Path(args.pdf).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    mineru_root = output_dir / "mineru_raw"
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if not mineru_root.is_dir():
        raise FileNotFoundError(f"MinerU raw output not found: {mineru_root}")
    pdf_pages = extract_pdf_pages(pdf_path)
    raw_markdown = find_markdown(mineru_root, pdf_path.stem)
    markdown = raw_markdown.read_text(encoding="utf-8", errors="replace")
    content_list = find_content_list(mineru_root, pdf_path.stem)
    annotated, entries = build_mapping(markdown, pdf_pages, content_list)
    markdown_path = output_dir / f"{pdf_path.stem}.md"
    map_path = output_dir / f"{pdf_path.stem}.page-map.md"
    json_path = output_dir / f"{pdf_path.stem}.page-map.json"
    markdown_path.write_text(annotated, encoding="utf-8")
    copied_images = copy_images(mineru_root, output_dir)
    write_page_map(map_path, pdf_path, markdown_path, entries)
    metadata = {
        "source_pdf": str(pdf_path),
        "markdown": str(markdown_path),
        "page_map_markdown": str(map_path),
        "page_count": len(pdf_pages),
        "content_list": str(content_list) if content_list else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rebuild_from_existing_raw": True,
        "copied_images": copied_images,
        "pages": [asdict(entry) for entry in entries],
    }
    json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = verify_output(output_dir, strict=True)
    print(f"Rebuilt Markdown: {markdown_path}")
    print(f"Images copied: {copied_images}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def load_index(output_dir: Path) -> tuple[dict[str, Any], Path]:
    indexes = sorted(output_dir.glob("*.page-map.json"))
    if not indexes:
        raise FileNotFoundError(f"No *.page-map.json under {output_dir}")
    index_path = indexes[0]
    return json.loads(index_path.read_text(encoding="utf-8")), index_path


def search(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    index, index_path = load_index(output_dir)
    markdown_path = Path(index["markdown"])
    markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(args.query, re.IGNORECASE) if args.regex else re.compile(re.escape(args.query), re.IGNORECASE)
    page: int | None = None
    lines = markdown.splitlines()
    hits = 0
    for line_no, line in enumerate(lines, start=1):
        marker = PAGE_MARKER_RE.search(line)
        if marker:
            page = int(marker.group(1))
        if pattern.search(line) and "pdf-page:" not in line:
            hits += 1
            location = f"PDF page {page}" if page is not None else "PDF page unknown"
            print(f"{location} | Markdown line {line_no} | {line.strip()}")
    if not hits:
        print(f"No match for {args.query!r} in {markdown_path.name} (index: {index_path.name})")
    return 0


def verify(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    report = verify_output(output_dir, strict=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def page(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    index, _ = load_index(output_dir)
    requested = int(args.page)
    entries = {int(item["pdf_page"]): item for item in index["pages"]}
    entry = entries.get(requested)
    if not entry:
        raise ValueError(f"PDF page must be between 1 and {len(entries)}")
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MinerU PDF -> Markdown converter with PDF page mapping")
    sub = parser.add_subparsers(dest="command", required=True)
    convert_parser = sub.add_parser("convert", help="Run MinerU and produce Markdown plus page maps")
    convert_parser.add_argument("pdf", help="Input PDF")
    convert_parser.add_argument("-o", "--output-dir", default="./mineru-output", help="Output directory")
    convert_parser.add_argument("--mineru-command", default="mineru-kit", help="MinerU 4 mineru-kit executable or command prefix")
    convert_parser.add_argument("-m", "--backend", default="standard", help="MinerU 4 tier (standard/flash) or legacy backend")
    convert_parser.add_argument("--clean", action="store_true", help="Remove the previous mineru_raw directory first")
    convert_parser.set_defaults(func=convert)
    rebuild_parser = sub.add_parser("rebuild", help="Rebuild page maps and images from existing MinerU raw output")
    rebuild_parser.add_argument("pdf", help="Original input PDF")
    rebuild_parser.add_argument("-o", "--output-dir", required=True, help="Existing output directory")
    rebuild_parser.set_defaults(func=rebuild)
    search_parser = sub.add_parser("search", help="Search converted Markdown and report PDF page numbers")
    search_parser.add_argument("output_dir")
    search_parser.add_argument("query")
    search_parser.add_argument("--regex", action="store_true", help="Treat query as a regular expression")
    search_parser.set_defaults(func=search)
    verify_parser = sub.add_parser("verify", help="Verify page mapping and Markdown image integrity")
    verify_parser.add_argument("output_dir")
    verify_parser.set_defaults(func=verify)
    page_parser = sub.add_parser("page", help="Show the mapping record for one PDF page")
    page_parser.add_argument("output_dir")
    page_parser.add_argument("page", type=int)
    page_parser.set_defaults(func=page)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # CLI errors should be readable without a traceback.
        eprint(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

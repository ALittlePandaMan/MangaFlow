"""Classic RGB PSD with cached pixels and native Type Tool descriptors.

Format reference: Adobe Photoshop File Formats Specification, Type Tool Object
Setting (TySh), Action Descriptors and layer information. EngineData uses the
documented PostScript-like Photoshop text-engine dictionary representation.
"""
from __future__ import annotations

import math
import struct

from app.services.layout.text_frame import aligned_text_frame
from app.storage import get_storage
from fontTools.ttLib import TTFont
from PIL import Image, ImageColor


def pack(fmt, *args):
    return struct.pack(">" + fmt, *args)


def unicode_string(text):
    value = text.encode("utf-16-be")
    return pack("I", len(value) // 2) + value


def identifier(text):
    value = text.encode("ascii")
    return pack("I", 0 if len(value) == 4 else len(value)) + value


def descriptor(class_id, items):
    body = unicode_string("") + identifier(class_id) + pack("I", len(items))
    for key, (kind, value) in items.items():
        body += identifier(key) + kind.encode("ascii")
        if kind == "TEXT":
            body += unicode_string(value)
        elif kind == "enum":
            body += identifier(value[0]) + identifier(value[1])
        elif kind == "doub":
            body += pack("d", value)
        elif kind == "long":
            body += pack("i", value)
        elif kind == "bool":
            body += pack("B", bool(value))
        elif kind == "UntF":
            body += value[0].encode("ascii") + pack("d", value[1])
        elif kind == "tdta":
            body += pack("I", len(value)) + value
        elif kind == "Objc":
            body += value
        else:
            raise ValueError(f"Unsupported descriptor {kind}")
    return body


def engine_value(value):
    if isinstance(value, dict):
        return b"<<\n" + b"\n".join(b"/" + key.encode("ascii") + b" " + engine_value(item)
                                          for key, item in value.items()) + b"\n>>"
    if isinstance(value, list):
        return b"[ " + b" ".join(map(engine_value, value)) + b" ]"
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, (int, float)):
        return (str(value) if isinstance(value, int) else f"{value:.8f}").encode("ascii")
    data = b"\xfe\xff" + value.encode("utf-16-be")
    data = data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + data + b")"


def block(key, value):
    value += b"\0" * (-len(value) % 4)
    return b"8BIM" + key.encode("ascii") + pack("I", len(value)) + value


def text_data(region, layout, font_name):
    # Preserve the fitted line breaks so opening the document does not ask
    # Photoshop to guess the application's wrapping again.
    text = "\r".join(layout.get("lines") or region.translated_text.splitlines()) + "\r"
    length = len(text.encode("utf-16-be")) // 2
    size = float(layout.get("font_size", region.font_size))
    polygon = getattr(region, "translated_polygon", None) or getattr(region, "polygon", [])
    x, y, width, height = aligned_text_frame(polygon, region.rotation) or region.translated_bbox or region.bbox
    style = layout.get("render_style", {})
    rgb = ImageColor.getrgb(style.get("text_color", region.text_color))[:3]
    sheet = {"Font": 0, "FontSize": size, "FauxBold": region.font_weight >= 600 and not any(
        token in font_name.lower() for token in ("bold", "black", "heavy", "semibold")),
             "FauxItalic": False, "AutoLeading": False, "Leading": size * region.line_spacing,
             "Tracking": region.character_spacing * 1000 / max(1, size),
             "FillColor": {"Type": 1, "Values": [1.0, *(channel / 255 for channel in rgb)]},
             "FillFlag": True, "StrokeFlag": False, "HorizontalScale": 1.0, "VerticalScale": 1.0}
    paragraph = {"Justification": {"left": 0, "right": 1}.get(region.alignment, 2),
                 "FirstLineIndent": 0.0, "StartIndent": 0.0, "EndIndent": 0.0,
                 "SpaceBefore": 0.0, "SpaceAfter": 0.0, "AutoHyphenate": False}
    lengths = [len((line + "\r").encode("utf-16-be")) // 2 for line in text[:-1].split("\r")]
    vertical = region.orientation == "vertical"
    resources = {"FontSet": [{"Name": font_name, "Script": 0, "FontType": 1, "Synthetic": 0}],
                 "StyleSheetSet": [{"Name": "Normal RGB", "StyleSheetData": sheet}],
                 "ParagraphSheetSet": [{"Name": "Normal RGB", "DefaultStyleSheet": 0, "Properties": paragraph}],
                 "TheNormalStyleSheet": 0, "TheNormalParagraphSheet": 0}
    engine = {"EngineDict": {"Editor": {"Text": text},
        "ParagraphRun": {"DefaultRunData": {"ParagraphSheet": {"DefaultStyleSheet": 0, "Properties": paragraph}},
                         "RunArray": [{"ParagraphSheet": {"DefaultStyleSheet": 0, "Properties": paragraph}} for _ in lengths],
                         "RunLengthArray": lengths, "IsJoinable": 1},
        "StyleRun": {"DefaultRunData": {"StyleSheet": {"StyleSheetData": sheet}},
                     "RunArray": [{"StyleSheet": {"StyleSheetData": sheet}}], "RunLengthArray": [length], "IsJoinable": 2},
        "GridInfo": {"GridIsOn": False, "ShowGrid": False, "GridSize": 18.0, "GridLeading": 22.0,
                     "GridColor": {"Type": 1, "Values": [1.0, 0.0, 0.0, 1.0]}, "AlignLineHeightToGridFlags": False},
        "AntiAlias": 4, "UseFractionalGlyphWidths": True,
        "Rendered": {"Version": 1, "Shapes": {"WritingDirection": 2 if vertical else 0,
            "Children": [{"ShapeType": 1, "Procession": 1 if vertical else 0,
                          "WritingDirection": 2 if vertical else 0,
                          "Cookie": {"Photoshop": {"ShapeType": 1, "BoxBounds": [0.0, 0.0, width, height],
                                     "Base": {"ShapeType": 1, "TransformPoint0": [0.0, 0.0],
                                              "TransformPoint1": [1.0, 0.0], "TransformPoint2": [0.0, 1.0]}}}}]}}},
        "ResourceDict": resources, "DocumentResources": resources}
    bounds = descriptor("bounds", {key: ("doub", val) for key, val in
                         zip(("Left", "Top ", "Rght", "Btom"), (0., 0., width, height), strict=True)})
    orientation = "Vrtc" if vertical else "Hrzn"
    desc = descriptor("TxLr", {"Txt ": ("TEXT", text), "textGridding": ("enum", ("textGridding", "None")),
        "Ornt": ("enum", ("Ornt", orientation)), "AntA": ("enum", ("Annt", "antiAliasSharp")),
        "bounds": ("Objc", bounds), "boundingBox": ("Objc", bounds),
        "TextIndex": ("long", 0), "EngineData": ("tdta", engine_value(engine))})
    warp = descriptor("warp", {"warpStyle": ("enum", ("warpStyle", "warpNone")),
        "warpValue": ("doub", 0.), "warpPerspective": ("doub", 0.), "warpPerspectiveOther": ("doub", 0.),
        "warpRotate": ("enum", ("Ornt", orientation))})
    angle = math.radians(region.rotation)
    c, s = math.cos(angle), math.sin(angle)
    tx = x + width / 2 - c * width / 2 + s * height / 2
    ty = y + height / 2 - s * width / 2 - c * height / 2
    return (pack("H6dHI", 1, c, s, -s, c, tx, ty, 50, 16) + desc + pack("HI", 1, 16) + warp
            + pack("4i", 0, 0, round(height), round(width)))


def stroke_effect(region, style):
    width = style.get("stroke_width", region.stroke_width)
    if not width:
        return b""
    rgb = ImageColor.getrgb(style.get("stroke_color", region.stroke_color))[:3]
    color = descriptor("RGBC", {key: ("doub", float(value)) for key, value in zip(("Rd  ", "Grn ", "Bl  "), rgb, strict=True)})
    effect = descriptor("FrFX", {"enab": ("bool", True), "present": ("bool", True),
        "showInDialog": ("bool", True), "Styl": ("enum", ("FStl", "OutF")),
        "PntT": ("enum", ("FrFl", "SClr")), "Md  ": ("enum", ("BlnM", "Nrml")),
        "Opct": ("UntF", ("#Prc", 100.)), "Sz  ": ("UntF", ("#Pxl", float(width))), "Clr ": ("Objc", color)})
    return block("lfx2", pack("II", 0, 16) + descriptor("null", {
        "Scl ": ("UntF", ("#Prc", 100.)), "masterFXSwitch": ("bool", True), "FrFX": ("Objc", effect)}))


def write_psd(path, size, layers, composite, check=lambda: None, progress=lambda value: None):
    width, height = size
    if not 0 < width <= 30000 or not 0 < height <= 30000:
        raise ValueError("PSD 每边不能超过 30000 像素")
    if len(layers) > 32767:
        raise ValueError("PSD 图层数量超过限制")
    estimate = width * height * 3
    for layer in layers:
        if layer.get("path"):
            with Image.open(layer["path"]) as image:
                estimate += image.width * image.height * 4 + 65536
    if estimate >= 2_000_000_000:
        raise ValueError("PSD 超过 2 GB，请拆分页面或减少图层")
    with path.open("w+b") as writer:
        writer.write(b"8BPS" + pack("H", 1) + b"\0" * 6 + pack("HIIHH", 3, height, width, 8, 3))
        writer.write(pack("II", 0, 0))  # Color mode data, image resources.
        section = writer.tell()
        writer.write(pack("IIh", 0, 0, len(layers)))
        ordered = list(layers)
        progress(0)
        for index, layer in enumerate(ordered):
            check()
            left, top, right, bottom = layer.get("bounds", (0, 0, 0, 0))
            pixels = max(0, right - left) * max(0, bottom - top)
            channels = 4 if layer.get("path") else 0
            writer.write(pack("4iH", top, left, bottom, right, channels))
            for channel in (-1, 0, 1, 2)[:channels]:
                writer.write(pack("hI", channel, pixels + 2))
            writer.write(b"8BIMnorm" + pack("BBBB", 255, 0, 0 if layer.get("visible", True) else 2, 0))
            name = layer["name"]
            ascii_name = name.encode("ascii", errors="replace")[:255]
            pascal = bytes([len(ascii_name)]) + ascii_name
            pascal += b"\0" * (-len(pascal) % 4)
            extra = pack("II", 0, 0) + pascal + block("luni", unicode_string(name))
            if "group" in layer:
                extra += block("lsct", pack("I", layer["group"]))
            if layer.get("text"):
                extra += block("TySh", layer["text"]) + layer.get("effects", b"")
            writer.write(pack("I", len(extra)) + extra)
            progress(.1 * (index + 1) / max(1, len(ordered)))
        written_pixels = 0
        total_pixels = max(1, estimate - width * height * 3)
        for layer in ordered:
            check()
            if layer.get("path"):
                with Image.open(layer["path"]) as image:
                    rgba = image.convert("RGBA")
                    for channel in ("A", "R", "G", "B"):
                        writer.write(pack("H", 0))
                        writer.write(rgba.getchannel(channel).tobytes())
                        written_pixels += rgba.width * rgba.height
                        progress(.1 + .85 * min(1, written_pixels / total_pixels))
                    rgba.close()
        if (writer.tell() - section - 8) % 2:
            writer.write(b"\0")
        end_layers = writer.tell()
        writer.write(pack("I", 0))  # Global layer mask.
        end = writer.tell()
        writer.seek(section)
        writer.write(pack("II", end - section - 4, end_layers - section - 8))
        writer.seek(end)
        writer.write(pack("H", 0))
        with Image.open(composite) as image:
            rgb = image.convert("RGB")
            for index, channel in enumerate(("R", "G", "B")):
                check()
                writer.write(rgb.getchannel(channel).tobytes())
                progress(.95 + .05 * (index + 1) / 3)
            rgb.close()


def export_psd(page, render, directory, output, check, progress=lambda value: None):
    storage = get_storage()
    def image_layer(name, path, visible=True):
        with Image.open(path) as image:
            bounds = (0, 0, image.width, image.height)
        return {"name": name, "path": path, "bounds": bounds, "visible": visible}
    layers = [image_layer("Original", storage.absolute(page.original_path), False),
              image_layer("Clean", storage.absolute(page.clean_path or page.original_path))]
    fonts = set()
    for index, region in enumerate(page.regions):
        check()
        progress(.1 * index / max(1, len(page.regions)))
        if not region.translated_text.strip():
            continue
        layout = render["layouts"].get(region.id, {})
        tile = directory / "tiles" / f"{region.id}.png"
        placements = layout.get("placements", [])
        font_name = region.font_family
        if placements:
            try:
                with TTFont(placements[0]["font_path"], fontNumber=0, lazy=True) as font:
                    font_name = font["name"].getDebugName(6) or font_name
            except (OSError, KeyError):
                pass
        fonts.add(font_name)
        special = (region.perspective_warp or len({p["font_path"] for p in placements}) > 1
                   or region.orientation == "vertical" and any(char.isascii() and char.isalnum()
                                                                for char in region.translated_text))
        # Bottom-up layer list: end marker, children, group header.
        layers.append({"name": "</Layer group>", "group": 3})
        if region.pixel_mask_path:
            layers.append(image_layer("Mask", storage.absolute(region.pixel_mask_path), False))
        bounds = layout.get("render_style", {}).get("tile_bounds", (0, 0, 0, 0))
        text = {"name": "Editable text" if not special else "Editable text backup",
                "text": text_data(region, layout, font_name), "visible": region.visible and not special,
                "effects": stroke_effect(region, layout.get("render_style", {}))}
        if tile.exists():
            text.update(path=tile, bounds=bounds)
            plain = directory / "tiles" / f"{region.id}-plain.png"
            if plain.exists():
                text.update(path=plain, bounds=layout["render_style"]["plain_tile_bounds"])
        layers.append(text)
        if special and tile.exists():
            layers.append({"name": "Rendered text", "path": tile, "bounds": bounds, "visible": region.visible})
        layers.append({"name": region.region_key, "group": 1, "visible": region.visible})
    write_psd(output, (page.width, page.height), layers, directory / "translated.png", check,
              progress=lambda value: progress(.1 + .9 * value))
    return fonts

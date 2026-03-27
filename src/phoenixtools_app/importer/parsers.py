from __future__ import annotations

from dataclasses import dataclass

from lxml import etree
from lxml import html as lxml_html


@dataclass(frozen=True)
class InfoData:
    items: list[tuple[int, str]]
    systems: list[tuple[int, str]]
    affiliations: list[tuple[int, str]]
    item_types: list[tuple[int, str]]


@dataclass(frozen=True)
class PositionData:
    positions: list[dict[str, object]]


def parse_info_data(xml_text: str) -> InfoData:
    root = etree.fromstring(xml_text.encode("utf-8", errors="ignore"))
    items: list[tuple[int, str]] = []
    systems: list[tuple[int, str]] = []
    affiliations: list[tuple[int, str]] = []
    item_types: list[tuple[int, str]] = []

    for type_node in root.xpath("//data_types/type"):
        type_name = (type_node.get("name") or "").strip()
        pairs: list[tuple[int, str]] = []
        for data_node in type_node.xpath(".//data"):
            num = data_node.get("num")
            name = data_node.get("name") or ""
            if not num:
                continue
            try:
                pairs.append((int(num), name.strip()))
            except ValueError:
                continue
        if type_name == "Items":
            items = pairs
        elif type_name == "Systems":
            systems = pairs
        elif type_name == "Affiliation":
            affiliations = pairs
        elif type_name == "Item Type":
            item_types = pairs

    return InfoData(items=items, systems=systems, affiliations=affiliations, item_types=item_types)


def parse_pos_list(xml_text: str) -> PositionData:
    root = etree.fromstring(xml_text.encode("utf-8", errors="ignore"))
    out: list[dict[str, object]] = []
    for pos in root.xpath("//positions/position"):
        num = pos.get("num")
        if not num:
            continue
        try:
            pid = int(num)
        except ValueError:
            continue
        out.append(
            {
                "id": pid,
                "name": (pos.get("name") or "").strip() or None,
                "position_class": (pos.xpath("string(.//class)") or "").strip() or None,
                "design": (pos.xpath("string(.//design)") or "").strip() or None,
                "size": _parse_int((pos.xpath("string(.//size)") or "").strip().split(" ")[0]),
                "size_type": _parse_size_type((pos.xpath("string(.//size)") or "").strip()),
            }
        )
    return PositionData(positions=out)


@dataclass(frozen=True)
class MarketData:
    market_time: str | None
    stardate: str | None
    starbases: list[dict[str, object]]


def parse_market_xml(xml_text: str) -> MarketData:
    root = etree.fromstring(xml_text.encode("utf-8", errors="ignore"))
    market_node = root.xpath("//markets")
    market = market_node[0] if market_node else root

    market_time = (market.xpath("string(.//time)") or "").strip() or None
    stardate = (market.xpath("string(.//stardate)") or "").strip() or None

    starbases: list[dict[str, object]] = []
    for sb in market.xpath(".//starbase"):
        sb_id = _parse_int(sb.get("id"))
        if sb_id is None:
            continue
        system_id = _parse_int(sb.xpath("string(.//system/@id)") or None)
        cbody_id = _parse_int(sb.xpath("string(.//cbody/@id)") or None)
        docks = _parse_int(sb.xpath("string(.//docks/@quant)") or None)
        hiports = _parse_int(sb.xpath("string(.//hiport/@quant)") or None)
        maintenance = _parse_int(sb.xpath("string(.//maintenance/@quant)") or None)
        patches = _parse_float(sb.xpath("string(.//patches/@price)") or None)

        items: list[dict[str, object]] = []
        for item in sb.xpath(".//item"):
            item_id = _parse_int(item.get("id"))
            if item_id is None:
                continue
            name = (item.xpath("string(.//name)") or "").strip() or None
            buy_quant = _parse_int(item.xpath("string(.//buy/@quant)") or None)
            buy_price = _parse_float(item.xpath("string(.//buy/@price)") or None)
            sell_quant = _parse_int(item.xpath("string(.//sell/@quant)") or None)
            sell_price = _parse_float(item.xpath("string(.//sell/@price)") or None)
            items.append(
                {
                    "id": item_id,
                    "name": name,
                    "buy": None
                    if buy_quant is None or buy_price is None
                    else {"quantity": buy_quant, "price": buy_price},
                    "sell": None
                    if sell_quant is None or sell_price is None
                    else {"quantity": sell_quant, "price": sell_price},
                }
            )

        starbases.append(
            {
                "id": sb_id,
                "name": (sb.xpath("string(.//name)") or "").strip() or None,
                "aff_tag": (sb.xpath("string(.//aff)") or "").strip() or None,
                "system": {"id": system_id, "name": (sb.xpath("string(.//system)") or "").strip() or None},
                "cbody": {"id": cbody_id, "name": (sb.xpath("string(.//cbody)") or "").strip() or None},
                "docks": docks,
                "hiports": hiports,
                "maintenance": maintenance,
                "patches": patches,
                "items": items,
            }
        )

    return MarketData(market_time=market_time, stardate=stardate, starbases=starbases)


@dataclass(frozen=True)
class JumpMapData:
    systems: list[tuple[int, str]]
    links: list[tuple[int, int, int]]


def parse_jump_map_html(html_text: str) -> JumpMapData:
    doc = lxml_html.fromstring(html_text)
    systems: list[tuple[int, str]] = []
    for el in doc.xpath('//div[contains(@class,"jump_map_system")]'):
        text = (el.text_content() or "").strip()
        parsed = _parse_name_id(text)
        if parsed:
            systems.append(parsed)

    links: list[tuple[int, int, int]] = []
    for el in doc.xpath('//div[contains(@class,"jump_map_link")]'):
        title = (el.get("title") or "").strip()
        # Format: "NameA (61)<->NameB (103)[2 jumps]"
        if "<->" not in title:
            continue
        left, right = title.split("<->", 1)
        right_part, *rest = right.split("[", 1)
        a = _parse_name_id(left)
        b = _parse_name_id(right_part)
        if not a or not b:
            continue
        jumps = 1
        if rest:
            jumps = _parse_int(rest[0].replace("jumps", "").replace("jump", "").replace("]", "").strip()) or 1
        links.append((a[0], b[0], jumps))

    return JumpMapData(systems=systems, links=links)


@dataclass(frozen=True)
class SystemCbodiesData:
    cbodies: list[dict[str, object]]


def parse_system_cbodies_html(html_text: str) -> SystemCbodiesData:
    """
    Ports the Rails state machine parsing `td.cbody_text` cells.
    Returns list of cbodies with: cbody_id, name, cbody_type, quad, ring.
    """
    doc = lxml_html.fromstring(html_text)
    values: dict[int, dict[str, object]] = {}

    name_and_id: tuple[str, int] | None = None
    cbody_type: str | None = None
    quad: str | None = None

    for td in doc.xpath('//td[contains(@class,"cbody_text")]'):
        text = (td.text_content() or "").strip()
        if not text:
            continue

        if name_and_id is None:
            a = td.xpath(".//a")
            if a and ("cbody" in (a[0].get("href") or "")) and "(" in text and ")" in text:
                parsed = _parse_name_id(text.replace("- ", "").strip())
                if parsed:
                    name_and_id = (parsed[1], parsed[0])
                    cbody_type = None
                    quad = None
            continue

        # We have name/id; now parse type -> quad -> ring.
        if cbody_type is None:
            # Accept only known types; else reset.
            if text in {"Planet", "Gas Giant", "Moon", "Nebula", "Asteroid", "Asteroid Belt", "Wormhole", "Stargate"}:
                cbody_type = text
            else:
                name_and_id = None
            continue

        if quad is None:
            quad = text
            continue

        ring = text
        name, cid = name_and_id
        values[cid] = {"cbody_id": cid, "name": name, "cbody_type": cbody_type, "quad": quad, "ring": ring}
        name_and_id = None
        cbody_type = None
        quad = None

    return SystemCbodiesData(cbodies=list(values.values()))


def _parse_int(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_name_id(s: str) -> tuple[int, str] | None:
    # "Name (123)" -> (123, "Name")
    if "(" not in s or ")" not in s:
        return None
    name = s.split("(", 1)[0].strip()
    id_part = s.split("(", 1)[1].split(")", 1)[0].strip()
    try:
        return int(id_part), name
    except ValueError:
        return None


def _parse_size_type(size_str: str) -> str | None:
    if not size_str:
        return None
    parts = [p for p in size_str.split(" ") if p]
    if len(parts) < 2:
        return None
    return " ".join(parts[1:]).strip() or None


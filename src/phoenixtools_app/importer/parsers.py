from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree
from lxml import html as lxml_html


@dataclass(frozen=True)
class ParsedPositionLocation:
    """Values extracted from Nexus `pos_list` / `loc_text` (Rails `parse_location!`)."""

    star_system_id: int | None
    cbody_game_id: int | None
    docked_base_id: int | None

    @property
    def has_data(self) -> bool:
        return self.star_system_id is not None or self.docked_base_id is not None


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
        loc_nodes = pos.xpath(".//loc_text")
        loc_text = None
        if loc_nodes:
            loc_text = "".join(loc_nodes[0].itertext()).strip() or None
        out.append(
            {
                "id": pid,
                "name": (pos.get("name") or "").strip() or None,
                "position_class": (pos.xpath("string(.//class)") or "").strip() or None,
                "design": (pos.xpath("string(.//design)") or "").strip() or None,
                "size": _parse_int((pos.xpath("string(.//size)") or "").strip().split(" ")[0]),
                "size_type": _parse_size_type((pos.xpath("string(.//size)") or "").strip()),
                "loc_text": loc_text,
            }
        )
    return PositionData(positions=out)


def parse_pos_types(xml_text: str) -> dict[int, str]:
    """Map position id -> Nexus position `type` (Starbase, Outpost, Ship, Platform, Political, …)."""
    root = etree.fromstring(xml_text.encode("utf-8", errors="ignore"))
    out: dict[int, str] = {}
    for pos in root.xpath("//positions/position"):
        num = pos.get("num")
        ptype = (pos.get("type") or "").strip()
        if not num or not ptype:
            continue
        try:
            out[int(num)] = ptype
        except ValueError:
            continue
    return out


def parse_position_loc_text(loc_str: str | None) -> ParsedPositionLocation:
    """
    Mirror Rails `NexusXMLClient#parse_location!` enough for Base star_system / cbody assignment.
    """
    if not loc_str or not loc_str.strip():
        return ParsedPositionLocation(None, None, None)
    parts = [p.strip() for p in loc_str.split(" - ")]
    loc = ""
    sys_name: str | None = None
    if len(parts) > 2:
        loc = parts[0]
        sys_name = parts[2]
    elif len(parts) > 1:
        loc = parts[0]
        sys_name = parts[1]
    else:
        loc = parts[0]

    star_system_id: int | None = None
    if sys_name and "(" in sys_name and ")" in sys_name:
        try:
            star_system_id = int(sys_name.split("(", 1)[1].split(")", 1)[0].strip())
        except ValueError:
            star_system_id = None

    cbody_game_id: int | None = None
    docked_base_id: int | None = None
    if loc:
        if "Docked" in loc:
            docked_base_id = _first_paren_int(loc)
        elif "Landed" in loc or "Orbit" in loc:
            cbody_game_id = _first_paren_int(loc)

    return ParsedPositionLocation(
        star_system_id=star_system_id,
        cbody_game_id=cbody_game_id,
        docked_base_id=docked_base_id,
    )


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


def _parse_item_name_id(item_str: str) -> tuple[int, str] | None:
    if "(" not in item_str or ")" not in item_str:
        return None
    name = item_str.split("(", 1)[0].strip()
    id_part = item_str.split("(", 1)[1].split(")", 1)[0].strip()
    try:
        return int(id_part), name
    except ValueError:
        return None


def _parse_html_table_rows(table_node) -> list[list[str]]:
    rows: list[list[str]] = []
    if table_node is None:
        return rows
    for tr in table_node.xpath(".//tr"):
        cols = []
        for td in tr.xpath("./td"):
            txt = (td.text_content() or "").strip()
            if txt:
                cols.append(txt)
        if cols:
            rows.append(cols)
    return rows


def _report_sections_from_doc(doc) -> dict[str, list[list[str]]]:
    """Collect table rows following each `td.report_left` heading (Rails NexusTurn-style layout)."""
    out: dict[str, list[list[str]]] = {}
    for cell in doc.xpath('//td[contains(@class,"report_left")]'):
        title = (cell.text_content() or "").strip()
        if not title:
            continue
        tr = cell.xpath("ancestor::tr[1]")
        if not tr:
            continue
        tr = tr[0]
        block: list[list[str]] = []
        for sib in tr.itersiblings():
            if sib.tag == "table":
                block.extend(_parse_html_table_rows(sib))
            for tbl in sib.xpath(".//table"):
                block.extend(_parse_html_table_rows(tbl))
        out[title] = block
    return out


def _parse_mass_production_rows(rows: list[list[str]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows[1:]:
        if len(row) < 4:
            continue
        low0 = row[0].lower()
        if low0.startswith("basic") or low0 == "item":
            continue
        parsed = _parse_item_name_id(row[0])
        if parsed is None:
            continue
        item_id, _nm = parsed
        try:
            factories = int(row[1])
            carry = int(row[2])
        except (ValueError, IndexError):
            continue
        status = row[3] if len(row) > 3 else ""
        out.append({"item_id": item_id, "factories": factories, "carry": carry, "status": status})
    return out


def _read_resource_row_nexus(row: list[str]) -> tuple[int, dict[str, object]] | None:
    if len(row) < 5:
        return None
    parsed = _parse_item_name_id(row[0])
    if parsed is None:
        return None
    item_id, item_name = parsed
    try:
        resource_id = int(row[1])
        resource_yield = float(row[2])
        resource_drop = int(row[3])
    except (ValueError, IndexError):
        return None
    rs = row[4]
    resource_size = -999
    if rs and "infinite" not in rs.lower():
        try:
            resource_size = int(rs)
        except ValueError:
            resource_size = -999
    return resource_id, {
        "item_id": item_id,
        "item_name": item_name,
        "resource_id": resource_id,
        "resource_yield": resource_yield,
        "resource_drop": resource_drop,
        "resource_size": resource_size,
        "ore_mines": 0,
        "resource_complexes": 0,
        "output": None,
    }


def _merge_base_resources_from_sections(sections: dict[str, list[list[str]]]) -> list[dict[str, object]]:
    """Port of Rails `NexusTurn#resources` merge (mineral + mining + resource + extraction)."""
    mining: dict[int, dict[str, object]] = {}
    mrs = sections.get("Mineral Report") or []
    for row in mrs[1:]:
        got = _read_resource_row_nexus(row)
        if got is None:
            continue
        rid, res = got
        mining[rid] = res

    mrs2 = sections.get("Mining Report") or []
    for row in mrs2[1:]:
        if len(row) < 5:
            continue
        try:
            rid = int(row[3])
        except (ValueError, IndexError):
            continue
        resource = mining.get(rid)
        if resource is None:
            continue
        try:
            resource["ore_mines"] = int(row[0])
            resource["output"] = float(row[4])
        except (ValueError, IndexError):
            pass

    resourcing: dict[int, dict[str, object]] = {}
    mrs3 = sections.get("Resource Report") or []
    for row in mrs3[1:]:
        got = _read_resource_row_nexus(row)
        if got is None:
            continue
        rid, res = got
        resourcing[rid] = res

    mrs4 = sections.get("Resource Extraction Report") or []
    for row in mrs4[1:]:
        if len(row) < 5:
            continue
        try:
            rid = int(row[2])
        except (ValueError, IndexError):
            continue
        resource = resourcing.get(rid)
        if resource is None:
            continue
        try:
            resource["resource_complexes"] = int(row[0])
            resource["output"] = float(row[4])
        except (ValueError, IndexError):
            pass

    # Rails appended both lists, duplicating deposits present in both the Mineral
    # and Resource reports; merge them by resource id instead.
    merged: dict[int, dict[str, object]] = dict(mining)
    for rid, res in resourcing.items():
        cur = merged.get(rid)
        if cur is None:
            merged[rid] = res
            continue
        cur["resource_complexes"] = res.get("resource_complexes", 0)
        out_a = cur.get("output")
        out_b = res.get("output")
        if out_a is None:
            cur["output"] = out_b
        elif out_b is not None:
            # both sources produce: total weekly output of the deposit
            cur["output"] = float(out_a) + float(out_b)
    return list(merged.values())


@dataclass(frozen=True)
class TurnData:
    inventory: dict[int, int]
    trade_items: dict[int, int]
    raw_materials: dict[int, int]
    item_groups: dict[int, dict[str, object]]  # {group_id: {"name": str, "items": {item_id: qty}}}
    mass_production: list[dict[str, object]] = field(default_factory=list)
    base_resources: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class TurnListEntry:
    """One turn row from the Nexus (personal list and/or Find → External)."""

    pos_id: int
    name: str
    token: str | None = None  # present on personal list; resolved at import for external-only
    owned: bool = False  # True = your own turn on the personal list
    owner_name: str = ""
    owner_id: int = 0
    tus: int = 0
    position_type: str | None = None  # Starbase / Outpost / Ship / Platform / Political
    source: str = "personal"  # personal | external
    system_name: str | None = None
    system_id: int | None = None
    last_access: str | None = None

    BASE_TYPES = ("Starbase", "Outpost")

    @property
    def is_base(self) -> bool:
        if not self.position_type:
            return self.source == "external"
        return self.position_type in self.BASE_TYPES

    @property
    def on_personal_list(self) -> bool:
        return self.source == "personal" or self.token is not None


# ss_set_turn("<hash>","t_N",<pos>,<tus>,<owned bool>,"<owner>",<owner_id>)
_SS_SET_TURN_RE = re.compile(
    r'ss_set_turn\(\s*"([0-9a-fA-F]+)"\s*,\s*"[^"]*"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(true|false)\s*,\s*"([^"]*)"\s*,\s*(\d+)'
)
_NAME_ID_TAIL_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")
_PAREN_ID_RE = re.compile(r"\(\s*(\d{1,7})\s*\)")


def parse_turn_list(html_text: str) -> list[TurnListEntry]:
    """Parse the logged-in turns list page into entries (own + shared turns)."""
    doc = lxml_html.fromstring(html_text)
    out: list[TurnListEntry] = []
    seen: set[int] = set()
    for a in doc.xpath('//a[contains(@onclick,"ss_set_turn")]'):
        onclick = a.get("onclick") or ""
        m = _SS_SET_TURN_RE.search(onclick)
        if not m:
            continue
        token, pos, tus, owned, owner, owner_id = m.groups()
        pid = int(pos)
        if pid in seen:
            continue
        seen.add(pid)
        text = (a.text_content() or "").strip()
        name = text
        nm = _NAME_ID_TAIL_RE.match(text)
        if nm:
            name = nm.group(1).strip()
        out.append(
            TurnListEntry(
                pos_id=pid,
                name=name or f"Turn {pid}",
                token=token,
                owned=(owned == "true"),
                owner_name=owner,
                owner_id=int(owner_id),
                tus=int(tus),
                source="personal",
            )
        )
    return out


_FIND_LINK_RE = re.compile(r"\?a=turns&sa=list&la=find&id=(\d+)")
_SYSTEM_HEAD_RE = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")


def parse_external_turns_find(html_text: str) -> list[TurnListEntry]:
    """
    Parse Turns → Find → External (`a=turns&sa=find`): affiliation turns grouped by system.
    These do not need to be on your personal turns list to import.
    """
    doc = lxml_html.fromstring(html_text)
    out: list[TurnListEntry] = []
    seen: set[int] = set()
    system_name: str | None = None
    system_id: int | None = None

    for el in doc.xpath("//div[contains(@class,'t_element')]"):
        head = el.xpath(".//div[contains(@class,'t_c_n')]")
        if head:
            hm = _SYSTEM_HEAD_RE.match((head[0].text_content() or "").strip())
            if hm:
                system_name = hm.group(1).strip()
                system_id = int(hm.group(2))

        for row in el.xpath(".//div[contains(@class,'t_d_n2')]"):
            link = row.xpath(".//a[contains(@href,'la=find&id=')]")
            if not link:
                continue
            href = link[0].get("href") or ""
            lm = _FIND_LINK_RE.search(href)
            if not lm:
                continue
            pid = int(lm.group(1))
            if pid in seen:
                continue
            seen.add(pid)

            raw_name = (link[0].text_content() or "").strip()
            name = raw_name
            nm = _NAME_ID_TAIL_RE.match(raw_name)
            if nm:
                name = nm.group(1).strip()
            # Strip leading affiliation tag (e.g. "BHD ").
            name = re.sub(r"^[A-Z]{2,5}\s+", "", name).strip() or name

            pos_type: str | None = None
            last_access: str | None = None
            for fr in row.xpath(".//div[contains(@class,'fr')]"):
                txt = (fr.text_content() or "").strip()
                if not txt:
                    continue
                if re.fullmatch(r"\d{2}/\d{2}/\d{2}", txt):
                    last_access = txt
                elif txt not in ("Positions",) and not txt.endswith(" Positions"):
                    pos_type = txt

            out.append(
                TurnListEntry(
                    pos_id=pid,
                    name=name or f"Turn {pid}",
                    owned=False,
                    owner_name="External",
                    position_type=pos_type,
                    source="external",
                    system_name=system_name,
                    system_id=system_id,
                    last_access=last_access,
                )
            )
    return out


def merge_turn_catalog(
    personal: list[TurnListEntry], external: list[TurnListEntry]
) -> list[TurnListEntry]:
    """Merge personal turns list with Find → External; personal metadata wins on overlap."""
    by_id: dict[int, TurnListEntry] = {e.pos_id: e for e in external}
    for pe in personal:
        ex = by_id.get(pe.pos_id)
        if ex is None:
            by_id[pe.pos_id] = pe
            continue
        by_id[pe.pos_id] = TurnListEntry(
            pos_id=pe.pos_id,
            name=pe.name or ex.name,
            token=pe.token,
            owned=pe.owned,
            owner_name=pe.owner_name or ex.owner_name,
            owner_id=pe.owner_id,
            tus=pe.tus,
            position_type=pe.position_type or ex.position_type,
            source="personal",
            system_name=ex.system_name,
            system_id=ex.system_id,
            last_access=ex.last_access,
        )
    return sorted(by_id.values(), key=lambda e: ((e.system_name or "").lower(), e.name.lower(), e.pos_id))


def parse_turn_position_type(html_text: str, pos_id: int) -> str | None:
    """
    From a turn report header like 'BHD OUTPOST Chapel of Primus/01-I (98287)', return the
    title-cased position type ('Outpost', 'Starbase', 'Ship', 'Platform', 'Political', …).

    Matches on the report's own position id so it isn't fooled by trade-log lines that also
    begin with '<AFF> <TYPE> <ship name> (<other id>)'.
    """
    html_text = _unwrap_possible_xml_to_html(html_text)
    head = html_text.lstrip()[:200].lower()
    if head.startswith("<?xml") and "encoding" in head:
        doc = lxml_html.fromstring(html_text.encode("utf-8", errors="ignore"))
    else:
        doc = lxml_html.fromstring(html_text)
    text = doc.text_content() or ""
    m = re.search(
        r"\b([A-Z]{2,5})\s+([A-Z]+)\s+[^()\n]*?\(\s*" + re.escape(str(int(pos_id))) + r"\s*\)",
        text,
    )
    if m:
        return m.group(2).title()
    return None


def parse_turn_location_ids(html_text: str) -> list[int]:
    """
    Best-effort: extract the parenthesised ids from a turn report's 'Starbase Location:' value
    (e.g. cbody + system). The caller resolves which id is the star system / celestial body.
    """
    html_text = _unwrap_possible_xml_to_html(html_text)
    head = html_text.lstrip()[:200].lower()
    if head.startswith("<?xml") and "encoding" in head:
        doc = lxml_html.fromstring(html_text.encode("utf-8", errors="ignore"))
    else:
        doc = lxml_html.fromstring(html_text)
    for n in doc.xpath('//td[contains(text(),"Location")]'):
        label = (n.text_content() or "").strip()
        if "Location" not in label:
            continue
        chunk = label
        sib = n.getnext()
        steps = 0
        while sib is not None and steps < 3:
            chunk += " " + (sib.text_content() or "")
            sib = sib.getnext()
            steps += 1
        ids = [int(x) for x in _PAREN_ID_RE.findall(chunk)]
        if ids:
            return ids
    return []


def _unwrap_possible_xml_to_html(payload: str) -> str:
    """
    Nexus `sa=turn_data&tid=...` sometimes returns a full HTML document, and sometimes
    returns an XML wrapper containing the HTML as text/CDATA. This extracts the HTML
    in a best-effort way.
    """
    s = payload.lstrip()
    if not s.startswith("<?xml"):
        return payload

    # Parse XML as bytes (lxml disallows unicode with encoding declaration).
    try:
        root = etree.fromstring(payload.encode("utf-8", errors="ignore"))
    except Exception:
        return payload

    # Prefer any text node that looks like HTML.
    texts: list[str] = []
    try:
        for t in root.xpath("//text()"):
            if t is None:
                continue
            tt = str(t)
            if "<html" in tt.lower() or "<td" in tt.lower() or "<table" in tt.lower():
                texts.append(tt)
    except Exception:
        texts = []

    if texts:
        return max(texts, key=len)

    # Fallback to full string value.
    try:
        return str(root.xpath("string(.)"))
    except Exception:
        return payload


def parse_turn_html(html_text: str) -> TurnData:
    """
    Partial port of Rails `NexusTurn`:
    - Parses "Inventory Report" into item_id -> quantity
    - Parses "Trade Item Report" / "Raw Material Report" (Rails BaseItem categories)
    - Parses "Item Group: NAME (ID)" sections into grouped items
    """
    html_text = _unwrap_possible_xml_to_html(html_text)

    # lxml does not allow unicode strings with an XML encoding declaration.
    head = html_text.lstrip()[:200].lower()
    if head.startswith("<?xml") and "encoding" in head:
        doc = lxml_html.fromstring(html_text.encode("utf-8", errors="ignore"))
    else:
        doc = lxml_html.fromstring(html_text)

    def parse_table_rows(table_node) -> list[list[str]]:
        rows: list[list[str]] = []
        if table_node is None:
            return rows
        for tr in table_node.xpath(".//tr"):
            cols = []
            for td in tr.xpath("./td"):
                txt = (td.text_content() or "").strip()
                if txt:
                    cols.append(txt)
            if cols:
                rows.append(cols)
        return rows

    def find_section_table(heading: str):
        # Find <td class="report_left">Heading</td>, then use a heuristic:
        # the next table-containing sibling in the report layout.
        for n in doc.xpath('//td[contains(@class,"report_left")]'):
            if (n.text_content() or "").strip() != heading:
                continue
            cur = n.getparent()
            for _ in range(10):
                if cur is None:
                    break
                cur = cur.getnext()
                if cur is None:
                    break
                tables = cur.xpath(".//table")
                if tables:
                    return tables[0]
        return None

    def parse_qty_name_table(heading: str) -> dict[int, int]:
        out: dict[int, int] = {}
        tbl = find_section_table(heading)
        for row in parse_table_rows(tbl)[1:]:
            if len(row) < 2:
                continue
            qty = _parse_int(row[0])
            parsed = _parse_item_name_id(row[1])
            if qty is None or parsed is None:
                continue
            item_id, _name = parsed
            out[item_id] = out.get(item_id, 0) + int(qty)
        return out

    inventory = parse_qty_name_table("Inventory Report")
    trade_items = parse_qty_name_table("Trade Item Report")
    raw_materials = parse_qty_name_table("Raw Material Report")

    item_groups: dict[int, dict[str, object]] = {}
    for n in doc.xpath('//td[contains(@class,"report_left")]'):
        heading = (n.text_content() or "").strip()
        if "Item Group" not in heading:
            continue
        # "Item Group: NAME (123)"
        if ":" not in heading or "(" not in heading or ")" not in heading:
            continue
        after = heading.split(":", 1)[1].strip()
        name = after.split("(", 1)[0].strip()
        id_part = after.split("(", 1)[1].split(")", 1)[0].strip()
        try:
            group_id = int(id_part)
        except ValueError:
            continue
        table = None
        cur = n.getparent()
        for _ in range(10):
            if cur is None:
                break
            cur = cur.getnext()
            if cur is None:
                break
            tables = cur.xpath(".//table")
            if tables:
                table = tables[0]
                break
        rows = parse_table_rows(table)
        items: dict[int, int] = {}
        for r in rows[1:]:
            if len(r) < 2:
                continue
            qty = _parse_int(r[0])
            parsed = _parse_item_name_id(r[1])
            if qty is None or parsed is None:
                continue
            item_id, _nm = parsed
            items[item_id] = items.get(item_id, 0) + int(qty)
        item_groups[group_id] = {"name": name, "items": items}

    sections = _report_sections_from_doc(doc)
    mass_production = _parse_mass_production_rows(sections.get("Production Report") or [])
    base_resources = _merge_base_resources_from_sections(sections)

    return TurnData(
        inventory=inventory,
        trade_items=trade_items,
        raw_materials=raw_materials,
        item_groups=item_groups,
        mass_production=mass_production,
        base_resources=base_resources,
    )


def parse_item_attributes_html(html_text: str) -> dict[str, str]:
    """
    Rails `Item#fetch_item_attributes!`: `td.data_field` cells alternate key / value.
    Returns {} when the page has no data (e.g. session expired -> caller should re-login).
    """
    doc = lxml_html.fromstring(html_text)
    values: dict[str, str] = {}
    key: str | None = None
    for n in doc.xpath('//td[@class="data_field"]'):
        text = (n.text_content() or "").strip()
        if key is None:
            key = text
        else:
            values[key] = text
            key = None
    return values


def _first_paren_int(s: str) -> int | None:
    if "(" not in s or ")" not in s:
        return None
    try:
        return int(s.split("(", 1)[1].split(")", 1)[0].strip())
    except ValueError:
        return None


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


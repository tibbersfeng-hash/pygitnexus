"""Parse MyBatis mapper XML files to extract method-to-entity relationships."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ResultProperty:
    """A field mapping in a resultMap."""
    column: str
    property: str  # Java property name (matches getter/setter)
    jdbc_type: str = ""
    is_id: bool = False  # True for <id>, False for <result>


@dataclass
class ResultMapInfo:
    """A <resultMap> definition."""
    id: str
    entity_fqn: str  # Fully qualified entity class name, e.g. "ltd.newbee.mall.entity.MallUser"
    properties: list[ResultProperty] = field(default_factory=list)


@dataclass
class SqlStatement:
    """A <select>, <insert>, <update>, or <delete> element."""
    id: str  # method name
    stmt_type: str  # "select", "insert", "update", "delete"
    result_map: str = ""  # resultMap id reference
    parameter_type: str = ""  # parameter type (FQN or simple)
    sql: str = ""  # raw SQL (trimmed)
    referenced_properties: list[str] = field(default_factory=list)  # #{property} references


@dataclass
class MapperInfo:
    """Parsed MyBatis mapper XML."""
    namespace: str  # FQN of Mapper interface, e.g. "ltd.newbee.mall.dao.MallUserMapper"
    result_maps: list[ResultMapInfo] = field(default_factory=list)
    statements: list[SqlStatement] = field(default_factory=list)
    source_file: str = ""  # relative path of XML file


def parse_mapper_xml(file_path: str | Path) -> MapperInfo | None:
    """Parse a MyBatis mapper XML file.

    Returns MapperInfo or None if not a valid MyBatis mapper.
    """
    try:
        tree = ET.parse(str(file_path))
    except Exception:
        return None

    root = tree.getroot()
    if not root.tag.endswith("mapper"):
        return None

    # Extract namespace
    namespace = root.get("namespace", "")
    if not namespace:
        return None

    info = MapperInfo(namespace=namespace, source_file=str(file_path))

    # Parse resultMap definitions
    for rm_elem in root.findall("resultMap"):
        rm_id = rm_elem.get("id", "")
        rm_type = rm_elem.get("type", "")
        if not rm_type:
            continue

        props: list[ResultProperty] = []
        for prop_elem in rm_elem.findall("result"):
            props.append(ResultProperty(
                column=prop_elem.get("column", ""),
                property=prop_elem.get("property", ""),
                jdbc_type=prop_elem.get("jdbcType", ""),
                is_id=False,
            ))
        for prop_elem in rm_elem.findall("id"):
            props.append(ResultProperty(
                column=prop_elem.get("column", ""),
                property=prop_elem.get("property", ""),
                jdbc_type=prop_elem.get("jdbcType", ""),
                is_id=True,
            ))
        # Handle association/nested resultMaps (just capture type for now)
        for prop_elem in rm_elem.findall("association"):
            assoc_type = prop_elem.get("javaType", "") or prop_elem.get("resultMap", "")
            if assoc_type:
                props.append(ResultProperty(
                    column=prop_elem.get("property", ""),
                    property=prop_elem.get("property", ""),
                    jdbc_type="",
                    is_id=False,
                ))

        info.result_maps.append(ResultMapInfo(
            id=rm_id,
            entity_fqn=rm_type,
            properties=props,
        ))

    # Parse SQL statements
    for tag in ("select", "insert", "update", "delete"):
        for stmt_elem in root.findall(tag):
            stmt_id = stmt_elem.get("id", "")
            if not stmt_id:
                continue

            # Collect all text content (SQL)
            sql_parts = [stmt_elem.text or ""]
            for child in stmt_elem:
                if child.tail:
                    sql_parts.append(child.tail)
            sql = " ".join(sql_parts).strip()

            # Extract #{property} references
            import re
            prop_refs = sorted(set(re.findall(r'#\{(\w+)', sql)))

            info.statements.append(SqlStatement(
                id=stmt_id,
                stmt_type=tag,
                result_map=stmt_elem.get("resultMap", ""),
                parameter_type=stmt_elem.get("parameterType", ""),
                sql=sql,
                referenced_properties=prop_refs,
            ))

    return info

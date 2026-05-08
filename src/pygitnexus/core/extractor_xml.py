"""XML file parser — produces a minimal ParsedFile.

XML files don't contain Java methods/classes, so this returns an empty
ParsedFile. MyBatis mapper XMLs are handled separately in
_write_mybatis_relations() which calls parse_mapper_xml().
"""

from __future__ import annotations

from ..core.models import ParsedFile


def parse(file_path: str, content: bytes) -> ParsedFile:
    """Parse an XML file and return an empty ParsedFile.

    All XML files (MyBatis, Spring, Maven, etc.) are treated the same
    here — they produce a File node with no methods or classes.
    MyBatis-specific processing (MAPS_TO edges) happens downstream
    in _write_mybatis_relations().
    """
    return ParsedFile(
        file_path=file_path,
        classes=[],
        methods=[],
        constructors=[],
        fields=[],
        variables=[],
        annotations=[],
        calls=[],
        field_accesses=[],
        imports=[],
        type_aliases=[],
        enums=[],
        exports=[],
    )

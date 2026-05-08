"""Test XML parsing: MyBatis mapper detection + non-MyBatis XML filtering.

Covers three XML types:
1. MyBatis mapper XML  → parse_mapper_xml returns MapperInfo
2. Spring config XML    → parse_mapper_xml returns None (not a mapper)
3. Maven pom.xml        → parse_mapper_xml returns None (not a mapper)

Also tests the XML extractor (extractor_xml.py) which produces
an empty ParsedFile for all XML files, preventing "Unknown language: xml" errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pygitnexus.core.mybatis_parser import parse_mapper_xml
from pygitnexus.core.extractor_xml import parse as parse_xml

FIXTURES = Path(__file__).parent / "fixtures" / "xml"


class TestMyBatisMapperXml:
    """Valid MyBatis mapper XML should be fully parsed."""

    def test_parse_mapper_returns_info(self):
        """UserMapper.xml should return a MapperInfo object."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        assert info is not None
        assert info.namespace == "com.example.demo.mapper.UserMapper"

    def test_result_maps(self):
        """Should extract resultMap definitions."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        assert len(info.result_maps) == 1
        rm = info.result_maps[0]
        assert rm.id == "userMap"
        assert rm.entity_fqn == "com.example.demo.entity.User"
        prop_names = [p.property for p in rm.properties]
        assert "username" in prop_names
        assert "email" in prop_names
        assert "status" in prop_names

    def test_sql_statements(self):
        """Should extract all SQL statements."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        stmt_ids = [s.id for s in info.statements]
        assert "selectById" in stmt_ids
        assert "selectByUsername" in stmt_ids
        assert "insert" in stmt_ids
        assert "updateStatus" in stmt_ids
        assert "deleteById" in stmt_ids

    def test_statement_types(self):
        """Statement types should match the XML element tags."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        type_map = {s.id: s.stmt_type for s in info.statements}
        assert type_map["selectById"] == "select"
        assert type_map["insert"] == "insert"
        assert type_map["updateStatus"] == "update"
        assert type_map["deleteById"] == "delete"

    def test_parameter_type(self):
        """Should extract parameterType."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        select = next(s for s in info.statements if s.id == "selectById")
        assert select.parameter_type == "java.lang.Long"

    def test_result_map_reference(self):
        """Should extract resultMap reference on statements."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        select = next(s for s in info.statements if s.id == "selectById")
        assert select.result_map == "userMap"

    def test_sql_content(self):
        """Should extract SQL content."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        select = next(s for s in info.statements if s.id == "selectById")
        assert "SELECT" in select.sql.upper()
        assert "t_user" in select.sql

    def test_referenced_properties(self):
        """Should extract #{property} references from SQL."""
        info = parse_mapper_xml(FIXTURES / "UserMapper.xml")
        select = next(s for s in info.statements if s.id == "selectById")
        assert "id" in select.referenced_properties


class TestSpringConfigXml:
    """Spring applicationContext.xml should NOT be treated as a MyBatis mapper."""

    def test_not_a_mapper(self):
        """Root tag is <beans>, not <mapper> — should return None."""
        info = parse_mapper_xml(FIXTURES / "applicationContext.xml")
        assert info is None

    def test_xml_parser_still_produces_parsed_file(self):
        """The XML extractor should still produce a ParsedFile (empty)."""
        content = (FIXTURES / "applicationContext.xml").read_bytes()
        pf = parse_xml("applicationContext.xml", content)
        assert pf.file_path == "applicationContext.xml"
        assert pf.classes == []
        assert pf.methods == []
        assert pf.calls == []


class TestMavenPomXml:
    """Maven pom.xml should NOT be treated as a MyBatis mapper."""

    def test_not_a_mapper(self):
        """Root tag is <project>, not <mapper> — should return None."""
        info = parse_mapper_xml(FIXTURES / "pom.xml")
        assert info is None

    def test_xml_parser_still_produces_parsed_file(self):
        """The XML extractor should still produce a ParsedFile (empty)."""
        content = (FIXTURES / "pom.xml").read_bytes()
        pf = parse_xml("pom.xml", content)
        assert pf.file_path == "pom.xml"
        assert pf.classes == []
        assert pf.methods == []


class TestXmlExtractor:
    """Test extractor_xml.py produces empty ParsedFile for any XML."""

    def test_empty_xml(self):
        """Empty XML still produces a valid ParsedFile."""
        pf = parse_xml("empty.xml", b"<?xml version='1.0'?><root/>")
        assert pf.file_path == "empty.xml"
        assert pf.methods == []
        assert pf.classes == []

    def test_malformed_xml(self):
        """Malformed XML should not raise."""
        pf = parse_xml("broken.xml", b"<broken><not-closed>")
        assert pf.file_path == "broken.xml"
        assert pf.methods == []

    def test_non_xml_content(self):
        """Non-XML content should not raise."""
        pf = parse_xml("fake.xml", b"this is not xml at all")
        assert pf.file_path == "fake.xml"
        assert pf.methods == []

    def test_large_xml(self):
        """Large XML should parse without issues."""
        lines = ["<?xml version='1.0'?><root>"]
        for i in range(1000):
            lines.append(f"<item id='{i}'>text {i}</item>")
        lines.append("</root>")
        content = "\n".join(lines).encode("utf-8")
        pf = parse_xml("large.xml", content)
        assert pf.file_path == "large.xml"
        assert pf.methods == []

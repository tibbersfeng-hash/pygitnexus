"""MyBatis integration tests: Controller → Service → Mapper → XML → Entity chain.

Tests three layers:
1. Java parsing (extractor.py) — verifies AST extraction of classes, methods, fields
2. MyBatis XML parsing (mybatis_parser.py) — verifies MapperInfo extraction
3. Full pipeline (pipeline.py + KuzuDB) — verifies CALLS, MAPS_TO, EXPOSES relations
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MYBATIS_FIXTURES = FIXTURES / "mybatis"
XML_FIXTURES = FIXTURES / "xml"


# =============================================================================
# Layer 1: Java Parsing Tests (no KuzuDB required)
# =============================================================================

class TestMybatisJavaParsing:
    """Verify the Java extractor correctly parses each MyBatis fixture."""

    def test_user_entity_has_fields(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "User.java").read_bytes()
        pf = parse_java("User.java", content)
        field_names = {f.name for f in pf.fields}
        assert field_names == {"id", "username", "email", "status"}

    def test_user_entity_has_getters_and_setters(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "User.java").read_bytes()
        pf = parse_java("User.java", content)
        method_names = {m.name for m in pf.methods}
        expected = {
            "getId", "setId",
            "getUsername", "setUsername",
            "getEmail", "setEmail",
            "getStatus", "setStatus",
        }
        assert expected.issubset(method_names)

    def test_user_entity_class_name(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "User.java").read_bytes()
        pf = parse_java("User.java", content)
        assert len(pf.classes) == 1
        assert pf.classes[0].name == "com.example.demo.entity.User"

    def test_user_mapper_is_interface(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserMapper.java").read_bytes()
        pf = parse_java("UserMapper.java", content)
        assert len(pf.classes) == 1
        assert pf.classes[0].is_interface is True
        assert pf.classes[0].name == "com.example.demo.mapper.UserMapper"

    def test_user_mapper_has_methods(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserMapper.java").read_bytes()
        pf = parse_java("UserMapper.java", content)
        method_names = {m.name for m in pf.methods}
        expected = {
            "selectById", "selectByUsername", "selectAll",
            "insert", "updateStatus", "deleteById",
        }
        assert expected == method_names

    def test_user_mapper_has_import(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserMapper.java").read_bytes()
        pf = parse_java("UserMapper.java", content)
        imports = {imp.qualified_name for imp in pf.imports}
        assert "com.example.demo.entity.User" in imports

    def test_user_service_has_mapper_field(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserService.java").read_bytes()
        pf = parse_java("UserService.java", content)
        field_names = {f.name for f in pf.fields}
        assert "userMapper" in field_names

    def test_user_service_calls_mapper_methods(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserService.java").read_bytes()
        pf = parse_java("UserService.java", content)
        call_targets = {c.target_name for c in pf.calls}
        expected = {"selectById", "selectByUsername", "selectAll",
                     "insert", "updateStatus", "deleteById"}
        assert expected.issubset(call_targets)

    def test_user_controller_has_annotations(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserController.java").read_bytes()
        pf = parse_java("UserController.java", content)
        ann_names = {a.name for a in pf.annotations}
        assert "GetMapping" in ann_names
        assert "PostMapping" in ann_names

    def test_user_controller_has_endpoint_paths(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserController.java").read_bytes()
        pf = parse_java("UserController.java", content)
        ann_paths = {
            a.attributes.get("path") or a.attributes.get("value")
            for a in pf.annotations
            if a.name in ("GetMapping", "PostMapping")
        }
        assert "/users/{id}" in ann_paths
        assert "/users" in ann_paths

    def test_user_controller_calls_service_methods(self):
        from pygitnexus.core.extractor import parse as parse_java
        content = (MYBATIS_FIXTURES / "UserController.java").read_bytes()
        pf = parse_java("UserController.java", content)
        call_targets = {c.target_name for c in pf.calls}
        expected = {"findById", "findAll", "createUser", "updateStatus", "deleteUser"}
        assert expected.issubset(call_targets)


# =============================================================================
# Layer 2: MyBatis XML Parsing Tests (no KuzuDB required)
# =============================================================================

class TestMybatisXmlParsing:
    """Verify the MyBatis XML parser extracts correct MapperInfo."""

    def test_namespace(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        assert info is not None
        assert info.namespace == "com.example.demo.mapper.UserMapper"

    def test_result_map_entity(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        assert len(info.result_maps) == 1
        rm = info.result_maps[0]
        assert rm.id == "userMap"
        assert rm.entity_fqn == "com.example.demo.entity.User"

    def test_result_map_properties(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        rm = info.result_maps[0]
        prop_names = {p.property for p in rm.properties}
        assert prop_names == {"id", "username", "email", "status"}
        # Verify id property is marked as is_id=True
        id_prop = next(p for p in rm.properties if p.property == "id")
        assert id_prop.is_id is True

    def test_all_statements_present(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        stmt_ids = {s.id for s in info.statements}
        expected = {
            "selectById", "selectByUsername", "selectAll",
            "insert", "updateStatus", "deleteById",
        }
        assert stmt_ids == expected

    def test_statement_types(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        type_map = {s.id: s.stmt_type for s in info.statements}
        assert type_map["selectById"] == "select"
        assert type_map["selectAll"] == "select"
        assert type_map["insert"] == "insert"
        assert type_map["updateStatus"] == "update"
        assert type_map["deleteById"] == "delete"

    def test_select_references_resultmap(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        select = next(s for s in info.statements if s.id == "selectById")
        assert select.result_map == "userMap"

    def test_insert_references_parameter_type(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        insert = next(s for s in info.statements if s.id == "insert")
        assert insert.parameter_type == "com.example.demo.entity.User"

    def test_referenced_properties_in_sql(self):
        from pygitnexus.core.mybatis_parser import parse_mapper_xml
        info = parse_mapper_xml(XML_FIXTURES / "UserMapper.xml")
        insert = next(s for s in info.statements if s.id == "insert")
        assert "username" in insert.referenced_properties
        assert "email" in insert.referenced_properties
        assert "status" in insert.referenced_properties


# =============================================================================
# Layer 3: Full Pipeline Integration Tests (KuzuDB required)
# =============================================================================

class TestMybatisFullPipeline:
    """Run the full pipeline with MyBatis fixtures and verify the graph."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temp directory with all fixture files arranged as a project."""
        tmp = Path(tempfile.mkdtemp())

        # Arrange files in a realistic directory structure:
        # src/main/java/com/example/demo/controller/UserController.java
        # src/main/java/com/example/demo/service/UserService.java
        # src/main/java/com/example/demo/mapper/UserMapper.java
        # src/main/java/com/example/demo/entity/User.java
        # src/main/resources/mapper/UserMapper.xml

        structure = {
            "src/main/java/com/example/demo/controller": ["UserController.java"],
            "src/main/java/com/example/demo/service": ["UserService.java"],
            "src/main/java/com/example/demo/mapper": ["UserMapper.java"],
            "src/main/java/com/example/demo/entity": ["User.java"],
            "src/main/resources/mapper": [],
        }
        for rel_dir, files in structure.items():
            (tmp / rel_dir).mkdir(parents=True, exist_ok=True)
            for f in files:
                src = MYBATIS_FIXTURES / f
                shutil.copy2(src, tmp / rel_dir / f)

        # Copy XML mapper
        shutil.copy2(
            XML_FIXTURES / "UserMapper.xml",
            tmp / "src/main/resources/mapper/UserMapper.xml",
        )

        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.fixture
    def analyzed_db(self, temp_repo):
        """Run the full pipeline and return the GraphStore."""
        pytest.importorskip("kuzu")
        from pygitnexus.core.pipeline import run_analysis
        from pygitnexus.graph.store import GraphStore

        db_path = temp_repo / "test.kuzu"
        run_analysis(temp_repo, db_path)

        store = GraphStore(db_path)
        yield store
        store.close()

    def test_controller_to_service_calls(self, analyzed_db):
        """UserController methods should CALL UserService methods."""
        results = analyzed_db.query("""
            MATCH (caller:Method)-[:CodeRelation {type: 'CALLS'}]->(target:Method)
            WHERE caller.className = 'UserController'
            RETURN caller.name AS caller, target.name AS target
        """)
        caller_target_pairs = {(r["caller"], r["target"]) for r in results}
        assert ("getUser", "findById") in caller_target_pairs
        assert ("getAllUsers", "findAll") in caller_target_pairs
        assert ("createUser", "createUser") in caller_target_pairs

    def test_service_to_mapper_calls(self, analyzed_db):
        """UserService methods should CALL UserMapper methods."""
        results = analyzed_db.query("""
            MATCH (caller:Method)-[:CodeRelation {type: 'CALLS'}]->(target:Method)
            WHERE caller.className = 'UserService'
            RETURN caller.name AS caller, target.name AS target
        """)
        caller_target_pairs = {(r["caller"], r["target"]) for r in results}
        assert ("findById", "selectById") in caller_target_pairs
        assert ("findAll", "selectAll") in caller_target_pairs
        assert ("createUser", "insert") in caller_target_pairs

    def test_maps_to_entity_class(self, analyzed_db):
        """Mapper methods should have MAPS_TO relations to the User entity class."""
        results = analyzed_db.query("""
            MATCH (m:Method)-[:CodeRelation {type: 'MAPS_TO'}]->(c:Class)
            WHERE m.className = 'UserMapper'
            RETURN m.name AS method, c.name AS entity
        """)
        assert len(results) >= 1
        entities = {r["entity"] for r in results}
        assert "com.example.demo.entity.User" in entities

    def test_maps_to_entity_fields(self, analyzed_db):
        """Mapper methods should have MAPS_TO relations to User entity fields."""
        results = analyzed_db.query("""
            MATCH (m:Method)-[:CodeRelation {type: 'MAPS_TO'}]->(f:Field)
            WHERE m.className = 'UserMapper'
            RETURN m.name AS method, f.name AS field
        """)
        assert len(results) >= 4  # At least id, username, email, status
        fields = {r["field"] for r in results}
        assert "id" in fields
        assert "username" in fields
        assert "email" in fields
        assert "status" in fields

    def test_maps_to_entity_setters(self, analyzed_db):
        """Mapper methods should have MAPS_TO relations to User entity setter methods."""
        results = analyzed_db.query("""
            MATCH (m:Method)-[:CodeRelation {type: 'MAPS_TO'}]->(s:Method)
            WHERE m.className = 'UserMapper' AND s.className = 'User'
            RETURN m.name AS mapper_method, s.name AS setter
        """)
        assert len(results) >= 4
        setters = {r["setter"] for r in results}
        assert "setId" in setters
        assert "setUsername" in setters
        assert "setEmail" in setters
        assert "setStatus" in setters

    def test_controller_has_exposes_api(self, analyzed_db):
        """Controller methods should EXPOSE API nodes."""
        results = analyzed_db.query("""
            MATCH (m:Method)-[:CodeRelation {type: 'EXPOSES'}]->(api:API)
            WHERE m.className = 'UserController'
            RETURN m.name AS method, api.httpMethod AS http_method, api.httpPath AS http_path
        """)
        assert len(results) >= 2
        paths = {r["http_path"] for r in results}
        assert "/users/{id}" in paths
        assert "/users" in paths

    def test_mapper_interface_exists(self, analyzed_db):
        """The UserMapper Interface node should exist in the graph."""
        results = analyzed_db.query("""
            MATCH (n:Interface) WHERE n.name = 'com.example.demo.mapper.UserMapper'
            RETURN n.name AS name
        """)
        assert len(results) == 1
        assert results[0]["name"] == "com.example.demo.mapper.UserMapper"

    def test_entity_class_exists(self, analyzed_db):
        """The User Class node should exist in the graph."""
        results = analyzed_db.query("""
            MATCH (n:Class) WHERE n.name = 'com.example.demo.entity.User'
            RETURN n.name AS name
        """)
        assert len(results) == 1
        assert results[0]["name"] == "com.example.demo.entity.User"

    def test_user_fields_exist(self, analyzed_db):
        """All four User entity fields should exist as Field nodes."""
        results = analyzed_db.query("""
            MATCH (f:Field) WHERE f.className = 'User'
            RETURN f.name AS name
        """)
        names = {r["name"] for r in results}
        assert "id" in names
        assert "username" in names
        assert "email" in names
        assert "status" in names

    def test_maps_to_confidence_values(self, analyzed_db):
        """MAPS_TO relations should have appropriate confidence values."""
        # MAPS_TO to class should be 0.95
        results = analyzed_db.query("""
            MATCH (m:Method)-[r:CodeRelation {type: 'MAPS_TO'}]->(c:Class)
            WHERE m.className = 'UserMapper'
            RETURN r.confidence AS confidence
        """)
        assert len(results) >= 1
        assert all(r["confidence"] == 0.95 for r in results)

        # MAPS_TO to fields/setters should be 0.9
        results = analyzed_db.query("""
            MATCH (m:Method)-[r:CodeRelation {type: 'MAPS_TO'}]->(f:Field)
            WHERE m.className = 'UserMapper'
            RETURN r.confidence AS confidence
        """)
        assert len(results) >= 4
        assert all(r["confidence"] == 0.9 for r in results)

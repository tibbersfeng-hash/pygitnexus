"""Unit tests for extractor.py — Java source file parsing.

Covers all parsing scenarios:
  - Basic: empty files, package-only, classes, interfaces, nested classes
  - Methods: params, return types, constructors, modifiers
  - Calls: simple, chained, static, this, nested class attribution
  - Lambda: single/multi param, typed, stream filter/map, comparator, method refs
  - Annotations: marker, with value, with attributes, class-level
  - Fields: extraction, static, local vars, field access via this, field writes
  - Imports
  - Edge cases: empty class, interface methods, generic classes, enhanced for
  - Builder pattern: chain type resolution
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pygitnexus.core.extractor import parse
from pygitnexus.core.models import ParsedFile


# ─── Helpers ────────────────────────────────────────────────────────

def _parse_content(content: str) -> ParsedFile:
    """Parse Java source code string and return ParsedFile."""
    encoded = content.encode("utf-8")
    with tempfile.NamedTemporaryFile(suffix=".java", delete=False) as f:
        f.write(encoded)
        f.flush()
        return parse(f.name, encoded)


def _call_targets(result: ParsedFile) -> list[str]:
    """Extract target method names from all call sites."""
    return [c.target_name for c in result.calls]


def _call_receivers(result: ParsedFile) -> list[str | None]:
    """Extract receiver names from all call sites."""
    return [c.receiver for c in result.calls]


def _call_receiver_types(result: ParsedFile) -> list[str | None]:
    """Extract resolved receiver types from all call sites."""
    return [c.receiver_type for c in result.calls]


# ─── 1. Basic Parsing ───────────────────────────────────────────────

class TestBasicParsing:

    def test_empty_file(self):
        """Empty file should parse without crashing, returning empty results."""
        result = _parse_content("")
        assert result.classes == []
        assert result.methods == []
        assert result.calls == []

    def test_package_only(self):
        """File with only package declaration should extract package but no symbols."""
        result = _parse_content("package com.example;\n")
        assert result.classes == []
        assert result.methods == []

    def test_single_class(self):
        """Simple class should extract name, FQN, line range."""
        src = """\
package com.example;
public class Foo {
    public void bar() {}
}
"""
        result = _parse_content(src)
        assert len(result.classes) == 1
        assert result.classes[0].name == "com.example.Foo"
        assert result.classes[0].is_public is True
        assert result.classes[0].is_interface is False
        assert result.classes[0].start_line == 2

    def test_interface(self):
        """Interface should be extracted with extends."""
        src = """\
package com.example;
public interface Foo extends Bar {
    void doSomething();
}
"""
        result = _parse_content(src)
        assert len(result.classes) == 1
        assert result.classes[0].is_interface is True
        assert result.classes[0].extends == "Bar"

    def test_abstract_class(self):
        """Abstract class modifier should be recognized."""
        src = """\
package com.example;
public abstract class BaseService {
    public abstract void execute();
}
"""
        result = _parse_content(src)
        assert len(result.classes) == 1
        assert result.classes[0].is_abstract is True

    def test_nested_class_fqn(self):
        """Nested class FQN should be Outer.Inner."""
        src = """\
package com.example;
public class Outer {
    public class Inner {
        public void doWork() {}
    }
}
"""
        result = _parse_content(src)
        class_names = {c.name for c in result.classes}
        assert "com.example.Outer" in class_names
        assert "com.example.Outer.Inner" in class_names


# ─── 2. Method Parsing ──────────────────────────────────────────────

class TestMethodParsing:

    def test_method_with_params(self):
        """Method parameters should be extracted with type and name."""
        src = """\
package com.example;
public class Foo {
    public void bar(String name, int count) {}
}
"""
        result = _parse_content(src)
        assert len(result.methods) == 1
        params = result.methods[0].parameters
        assert len(params) == 2
        assert params[0].name == "name"
        assert params[0].type_name == "String"
        assert params[1].name == "count"
        assert params[1].type_name == "int"

    def test_generic_return_type(self):
        """Generic return type should be stripped to base type."""
        src = """\
package com.example;
import java.util.List;
public class Foo {
    public List<User> getUsers() { return null; }
}
"""
        result = _parse_content(src)
        assert result.methods[0].return_type == "List"

    def test_array_return_type(self):
        """Array return type should be preserved with brackets."""
        src = """\
package com.example;
public class Foo {
    public String[] getNames() { return null; }
}
"""
        result = _parse_content(src)
        assert result.methods[0].return_type == "String[]"

    def test_void_return_type(self):
        """void return type should be recognized."""
        src = """\
package com.example;
public class Foo {
    public void doNothing() {}
}
"""
        result = _parse_content(src)
        assert result.methods[0].return_type == "void"

    def test_constructor(self):
        """Constructor should be extracted separately."""
        src = """\
package com.example;
public class Foo {
    public Foo(String name) { this.name = name; }
    private String name;
}
"""
        result = _parse_content(src)
        assert len(result.constructors) == 1
        assert result.constructors[0].name == "Foo"
        assert len(result.constructors[0].parameters) == 1

    def test_static_method(self):
        """Static modifier should be recognized."""
        src = """\
package com.example;
public class Foo {
    public static void helper() {}
}
"""
        result = _parse_content(src)
        assert result.methods[0].is_static is True


# ─── 3. Call Extraction ─────────────────────────────────────────────

class TestCallExtraction:

    def test_simple_method_call(self):
        """Simple method call: obj.method()"""
        src = """\
package com.example;
public class Foo {
    public void bar() {
        obj.doSomething();
    }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "doSomething" in targets

    def test_chained_call(self):
        """Chained call: obj.method1().method2()"""
        src = """\
package com.example;
public class Foo {
    public void bar() {
        obj.method1().method2();
    }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "method1" in targets
        assert "method2" in targets

    def test_static_call(self):
        """Static call: ClassName.staticMethod()"""
        src = """\
package com.example;
public class Foo {
    public void bar() {
        StringUtils.isEmpty(str);
    }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "isEmpty" in targets

    def test_this_call(self):
        """this call: this.method()"""
        src = """\
package com.example;
public class Foo {
    public void bar() {
        this.helper();
    }
    public void helper() {}
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "helper" in targets

    def test_call_in_nested_class(self):
        """Calls in nested class should be attributed to the nested class."""
        src = """\
package com.example;
public class Outer {
    public class Inner {
        public void doWork() {
            helper.process();
        }
    }
}
"""
        result = _parse_content(src)
        inner_calls = [c for c in result.calls if "Inner" in c.caller_class]
        assert len(inner_calls) >= 1
        assert inner_calls[0].target_name == "process"


# ─── 4. Lambda Expressions ──────────────────────────────────────────

class TestLambdaExpressions:

    def test_lambda_single_param(self):
        """Single parameter lambda without parens."""
        src = """\
package com.example;
import java.util.List;
public class Foo {
    public void bar(List<String> items) {
        items.forEach(x -> System.out.println(x));
    }
}
"""
        result = _parse_content(src)
        # Should not crash, and should find println call
        targets = _call_targets(result)
        assert "println" in targets

    def test_lambda_multi_param(self):
        """Multi parameter lambda with parens."""
        src = """\
package com.example;
import java.util.List;
public class Foo {
    public void bar(List<Integer> nums) {
        nums.sort((a, b) -> a.compareTo(b));
    }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "compareTo" in targets

    def test_typed_lambda_param(self):
        """Lambda with typed parameters should add to type map."""
        src = """\
package com.example;
public class Foo {
    public void bar() {
        java.util.function.Function<String, Integer> fn = (String x) -> x.length();
    }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "length" in targets

    def test_lambda_stream_filter(self):
        """Stream filter lambda — Strategy 1 should infer param type."""
        src = """\
package com.example;
import java.util.List;
public class Foo {
    public void bar(List<User> users) {
        users.stream().filter(u -> u.isActive()).forEach(System.out::println);
    }
}
class User {
    public boolean isActive() { return true; }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "isActive" in targets

    def test_lambda_stream_map(self):
        """Stream map lambda — Strategy 1 should infer param type."""
        src = """\
package com.example;
import java.util.List;
import java.util.stream.Collectors;
public class Foo {
    public List<String> bar(List<User> users) {
        return users.stream().map(u -> u.getName()).collect(Collectors.toList());
    }
}
class User {
    public String getName() { return ""; }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "getName" in targets

    def test_lambda_method_reference(self):
        """Method reference: Type::method"""
        src = """\
package com.example;
import java.util.List;
public class Foo {
    public void bar(List<String> items) {
        items.forEach(System.out::println);
    }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "println" in targets


# ─── 5. Annotations ─────────────────────────────────────────────────

class TestAnnotations:

    def test_marker_annotation(self):
        """Marker annotation (no arguments): @Override"""
        src = """\
package com.example;
public class Foo {
    @Override
    public String toString() { return ""; }
}
"""
        result = _parse_content(src)
        names = [a.name for a in result.annotations]
        assert "Override" in names

    def test_annotation_with_value(self):
        """Annotation with direct value: @GetMapping("/path")"""
        src = """\
package com.example;
public class FooController {
    @GetMapping("/users")
    public void getUsers() {}
}
"""
        result = _parse_content(src)
        ann = next((a for a in result.annotations if a.name == "GetMapping"), None)
        assert ann is not None
        assert ann.attributes.get("path") == "/users"

    def test_annotation_with_attributes(self):
        """Annotation with named attributes."""
        src = """\
package com.example;
public class FooController {
    @PostMapping(value = "/tasks", consumes = "application/json")
    public void createTask() {}
}
"""
        result = _parse_content(src)
        ann = next((a for a in result.annotations if a.name == "PostMapping"), None)
        assert ann is not None
        assert ann.attributes.get("value") == "/tasks"
        assert ann.attributes.get("consumes") == "application/json"

    def test_class_annotation(self):
        """Class-level annotation."""
        src = """\
package com.example;
@RestController
public class FooController {
}
"""
        result = _parse_content(src)
        names = [a.name for a in result.annotations]
        assert "RestController" in names


# ─── 6. Fields and Variables ────────────────────────────────────────

class TestFieldsAndVariables:

    def test_field_extraction(self):
        """Class fields should be extracted with type."""
        src = """\
package com.example;
public class Foo {
    private String name;
    private int count;
}
"""
        result = _parse_content(src)
        field_names = {f.name for f in result.fields}
        assert "name" in field_names
        assert "count" in field_names
        assert result.fields[0].type_name in ("String", "int")

    def test_static_field(self):
        """Static field modifier should be recognized."""
        src = """\
package com.example;
public class Foo {
    public static final int MAX_SIZE = 100;
}
"""
        result = _parse_content(src)
        assert len(result.fields) == 1
        assert result.fields[0].is_static is True

    def test_local_variable(self):
        """Local variable declarations should be extracted."""
        src = """\
package com.example;
public class Foo {
    public void bar() {
        String name = "test";
        int count = 0;
    }
}
"""
        result = _parse_content(src)
        var_names = {v.name for v in result.variables}
        assert "name" in var_names
        assert "count" in var_names

    def test_field_access_this(self):
        """this.field access should be detected."""
        src = """\
package com.example;
public class Foo {
    private String status;
    public void bar() {
        System.out.println(this.status);
    }
}
"""
        result = _parse_content(src)
        field_names = {f.field_name for f in result.field_accesses}
        assert "status" in field_names

    def test_field_write(self):
        """Field write access should be detected."""
        src = """\
package com.example;
public class Foo {
    private String status;
    public void bar() {
        this.status = "active";
    }
}
"""
        result = _parse_content(src)
        writes = [f for f in result.field_accesses if f.field_name == "status" and f.is_write]
        assert len(writes) >= 1


# ─── 7. Import Extraction ───────────────────────────────────────────

class TestImportExtraction:

    def test_import_extraction(self):
        """Import statements should be extracted."""
        src = """\
package com.example;
import java.util.List;
import java.util.ArrayList;
import com.google.common.collect.ImmutableList;
public class Foo {
}
"""
        result = _parse_content(src)
        import_names = {i.qualified_name for i in result.imports}
        assert "java.util.List" in import_names
        assert "java.util.ArrayList" in import_names
        assert "com.google.common.collect.ImmutableList" in import_names


# ─── 8. Edge Cases ──────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_class(self):
        """Empty class (no methods, no fields) should parse without errors."""
        src = """\
package com.example;
public class EmptyClass {
}
"""
        result = _parse_content(src)
        assert len(result.classes) == 1
        assert result.methods == []
        assert result.fields == []

    def test_interface_method_no_body(self):
        """Interface method (no body) should be extracted."""
        src = """\
package com.example;
public interface Foo {
    void doSomething();
    int calculate(int x, int y);
}
"""
        result = _parse_content(src)
        method_names = {m.name for m in result.methods}
        assert "doSomething" in method_names
        assert "calculate" in method_names

    def test_generic_class(self):
        """Generic class definition should parse."""
        src = """\
package com.example;
public class Container<T> {
    private T value;
    public T getValue() { return value; }
}
"""
        result = _parse_content(src)
        assert len(result.classes) == 1
        assert result.classes[0].name == "com.example.Container"

    def test_method_with_generic_params(self):
        """Method with generic parameters should strip to base type."""
        src = """\
package com.example;
import java.util.List;
public class Foo {
    public void process(List<String> items, Map<String, Object> map) {}
}
"""
        result = _parse_content(src)
        params = result.methods[0].parameters
        assert params[0].type_name == "List"
        assert params[1].type_name == "Map"

    def test_enhanced_for_loop(self):
        """Enhanced for-loop variable should be added to type map."""
        src = """\
package com.example;
import java.util.List;
public class Foo {
    public void bar(List<User> users) {
        for (User u : users) {
            u.getName();
        }
    }
}
class User {
    public String getName() { return ""; }
}
"""
        result = _parse_content(src)
        targets = _call_targets(result)
        assert "getName" in targets


# ─── 9. Builder Pattern ─────────────────────────────────────────────

class TestBuilderPattern:

    def test_builder_chain_resolution(self):
        """Builder chain: X.builder().name("x").build() — receiver type should resolve."""
        src = """\
package com.example;
public class Foo {
    public void bar() {
        ProxySelectorDO.builder().name("x").build();
    }
}
class ProxySelectorDO {
    public static Builder builder() { return new Builder(); }
}
class Builder {
    public Builder name(String n) { return this; }
    public ProxySelectorDO build() { return new ProxySelectorDO(); }
}
"""
        result = _parse_content(src)
        build_calls = [c for c in result.calls if c.target_name == "build"]
        assert len(build_calls) >= 1
        # The receiver type should be traced back to ProxySelectorDO
        assert build_calls[0].receiver_type is not None

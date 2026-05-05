"""HTML report generator for TestNexus analysis results."""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..graph.store import GraphStore


class HTMLReportGenerator:
    """Generate self-contained HTML report from TestGraph data."""

    def __init__(self, store: GraphStore, project_name: str = "") -> None:
        self.store = store
        self.project_name = project_name or "TestNexus Report"

    def generate(self, output_path: str | Path) -> str:
        """Generate HTML report and write to file."""
        output_path = Path(output_path)
        html_content = self._build_html()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html_content, encoding="utf-8")
        return str(output_path)

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    def _get_pages(self) -> list[dict]:
        """Get all pages with their action sets.

        Actions store their origin component in `a.componentName` (e.g.,
        "cart", "order" for service-level code within page files). We group
        by this field and merge into matching pages.
        """
        pages_result = self.store.query(
            "MATCH (p:Page) RETURN p.name, p.url, p.filePath ORDER BY p.name"
        )
        page_names = {p["p.name"] for p in pages_result if p.get("p.name")}

        # Build lowercase → Page name mapping
        lower_to_page: dict[str, str] = {}
        for pn in page_names:
            lower_to_page[pn.lower()] = pn

        # Group all actions by their componentName field
        action_result = self.store.query(
            "MATCH (a:Action) RETURN a.name, a.type, a.httpMethod, "
            "a.apiPath, a.componentName, a.selector, a.lineNumber"
        )
        comp_actions: dict[str, list[dict]] = {}
        for row in action_result:
            comp = row.get("a.componentName", "")
            if not comp:
                continue
            comp_actions.setdefault(comp, []).append({
                "name": row["a.name"],
                "type": row["a.type"],
                "httpMethod": row.get("a.httpMethod", ""),
                "apiPath": row.get("a.apiPath", ""),
                "componentName": comp,
                "selector": row.get("a.selector", ""),
                "lineNumber": row.get("a.lineNumber", 0),
            })

        result = []
        for page_row in pages_result:
            page_name = page_row.get("p.name", "")
            if not page_name:
                continue

            page_actions: list[dict] = []

            # 1. Actions from matching lowercase service component
            lower_key = page_name.lower()
            if lower_key in comp_actions:
                page_actions.extend(comp_actions[lower_key])
            # Also check if page name itself has actions
            if page_name != lower_key and page_name in comp_actions:
                page_actions.extend(comp_actions[page_name])

            # 2. Actions from reusable components shared across pages
            for comp_name in ("SimpleHeader", "NavBar"):
                if comp_name in comp_actions:
                    page_actions.extend(comp_actions[comp_name])

            # Deduplicate by (name, httpMethod, apiPath)
            seen = set()
            unique_actions = []
            for a in page_actions:
                key = (a["name"], a.get("httpMethod", ""), a.get("apiPath", ""))
                if key not in seen:
                    seen.add(key)
                    unique_actions.append(a)

            fetch_actions = [a for a in unique_actions if a.get("httpMethod")]
            click_actions = [a for a in unique_actions if not a.get("httpMethod")]

            result.append({
                "name": page_name,
                "url": page_row.get("p.url", ""),
                "file_path": page_row.get("p.filePath", ""),
                "actions": unique_actions,
                "fetch_count": len(fetch_actions),
                "click_count": len(click_actions),
                "total_count": len(unique_actions),
            })

        return sorted(result, key=lambda p: -p["fetch_count"])

    def _get_stats(self) -> dict:
        all_node_types = ["Page", "Component", "Action", "Endpoint",
                           "Module", "TestCase", "LLMAnalysis", "DataSource", "Baseline"]
        # Get tables that actually exist in this database
        existing_tables = set()
        try:
            tables_result = self.store.query("SHOW TABLES")
            for row in tables_result or []:
                for val in row.values():
                    if isinstance(val, str):
                        existing_tables.add(val)
        except Exception:
            # Fallback: assume all tables exist
            existing_tables = set(all_node_types)

        stats = {}
        for node_type in all_node_types:
            if node_type in existing_tables:
                try:
                    result = self.store.query(f"MATCH (n:{node_type}) RETURN count(n) AS cnt")
                    stats[node_type.lower()] = result[0].get("cnt", 0) if result else 0
                except Exception:
                    stats[node_type.lower()] = 0
            else:
                stats[node_type.lower()] = 0

        # Relation counts by type
        try:
            rels = self.store.query(
                "MATCH ()-[r]->() RETURN r.type AS type, count(r) AS cnt ORDER BY cnt DESC"
            )
            stats["relations"] = {r.get("type", ""): r.get("cnt", 0) for r in rels}
        except Exception:
            stats["relations"] = {}
        return stats

    def _get_modules(self) -> list[dict]:
        modules = self.store.query(
            "MATCH (m:Module) RETURN m.id, m.name, m.description, m.source, "
            "m.pageCount, m.componentCount, m.actionCount, "
            "m.endpointCount, m.apiCount ORDER BY m.endpointCount DESC"
        )
        result = []
        for mod in modules:
            entry = {
                "id": mod.get("m.id", ""),
                "name": mod.get("m.name", ""),
                "description": mod.get("m.description", ""),
                "source": mod.get("m.source", ""),
                "page_count": mod.get("m.pageCount", 0),
                "component_count": mod.get("m.componentCount", 0),
                "action_count": mod.get("m.actionCount", 0),
                "endpoint_count": mod.get("m.endpointCount", 0),
                "api_count": mod.get("m.apiCount", 0),
                "pages": [],
                "components": [],
                "endpoints": [],
                "workflows": [],
                "data_sources": [],
            }

            # Pages
            pages = self.store.query(
                f"MATCH (m:Module {{id: $mid}})-[r]->(p:Page) "
                "WHERE r.type = 'BELONGS_TO' RETURN p.name, p.url, p.filePath "
                "ORDER BY p.name LIMIT 50",
                {"mid": entry["id"]},
            )
            entry["pages"] = [
                {"name": p.get("p.name", ""), "url": p.get("p.url", ""),
                 "file_path": p.get("p.filePath", "")} for p in pages
            ]

            # Endpoints
            eps = self.store.query(
                f"MATCH (m:Module {{id: $mid}})-[r]->(e:Endpoint) "
                "WHERE r.type = 'BELONGS_TO' RETURN e.method, e.path, "
                "e.controllerName, e.functionName "
                "ORDER BY e.path LIMIT 50",
                {"mid": entry["id"]},
            )
            entry["endpoints"] = [
                {
                    "method": e.get("e.method", ""),
                    "path": e.get("e.path", ""),
                    "controller": e.get("e.controllerName", ""),
                    "function": e.get("e.functionName", ""),
                } for e in eps
            ]

            # Workflows (Module → Module relations)
            wfs = self.store.query(
                f"MATCH (m:Module {{id: $mid}})-[r]->(m2:Module) "
                "WHERE r.type = 'WORKFLOW' RETURN m2.name, r.reason, r.confidence",
                {"mid": entry["id"]},
            )
            entry["workflows"] = [
                {
                    "to": w.get("m2.name", ""),
                    "reason": w.get("r.reason", ""),
                    "confidence": w.get("r.confidence", 0),
                } for w in wfs
            ]

            result.append(entry)
        return result

    def _get_endpoints_grouped(self) -> list[dict]:
        """Get endpoints grouped by controller."""
        all_eps = self.store.query(
            "MATCH (e:Endpoint) RETURN e.method, e.path, e.controllerName, "
            "e.functionName, e.parameters ORDER BY e.controllerName, e.path"
        )

        groups: dict[str, dict] = {}
        for ep in all_eps:
            ctrl = ep.get("e.controllerName", "Unknown")
            if ctrl not in groups:
                groups[ctrl] = {"controller": ctrl, "endpoints": []}
            groups[ctrl]["endpoints"].append({
                "method": ep.get("e.method", ""),
                "path": ep.get("e.path", ""),
                "function": ep.get("e.functionName", ""),
                "parameters": ep.get("e.parameters", ""),
            })

        return sorted(groups.values(), key=lambda g: g["controller"])

    def _get_api_mappings(self) -> list[dict]:
        """Get frontend Action → backend Endpoint mappings."""
        mappings = self.store.query(
            "MATCH (a:Action)-[r]->(e:Endpoint) "
            "WHERE r.type = 'CALLS' "
            "RETURN a.name, a.type, a.apiPath, a.componentName, "
            "e.method, e.path, e.controllerName, e.functionName, "
            "r.confidence, r.reason "
            "ORDER BY r.confidence DESC LIMIT 100"
        )
        return [
            {
                "action_name": m.get("a.name", ""),
                "action_type": m.get("a.type", ""),
                "action_api_path": m.get("a.apiPath", ""),
                "component": m.get("a.componentName", ""),
                "endpoint_method": m.get("e.method", ""),
                "endpoint_path": m.get("e.path", ""),
                "controller": m.get("e.controllerName", ""),
                "function": m.get("e.functionName", ""),
                "confidence": m.get("r.confidence", 0),
                "reason": m.get("r.reason", ""),
            }
            for m in mappings
        ]

    def _get_llm_analysis(self) -> dict | None:
        """Get LLM analysis results if available."""
        try:
            analyses = self.store.query(
                "MATCH (a:LLMAnalysis) RETURN a.id, a.analysisType, a.modelName, "
                "a.createdAt ORDER BY a.createdAt DESC LIMIT 1"
            )
        except Exception:
            return None

        if not analyses:
            return None

        ann = analyses[0]
        analysis_id = ann.get("a.id", "")

        # Data sources
        ds_list = self.store.query(
            "MATCH (a:LLMAnalysis)-[r]->(d:DataSource) "
            "WHERE r.type = 'IDENTIFIES' "
            "RETURN d.name, d.sourceType, d.description "
            "ORDER BY d.name"
        )
        data_sources = [
            {"name": d.get("d.name", ""), "type": d.get("d.sourceType", ""),
             "description": d.get("d.description", "")}
            for d in ds_list
        ]

        # Endpoint classifications
        cls_list = self.store.query(
            "MATCH (a:LLMAnalysis)-[r]->(e:Endpoint) "
            "WHERE r.type = 'CLASSIFIED' "
            "RETURN e.method, e.path, r.reason, r.confidence "
            "ORDER BY r.confidence DESC LIMIT 100"
        )
        classifications = [
            {
                "method": c.get("e.method", ""),
                "path": c.get("e.path", ""),
                "classification": c.get("r.reason", ""),
                "confidence": c.get("r.confidence", 0),
            }
            for c in cls_list
        ]

        return {
            "id": analysis_id,
            "type": ann.get("a.analysisType", ""),
            "model": ann.get("a.modelName", ""),
            "created_at": ann.get("a.createdAt", ""),
            "data_sources": data_sources,
            "classifications": classifications,
        }

    def _get_module_test_cases(self) -> dict[str, list[dict]]:
        """Get test cases grouped by module from TestCase.actionDef JSON."""
        tcs = self.store.query(
            "MATCH (t:TestCase) RETURN t.id, t.name, t.type, t.status, "
            "t.actionType, t.actionDef, t.filePath, t.targetId"
        )
        result: dict[str, list[dict]] = {}
        import json as _json
        for row in tcs:
            action_def = {}
            raw = row.get("t.actionDef", "")
            if raw:
                try:
                    action_def = _json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Extract module/page/scenario from actionDef
            mod = action_def.get("module", "")
            page = action_def.get("page", "")
            scenario = action_def.get("scenario", row.get("t.status", "unknown"))

            if not mod:
                continue

            result.setdefault(mod, []).append({
                "id": row.get("t.id", ""),
                "name": row.get("t.name", ""),
                "type": row.get("t.type", ""),
                "status": scenario,
                "action_type": row.get("t.actionType", ""),
                "action_def": action_def,
                "file_path": row.get("t.filePath", ""),
                "target_id": row.get("t.targetId", ""),
                "page": page,
            })
        return result

    def _get_test_cases(self) -> list[dict]:
        """Get all generated test cases with scenario from actionDef."""
        tcs = self.store.query(
            "MATCH (t:TestCase) RETURN t.id, t.name, t.type, t.status, "
            "t.filePath, t.targetId, t.actionType, t.actionDef ORDER BY t.name"
        )
        import json
        result = []
        for t in tcs:
            action_def = {}
            raw = t.get("t.actionDef", "")
            if raw:
                try:
                    action_def = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
            scenario = action_def.get("scenario", t.get("t.status", "unknown"))
            result.append({
                "id": t.get("t.id", ""),
                "name": t.get("t.name", ""),
                "type": t.get("t.type", ""),
                "status": scenario,
                "file_path": t.get("t.filePath", ""),
                "target_id": t.get("t.targetId", ""),
                "action_type": t.get("t.actionType", ""),
                "journey": action_def,
            })
        return result

    def _get_coverage(self) -> dict | None:
        """Read coverage.json from the journeys output directory."""
        # Try to find coverage.json relative to the DB path
        db_path = getattr(self.store, "db_path", None)
        if db_path:
            coverage_file = Path(db_path).parent / "tests" / "journeys" / "coverage.json"
            if coverage_file.is_file():
                return json.loads(coverage_file.read_text())
        # Fallback: try common locations
        for candidate in [
            Path("/tmp/test-projects/newbee-mall-vue3-app/.testnexus/tests/journeys/coverage.json"),
        ]:
            if candidate.is_file():
                return json.loads(candidate.read_text())
        return None

    def _get_baselines(self) -> list[dict]:
        """Get baselines and their records."""
        baselines = self.store.query(
            "MATCH (b:Baseline) RETURN b.id, b.name, b.createdAt, b.testCount, b.source "
            "ORDER BY b.createdAt DESC"
        )
        result = []
        for bl in baselines:
            bl_id = bl.get("b.id", "")
            bl_entry = {
                "id": bl_id,
                "name": bl.get("b.name", ""),
                "created_at": bl.get("b.createdAt", ""),
                "test_count": bl.get("b.testCount", 0),
                "source": bl.get("b.source", ""),
                "records": [],
            }
            # Get records
            records = self.store.query(
                "MATCH (tr:TestRecord)-[r:IN_BASELINE]->(b:Baseline {id: $bid}) "
                "RETURN tr.testCaseId, tr.statusCode, tr.duration, tr.timestamp "
                "ORDER BY tr.testCaseId LIMIT 100",
                {"bid": bl_id},
            )
            bl_entry["records"] = [
                {
                    "test_case_id": r.get("tr.testCaseId", ""),
                    "status_code": r.get("tr.statusCode", 0),
                    "duration": r.get("tr.duration", 0),
                    "timestamp": r.get("tr.timestamp", ""),
                }
                for r in records
            ]
            result.append(bl_entry)
        return result

    def _get_file_to_tests(self) -> dict[str, list[dict]]:
        """Map source files to test cases for impact analysis.

        Uses Page.filePath from TestCase.actionDef to link pages to tests.
        """
        tcs = self.store.query(
            "MATCH (t:TestCase) RETURN t.id, t.name, t.type, t.actionType, t.actionDef"
        )
        # Get page -> filePath mapping
        pages = self.store.query("MATCH (p:Page) RETURN p.name, p.filePath")
        page_to_file: dict[str, str] = {}
        for p in pages:
            page_to_file[p["p.name"]] = p["p.filePath"]

        result: dict[str, list[dict]] = {}
        for row in tcs:
            action_def = {}
            raw = row.get("t.actionDef", "")
            if raw:
                try:
                    action_def = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            page_name = action_def.get("page", "")
            file_path = page_to_file.get(page_name, "")
            if not file_path:
                continue

            result.setdefault(file_path, []).append({
                "id": row.get("t.id", ""),
                "name": row.get("t.name", ""),
                "type": row.get("t.type", ""),
                "action_type": row.get("t.actionType", ""),
                "page": page_name,
                "tc_path": "",
            })

        # Deduplicate by test case ID per file
        deduped: dict[str, list[dict]] = {}
        for fpath, tests in result.items():
            seen = set()
            unique = []
            for t in tests:
                if t["id"] not in seen:
                    seen.add(t["id"])
                    unique.append(t)
            deduped[fpath] = unique

        return deduped

    # ------------------------------------------------------------------
    # HTML building
    # ------------------------------------------------------------------

    def _build_html(self) -> str:
        stats = self._get_stats()
        pages = self._get_pages()
        modules = self._get_modules()
        endpoints = self._get_endpoints_grouped()
        api_mappings = self._get_api_mappings()
        llm = self._get_llm_analysis()
        coverage = self._get_coverage()
        test_cases = self._get_test_cases()
        baselines = self._get_baselines()
        module_test_cases = self._get_module_test_cases()
        file_to_tests = self._get_file_to_tests()
        now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M CST")

        # Serialize data for client-side JS
        test_data_json = json.dumps([
            {
                "id": tc["id"],
                "name": tc["name"],
                "type": tc["type"],
                "status": tc["status"],
                "action_type": tc["action_type"],
                "file_path": tc["file_path"],
                "journey": tc["journey"],
            }
            for tc in test_cases
        ], ensure_ascii=False)

        module_test_data_json = json.dumps(module_test_cases, ensure_ascii=False)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(self.project_name)} - TestNexus 分析报告</title>
<style>
{self._css()}
</style>
</head>
<body>
{self._header(now)}

<div class="container">
{self._overview_section(stats)}
{self._coverage_section(coverage)}
{self._new_modules_section(modules, module_test_cases)}
{self._new_test_table_section(test_cases, module_test_cases)}
{self._pages_section(pages)}
{self._endpoints_section(endpoints)}
{self._api_mapping_section(api_mappings)}
{self._impact_section(file_to_tests)}
</div>

{self._footer()}
<script>
window.__TEST_DATA__ = {test_data_json};
window.__MODULE_TESTS__ = {module_test_data_json};
{self._javascript()}
</script>
</body>
</html>"""

    def _header(self, now: str) -> str:
        return f"""<header>
<div class="header-inner">
<h1>TestNexus 分析报告</h1>
<div class="header-meta">
<span class="badge badge-blue">{_esc(self.project_name)}</span>
<span>生成时间: {now}</span>
</div>
</div>
<nav class="nav-bar">
<a href="#overview">概览</a>
<a href="#coverage">覆盖率</a>
<a href="#modules">功能模块</a>
<a href="#test-table">全部用例</a>
<a href="#pages">页面</a>
<a href="#endpoints">API 端点</a>
<a href="#mappings">API 映射</a>
<a href="#impact">变更影响</a>
</nav>
</header>"""

    def _overview_section(self, stats: dict) -> str:
        cards = [
            ("页面", stats.get("page", 0), "#6366f1"),
            ("组件", stats.get("component", 0), "#8b5cf6"),
            ("操作", stats.get("action", 0), "#a855f7"),
            ("端点", stats.get("endpoint", 0), "#06b6d4"),
            ("模块", stats.get("module", 0), "#10b981"),
            ("测试用例", stats.get("testcase", 0), "#f59e0b"),
        ]

        cards_html = "".join(
            f'<div class="stat-card"><span class="stat-value">{v}</span>'
            f'<span class="stat-label">{l}</span></div>'
            for l, v, _ in cards
        )

        # Relations breakdown
        rels = stats.get("relations", {})
        rel_items = "".join(
            f"<li><code>{_esc(k)}</code> <span>{v}</span></li>"
            for k, v in sorted(rels.items(), key=lambda x: -x[1])
        )

        return f"""<section id="overview">
<h2>概览</h2>
<div class="stats-grid">{cards_html}</div>
<h3>关系</h3>
<ul class="rel-list">{rel_items}</ul>
</section>"""

    def _pages_section(self, pages: list[dict]) -> str:
        if not pages:
            return """<section id="pages"><h2>页面</h2><p>未找到页面。</p></section>"""

        sections = ""
        for page in pages:
            fetch_actions = [a for a in page["actions"] if a.get("httpMethod")]
            click_actions = [a for a in page["actions"] if not a.get("httpMethod")]

            # Fetch actions table
            fetch_rows = ""
            for a in fetch_actions:
                fetch_rows += f"""<tr>
<td><span class="method-{_http_color(a['httpMethod'])}">{_esc(a['httpMethod'])}</span></td>
<td>{_esc(a['name'])}</td>
<td><code>{_esc(a['apiPath'])}</code></td>
<td><span class="badge badge-{_action_type_color(a['type'])}">{_esc(a['type'])}</span></td>
<td class="muted">{a.get('lineNumber', '')}</td>
</tr>"""

            # Click actions table
            click_rows = ""
            for a in click_actions:
                click_rows += f"""<tr>
<td><span class="badge badge-{_action_type_color(a['type'])}">{_esc(a['type'])}</span></td>
<td>{_esc(a['name'])}</td>
<td><span class="muted">{_esc(a.get('componentName', ''))}</span></td>
<td class="muted">{a.get('lineNumber', '')}</td>
</tr>"""

            sections += f"""<details class="ctrl-group">
<summary>
<strong>{_esc(page['name'])}</strong>
<span class="muted">({_esc(page.get('url', ''))})</span>
<span class="badge badge-blue">{page['fetch_count']} 个 API 操作</span>
<span class="badge badge-purple">{page['click_count']} 个交互操作</span>
</summary>
<div style="padding: 0.5rem 1rem;">
<p class="muted" style="margin:0.5rem 0">源文件: <code>{_esc(page.get('file_path', ''))}</code></p>
{f'<h5>API 操作 ({page["fetch_count"]})</h5>' if fetch_rows else ''}
{f'<table class="data-table"><thead><tr><th>方法</th><th>名称</th><th>API 路径</th><th>类型</th><th>行号</th></tr></thead><tbody>{fetch_rows}</tbody></table>' if fetch_rows else ''}
{f'<h5>交互操作 ({page["click_count"]})</h5>' if click_rows else ''}
{f'<table class="data-table"><thead><tr><th>类型</th><th>名称</th><th>组件</th><th>行号</th></tr></thead><tbody>{click_rows}</tbody></table>' if click_rows else ''}
</div>
</details>"""

        return f"""<section id="pages">
<h2>页面 ({len(pages)})</h2>
{sections}
</section>"""

    def _new_modules_section(self, modules: list[dict], module_tests: dict[str, list[dict]]) -> str:
        """Module-centric view with embedded test cases and coverage gaps."""
        if not modules:
            return """<section id="modules"><h2>功能模块</h2>
<p>未找到业务模块。请运行 <code>testnexus analyze --backend &lt;path&gt;</code> 启用模块推断。</p></section>"""

        # Summary cards per module
        module_cards = ""
        for mod in modules:
            mod_name = mod["name"]
            tests = module_tests.get(mod_name, [])
            test_count = len(tests)
            # Count by scenario type
            scenario_counts = {}
            for t in tests:
                stype = t.get("status", "unknown")
                scenario_counts[stype] = scenario_counts.get(stype, 0) + 1

            normal = scenario_counts.get("normal", 0)
            error = scenario_counts.get("error", 0)
            boundary = scenario_counts.get("boundary", 0)

            # Coverage gaps
            uncovered_eps = []
            mod_eps = [e["path"] for e in mod.get("endpoints", [])]
            covered_paths = set()
            for t in tests:
                journey = t.get("action_def", {})
                for step in journey.get("steps", []):
                    action = step.get("action", {})
                    url = action.get("url", "")
                    if url:
                        # Extract path part
                        for ep in mod_eps:
                            if ep in url:
                                covered_paths.add(ep)
            for ep in mod_eps:
                if ep not in covered_paths:
                    uncovered_eps.append(ep)

            gap_class = "" if uncovered_eps else " module-complete"
            cards = []
            if test_count > 0:
                cards.append(f'<span class="badge badge-green">{test_count} 用例</span>')
            if normal > 0:
                cards.append(f'<span class="badge badge-blue">{normal} normal</span>')
            if error > 0:
                cards.append(f'<span class="badge badge-yellow">{error} error</span>')
            if boundary > 0:
                cards.append(f'<span class="badge badge-purple">{boundary} boundary</span>')
            if uncovered_eps:
                cards.append(f'<span class="badge badge-red" title="未覆盖 API: {", ".join(uncovered_eps[:3])}">{len(uncovered_eps)} 未覆盖</span>')

            module_cards += f"""<div class="module-card{gap_class}" data-module="{_esc(mod_name)}" onclick="toggleModuleDetail('{_esc(mod_name)}')">
<div class="module-card-header">
<strong>{_esc(mod_name)}</strong>
<div class="module-card-badges">{''.join(cards)}</div>
</div>
<div class="module-card-meta">
<span>{mod['page_count']} 页面</span>
<span>{mod['endpoint_count']} 端点</span>
<span>{mod['api_count']} API</span>
<span class="muted">{_esc(mod.get('source', ''))}</span>
</div>
{f'<div class="module-desc">{_esc(mod.get("description", ""))}</div>' if mod.get('description') else ''}
</div>"""

        # Detail panels
        details_html = ""
        for mod in modules:
            mod_name = mod["name"]
            tests = module_tests.get(mod_name, [])

            # Group tests by page
            by_page: dict[str, list[dict]] = {}
            for t in tests:
                pg = t.get("page", "未关联页面")
                by_page.setdefault(pg, []).append(t)

            # Group tests by scenario type
            by_scenario: dict[str, list[dict]] = {}
            for t in tests:
                stype = t.get("status", "unknown")
                by_scenario.setdefault(stype, []).append(t)

            # Pages in this module
            pages_list = "".join(
                f"<li>{_esc(p['name'])}</li>" for p in mod.get("pages", [])
            )

            # Endpoints in this module with coverage indicator
            mod_eps = set(e["path"] for e in mod.get("endpoints", []))
            covered = set()
            for t in tests:
                journey = t.get("action_def", {})
                for step in journey.get("steps", []):
                    action = step.get("action", {})
                    url = action.get("url", "")
                    for ep in mod_eps:
                        if ep in url:
                            covered.add(ep)

            eps_list = ""
            for e in mod.get("endpoints", [])[:30]:
                ep_path = e["path"]
                is_covered = ep_path in covered
                dot = f'<span class="status-dot" style="background:{"#22c55e" if is_covered else "#ef4444"}" title="{"已覆盖" if is_covered else "未覆盖"}"></span>'
                eps_list += f"<li>{dot} <span class=\"method-{_http_color(e['method'])}\">{_esc(e['method'])}</span> <code>{_esc(ep_path)}</code></li>"

            # Test cases grouped by page
            test_detail = ""
            for pg, pg_tests in sorted(by_page.items()):
                # Sample test cases for this page (show first 20)
                sample_tests = ""
                for t in pg_tests[:20]:
                    journey = t.get("action_def", {})
                    steps = journey.get("steps", [])
                    step_count = len(steps)
                    api_info = ""
                    for s in steps:
                        action = s.get("action", {})
                        if action.get("method"):
                            api_info = f'<span class="method-{_http_color(action["method"])}">{_esc(action["method"])}</span> <code>{_esc(action.get("url", ""))}</code>'
                            break
                    stype_badge = f'<span class="badge badge-{_scenario_color(t.get("status", ""))}">{_esc(t.get("status", ""))}</span>'
                    sample_tests += f"""<tr>
<td class="muted">{_esc(t['id'][:16])}...</td>
<td>{_esc(t['name'])}</td>
<td>{stype_badge}</td>
<td>{step_count} 步</td>
<td>{api_info}</td>
</tr>"""

                more_tag = ""
                if len(pg_tests) > 20:
                    more_tag = f'<tr><td colspan="5" class="muted" style="text-align:center">... 还有 {len(pg_tests) - 20} 条用例，请在「全部用例」表格中筛选 ...</td></tr>'

                test_detail += f"""<div style="margin: 0.75rem 0;">
<h6 style="margin:0 0 0.25rem; font-size:0.85rem; color:var(--text-muted)">{_esc(pg)} ({len(pg_tests)} 条用例)</h6>
<table class="data-table"><thead><tr><th>ID</th><th>名称</th><th>场景</th><th>步骤</th><th>API</th></tr></thead>
<tbody>{sample_tests}{more_tag}</tbody></table>
</div>"""

            details_html += f"""<div class="module-detail" id="mod-detail-{_esc(mod_name)}" style="display:none">
<h4 style="margin-bottom:0.5rem">{_esc(mod_name)} 详情</h4>
{f'<p class="muted" style="margin-bottom:0.5rem">{_esc(mod.get("description", ""))}</p>' if mod.get('description') else ''}

<div class="detail-grid">
<div class="detail-panel">
<h5>页面 ({len(mod.get("pages", []))})</h5>
<ul>{pages_list}</ul>
</div>
<div class="detail-panel">
<h5>端点 ({len(mod.get("endpoints", []))})</h5>
<ul>{eps_list}</ul>
{f'<p class="muted" style="margin-top:0.25rem">绿色=已覆盖, 红色=未覆盖</p>' if mod.get('endpoints') else ''}
</div>
</div>

<h5 style="margin-top:1rem">测试用例 ({len(tests)})</h5>

<div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.5rem">
{"".join(f'<span class="badge badge-{_scenario_color(st)}">{_esc(st)}: {len(ts)}</span>' for st, ts in sorted(by_scenario.items()))}
</div>
{test_detail if test_detail else '<p class="muted">暂无测试用例，请运行 <code>testnexus generate</code></p>'}
</div>"""

        return f"""<section id="modules">
<h2>功能模块 ({len(modules)})</h2>
<p class="muted" style="margin-bottom:1rem">点击模块卡片展开查看详情，绿色边框表示该模块所有端点均被测试覆盖。</p>
<div class="module-grid">{module_cards}</div>
{details_html}
</section>"""

    def _modules_section(self, modules: list[dict]) -> str:
        if not modules:
            return """<section id="modules"><h2>业务模块</h2>
<p>未找到业务模块。请运行 <code>testnexus analyze --backend &lt;path&gt;</code> 启用模块推断。</p></section>"""

        # Table
        rows = ""
        for mod in modules:
            src_badge = f'<span class="badge badge-{_src_color(mod["source"])}">{_esc(mod["source"])}</span>'
            rows += f"""<tr class="module-row" data-module="{_esc(mod['name'])}">
<td><strong>{_esc(mod["name"])}</strong></td>
<td>{_esc(mod.get("description", "")[:80])}</td>
<td>{src_badge}</td>
<td>{mod["page_count"]}</td>
<td>{mod["endpoint_count"]}</td>
<td>{mod["api_count"]}</td>
</tr>"""

        # Detail panels (hidden by default, toggled via JS)
        details_html = ""
        for mod in modules:
            pages_list = "".join(
                f"<li>{_esc(p['name'])} <code>{_esc(p.get('url', ''))}</code></li>"
                for p in mod.get("pages", [])
            )
            eps_list = "".join(
                f"<li><span class=\"method-{_http_color(e['method'])}\">{_esc(e['method'])}</span> "
                f"<code>{_esc(e['path'])}</code> "
                f"<span class=\"muted\">{_esc(e['controller'])}.{_esc(e['function'])}</span></li>"
                for e in mod.get("endpoints", [])
            )
            wfs_list = "".join(
                f"<li>{_esc(mod['name'])} &rarr; {_esc(w['to'])}: {_esc(w['reason'])}</li>"
                for w in mod.get("workflows", [])
            )
            details_html += f"""<div class="module-detail" id="detail-{_esc(mod['name'])}">
<h4>{_esc(mod["name"])} {_esc(mod.get("description", ""))}</h4>
{f'<h5>页面 ({len(mod["pages"])})</h5><ul>{pages_list}</ul>' if mod["pages"] else ""}
{f'<h5>端点 ({len(mod["endpoints"])})</h5><ul>{eps_list}</ul>' if mod["endpoints"] else ""}
{f'<h5>工作流</h5><ul>{wfs_list}</ul>' if mod["workflows"] else ""}
</div>"""

        return f"""<section id="modules">
<h2>业务模块 ({len(modules)})</h2>
<table class="data-table">
<thead><tr><th>名称</th><th>描述</th><th>来源</th><th>页面</th><th>端点</th><th>API</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p class="muted" style="margin-top:0.5rem">点击模块行展开详情。</p>
{details_html}
</section>"""

    def _coverage_section(self, coverage: dict | None) -> str:
        if not coverage:
            return """<section id="coverage"><h2>测试覆盖率</h2>
<p>未找到覆盖率数据。请先运行 <code>testnexus generate</code> 生成测试用例。</p></section>"""

        api_cov = coverage.get("api_endpoint_coverage", {})
        http_cov = coverage.get("http_method_coverage", {})
        mod_cov = coverage.get("module_coverage", {})
        scen_cov = coverage.get("scenario_coverage", {})
        by_mod = coverage.get("by_module", [])
        untested = coverage.get("untested_apis", [])
        param_cov = coverage.get("param_coverage", {})

        # Summary cards
        cards = [
            ("API 端点覆盖", f"{api_cov.get('percentage', 0)}%", f"{api_cov.get('covered', 0)}/{api_cov.get('total', 0)}", "#22c55e"),
            ("HTTP 方法覆盖", f"{http_cov.get('percentage', 0)}%", ", ".join(http_cov.get("tested", [])), "#3b82f6"),
            ("页面覆盖", str(scen_cov.get("total_apis_tested", 0)), f"{scen_cov.get('total_apis_tested', 0)} 个 API 被测试", "#8b5cf6"),
            ("模块覆盖", f"{mod_cov.get('percentage', 0)}%", f"{mod_cov.get('covered', 0)}/{mod_cov.get('total', 0)} 个模块", "#f59e0b"),
        ]
        if param_cov:
            cards.insert(1, (
                "参数覆盖",
                f"{param_cov.get('percentage', 0)}%",
                f"{param_cov.get('covered_params', 0)}/{param_cov.get('total_params', 0)} 参数 (全覆 {param_cov.get('fully_covered_percentage', 0)}%)",
                "#06b6d4",
            ))
        cards_html = "".join(
            f'<div class="stat-card"><span class="stat-value" style="color:{c}">{v}</span>'
            f'<span class="stat-label">{l}</span><span class="stat-label muted">{d}</span></div>'
            for l, v, d, c in cards
        )

        # Scenario coverage bar
        total_tested = scen_cov.get("total_apis_tested", 0)
        all_scen = scen_cov.get("apis_with_all_scenarios", 0)
        has_error = scen_cov.get("apis_with_error", 0)
        has_boundary = scen_cov.get("apis_with_boundary", 0)

        pct_all = round(all_scen / max(total_tested, 1) * 100)
        pct_err = round(has_error / max(total_tested, 1) * 100)
        pct_bnd = round(has_boundary / max(total_tested, 1) * 100)

        scenario_html = f"""
<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:0.5rem;padding:1rem;margin:1rem 0">
<h5>场景覆盖</h5>
<div style="display:flex;gap:1rem;margin-top:0.5rem">
<div style="flex:1"><span class="muted">normal</span> <strong>{total_tested}/{total_tested}</strong></div>
<div style="flex:1"><span class="muted">error</span> <strong>{has_error}/{total_tested} ({pct_err}%)</strong></div>
<div style="flex:1"><span class="muted">boundary</span> <strong>{has_boundary}/{total_tested} ({pct_bnd}%)</strong></div>
</div>
<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin-top:0.5rem;background:#1e293b">
<div style="width:100%;background:#22c55e"></div>
</div>
<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin-top:0.25rem;background:#1e293b">
<div style="width:{pct_err}%;background:#3b82f6"></div>
</div>
<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin-top:0.25rem;background:#1e293b">
<div style="width:{pct_bnd}%;background:#a855f7"></div>
</div>
</div>"""

        # Parameter-level coverage
        param_html = ""
        if param_cov:
            by_scenario = param_cov.get("by_scenario", {})
            scenario_rows = ""
            for stype in ["normal", "missing", "invalid", "boundary"]:
                sd = by_scenario.get(stype, {})
                pct = sd.get("pct", 0)
                covered = sd.get("covered", 0)
                total = sd.get("total", 0)
                bar_color = "#22c55e" if pct >= 90 else ("#3b82f6" if pct >= 70 else "#f59e0b" if pct >= 50 else "#ef4444")
                scenario_rows += f"""<tr>
<td><span class="badge badge-{"green" if pct >= 90 else "blue" if pct >= 70 else "yellow"}">{_esc(stype)}</span></td>
<td>{covered}/{total}</td>
<td>{pct}%</td>
<td><div style="background:#1e293b;border-radius:4px;height:12px;overflow:hidden">
<div style="width:{pct}%;height:100%;background:{bar_color};border-radius:4px"></div></div></td>
</tr>"""

            # By API param coverage
            api_param_rows = ""
            for a in param_cov.get("by_api", []):
                api_name = _esc(a["api"])
                params = a["params"]
                scenarios = a["scenarios"]
                covered = a["covered_params"]
                uncovered = a.get("uncovered_params", [])
                pct_api = round(covered / max(params, 1) * 100)
                bar_color_api = "#22c55e" if pct_api >= 90 else ("#3b82f6" if pct_api >= 70 else "#f59e0b")
                untag = ""
                if uncovered:
                    untag = f' <span class="muted" title="未覆盖: {", ".join(uncovered)}">未覆盖: {", ".join(uncovered[:3])}{"…" if len(uncovered) > 3 else ""}</span>'
                api_param_rows += f"""<tr>
<td><code>{api_name}</code></td>
<td>{params}</td>
<td>{scenarios}</td>
<td>{covered}/{params}</td>
<td><div style="background:#1e293b;border-radius:4px;height:12px;overflow:hidden">
<div style="width:{pct_api}%;height:100%;background:{bar_color_api};border-radius:4px"></div></div></td>
<td>{untag}</td>
</tr>"""

            param_html = f"""<h3>参数级覆盖</h3>
<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:0.5rem;padding:1rem;margin:1rem 0">
<div style="display:flex;gap:1rem;margin-bottom:1rem">
<div style="flex:1;text-align:center">
<div style="font-size:1.5rem;font-weight:700;color:#22c55e">{param_cov.get('covered_params', 0)}/{param_cov.get('total_params', 0)}</div>
<div class="muted">被覆盖参数</div>
</div>
<div style="flex:1;text-align:center">
<div style="font-size:1.5rem;font-weight:700;color:#3b82f6">{param_cov.get('fully_covered', 0)}/{param_cov.get('total_params', 0)}</div>
<div class="muted">完全覆盖 (4/4 场景)</div>
</div>
<div style="flex:1;text-align:center">
<div style="font-size:1.5rem;font-weight:700;color:#f59e0b">{param_cov.get('percentage', 0)}%</div>
<div class="muted">参数覆盖率</div>
</div>
</div>
<table class="data-table"><thead><tr><th>场景类型</th><th>覆盖数/总数</th><th>百分比</th><th>进度条</th></tr></thead>
<tbody>{scenario_rows}</tbody></table>
</div>
<details class="ctrl-group">
<summary><strong>按 API 查看参数覆盖</strong></summary>
<table class="data-table"><thead><tr><th>API</th><th>参数数</th><th>测试数</th><th>已覆盖</th><th>覆盖率</th><th>未覆盖参数</th></tr></thead>
<tbody>{api_param_rows}</tbody></table>
</details>"""

        # By module table
        mod_rows = ""
        for m in by_mod:
            name = _esc(m.get("name", ""))
            tests = m.get("tests", 0)
            scenarios = ", ".join(m.get("scenarios", []))
            has_all = "✓" if len(m.get("scenarios", [])) >= 3 else ""
            mod_rows += f"""<tr>
<td><strong>{name}</strong></td>
<td>{tests}</td>
<td>{_esc(scenarios)}</td>
<td style="text-align:center;color:#22c55e">{has_all}</td>
</tr>"""

        # Untested APIs
        untested_html = ""
        if untested:
            untested_rows = "".join(
                f'<tr><td><span class="method-{_http_color(u["method"])}">{_esc(u["method"])}</span></td>'
                f'<td><code>{_esc(u["path"])}</code></td></tr>'
                for u in untested
            )
            untested_html = f"""<h3>未覆盖的 API ({len(untested)})</h3>
<table class="data-table"><thead><tr><th>方法</th><th>路径</th></tr></thead>
<tbody>{untested_rows}</tbody></table>"""

        return f"""<section id="coverage">
<h2>测试覆盖率</h2>
<div class="stats-grid">{cards_html}</div>
{scenario_html}
{param_html}
<h3>按模块细分</h3>
<table class="data-table">
<thead><tr><th>模块</th><th>测试数</th><th>场景类型</th><th>完整覆盖</th></tr></thead>
<tbody>{mod_rows}</tbody>
</table>
{untested_html}
</section>"""

    def _endpoints_section(self, endpoints: list[dict]) -> str:
        if not endpoints:
            return """<section id="endpoints"><h2>API 端点</h2><p>未找到端点。</p></section>"""

        total_eps = sum(len(g["endpoints"]) for g in endpoints)
        sections = ""
        for group in endpoints:
            rows = ""
            for ep in group["endpoints"]:
                params = ep.get("parameters", "")
                params_display = f' <span class="muted">{_esc(params[:60])}</span>' if params and params != "{}" else ""
                rows += f"""<tr>
<td><span class="method-{_http_color(ep['method'])}">{_esc(ep['method'])}</span></td>
<td><code>{_esc(ep['path'])}</code></td>
<td>{_esc(ep['function'])}</td>
<td>{params_display}</td>
</tr>"""
            sections += f"""<details class="ctrl-group">
<summary><strong>{_esc(group['controller'])}</strong> <span class="muted">({len(group['endpoints'])} 个端点)</span></summary>
<table class="data-table"><thead><tr><th>方法</th><th>路径</th><th>函数</th><th>参数</th></tr></thead>
<tbody>{rows}</tbody></table>
</details>"""

        return f"""<section id="endpoints">
<h2>API 端点 (共 {total_eps} 个，{len(endpoints)} 个控制器)</h2>
{sections}
</section>"""

    def _api_mapping_section(self, mappings: list[dict]) -> str:
        if not mappings:
            return """<section id="mappings"><h2>API 映射</h2>
<p>未找到前端到后端的 API 映射。请同时提供 <code>--frontend</code> 和 <code>--backend</code> 路径。</p></section>"""

        # Confidence distribution
        exact = sum(1 for m in mappings if m["confidence"] >= 1.0)
        high = sum(1 for m in mappings if 0.9 <= m["confidence"] < 1.0)
        medium = sum(1 for m in mappings if 0.6 <= m["confidence"] < 0.9)
        low = sum(1 for m in mappings if m["confidence"] < 0.6)

        dist = (
            f'<span class="conf-exact">精确: {exact}</span> &middot; '
            f'<span class="conf-high">高: {high}</span> &middot; '
            f'<span class="conf-medium">中: {medium}</span> &middot; '
            f'<span class="conf-low">低: {low}</span>'
        )

        rows = ""
        for m in mappings:
            conf_color = _conf_color(m["confidence"])
            rows += f"""<tr>
<td><span class="conf-badge" style="background:{conf_color}">{m["confidence"]:.0%}</span></td>
<td>{_esc(m["component"])}.{_esc(m["action_name"])}</td>
<td><span class="method-{_http_color(m['action_api_path'][:3])}">{_esc(m.get('action_api_path', ''))}</span></td>
<td><span class="method-{_http_color(m['endpoint_method'])}">{_esc(m['endpoint_method'])}</span> <code>{_esc(m['endpoint_path'])}</code></td>
<td>{_esc(m['controller'])}.{_esc(m['function'])}</td>
<td class="muted">{_esc(m.get('reason', '')[:60])}</td>
</tr>"""

        return f"""<section id="mappings">
<h2>API 映射 ({len(mappings)})</h2>
<p>置信度分布: {dist}</p>
<table class="data-table">
<thead><tr><th>置信度</th><th>前端操作</th><th>前端路径</th><th>后端端点</th><th>控制器</th><th>原因</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</section>"""

    def _llm_section(self, llm: dict | None) -> str:
        if not llm:
            return """<section id="llm"><h2>LLM 分析</h2>
<p>无 LLM 分析结果。请运行 <code>testnexus llm-analyze</code> 或 <code>testnexus analyze --llm</code> 启用。</p></section>"""

        ds_rows = ""
        for ds in llm.get("data_sources", []):
            ds_rows += f"""<tr><td>{_esc(ds['name'])}</td><td>{_esc(ds['type'])}</td><td>{_esc(ds.get('description', ''))}</td></tr>"""

        cls_rows = ""
        for c in llm.get("classifications", [])[:50]:
            cls_rows += f"""<tr>
<td><span class="method-{_http_color(c['method'])}">{_esc(c['method'])}</span> <code>{_esc(c['path'])}</code></td>
<td>{_esc(c['classification'])}</td>
<td>{c['confidence']:.0%}</td>
</tr>"""

        return f"""<section id="llm">
<h2>LLM 分析</h2>
<div class="info-card">
<strong>模型:</strong> {_esc(llm['model'])} &middot;
<strong>类型:</strong> {_esc(llm['type'])} &middot;
<strong>生成时间:</strong> {_esc(llm['created_at'])}
</div>

<h3>数据源 ({len(llm['data_sources'])})</h3>
<table class="data-table">
<thead><tr><th>名称</th><th>类型</th><th>描述</th></tr></thead>
<tbody>{ds_rows}</tbody>
</table>

<h3>端点分类 ({len(llm['classifications'])})</h3>
<table class="data-table">
<thead><tr><th>端点</th><th>分类</th><th>置信度</th></tr></thead>
<tbody>{cls_rows}</tbody>
</table>
{f'<p class="muted">显示 50 条，共 {len(llm["classifications"])} 条分类</p>' if len(llm.get('classifications', [])) > 50 else ''}
</section>"""

    def _new_test_table_section(self, test_cases: list[dict], module_tests: dict[str, list[dict]]) -> str:
        """Searchable, filterable test case table with pagination."""
        if not test_cases:
            return """<section id="test-table"><h2>全部测试用例</h2>
<p>未生成测试用例。请先运行 <code>testnexus generate</code>。</p></section>"""

        # Count by type and status for filter buttons
        type_counts = {}
        status_counts = {}
        for tc in test_cases:
            t = tc.get("type", "")
            s = tc.get("status", "")
            type_counts[t] = type_counts.get(t, 0) + 1
            status_counts[s] = status_counts.get(s, 0) + 1

        type_filters = "".join(
            f'<button class="filter-btn" data-filter="type" data-value="{_esc(t)}">{_esc(t)} ({c})</button>'
            for t, c in sorted(type_counts.items())
        )
        status_filters = "".join(
            f'<button class="filter-btn" data-filter="status" data-value="{_esc(s)}">{_esc(s)} ({c})</button>'
            for s, c in sorted(status_counts.items())
        )

        # Module filter buttons
        mod_filters = "".join(
            f'<button class="filter-btn" data-filter="module" data-value="{_esc(m)}">{_esc(m)} ({len(ts)})</button>'
            for m, ts in sorted(module_tests.items())
        )

        return f"""<section id="test-table">
<h2>全部测试用例 ({len(test_cases)})</h2>

<div class="filter-bar">
<input type="text" id="test-search" placeholder="搜索用例名称、ID、API 路径..." class="search-input">
<span class="muted" id="result-count">显示 {len(test_cases)}/{len(test_cases)} 条</span>
</div>

<div class="filter-group">
<label class="muted">场景类型:</label>
<button class="filter-btn active" data-filter="status" data-value="all">全部 ({len(test_cases)})</button>
{status_filters}
</div>

<div class="filter-group">
<label class="muted">模块:</label>
<button class="filter-btn active" data-filter="module" data-value="all">全部</button>
{mod_filters}
</div>

<div id="test-table-body" class="test-table-wrapper"></div>
</section>"""

    def _test_section(self, test_cases: list[dict]) -> str:
        """Legacy test section - kept for backward compat."""
        if not test_cases:
            return """<section id="tests"><h2>测试用例</h2>
<p>未生成测试用例。请先运行 <code>testnexus generate</code>。</p></section>"""

        sections = ""
        for tc in test_cases[:50]:
            journey = tc.get("journey", {})
            steps = journey.get("steps", [])
            setup = journey.get("setup", [])
            teardown = journey.get("teardown", [])
            purpose = journey.get("purpose", "")
            tags = journey.get("tags", [])

            # Step details
            step_rows = ""
            for i, step in enumerate(steps, 1):
                action = step.get("action", {})
                method = action.get("method", "")
                url = action.get("url", "")
                atype = action.get("type", "")
                asserts = step.get("assert", [])
                assert_desc = ""
                for a in asserts:
                    op = a.get("operator", "")
                    val = a.get("value", "")
                    assert_desc += f"{a.get('field', '')} {op} {val}; "

                action_html = ""
                if atype == "api_request":
                    action_html = (f'<span class="method-{_http_color(method)}">{_esc(method)}</span> '
                                   f'<code>{_esc(url)}</code>')
                elif atype == "page_navigation":
                    action_html = f'<span class="muted">导航</span> <code>{_esc(url)}</code>'
                else:
                    action_html = f'<span class="muted">{_esc(atype)}</span>'

                step_rows += f"""<tr>
<td>{i}</td>
<td>{_esc(step.get('name', ''))}</td>
<td>{action_html}</td>
<td class="muted">{_esc(assert_desc.rstrip("; "))}</td>
</tr>"""

            # Setup rows
            setup_html = ""
            if setup:
                setup_items = "".join(
                    f'<li><span class="muted">{_esc(s.get("name", ""))}</span></li>'
                    for s in setup
                )
                setup_html = f'<h5>数据准备</h5><ul>{setup_items}</ul>'

            # Teardown rows
            teardown_html = ""
            if teardown:
                teardown_items = "".join(
                    f'<li><span class="muted">{_esc(t.get("name", ""))}</span></li>'
                    for t in teardown
                )
                teardown_html = f'<h5>清理</h5><ul>{teardown_items}</ul>'

            tag_badges = " ".join(
                f'<span class="badge badge-green">{_esc(t)}</span>' for t in tags[:3]
            )

            sections += f"""<details class="ctrl-group">
<summary>
<strong>{_esc(tc['name'])}</strong>
<span class="muted">({len(steps)} 步, {_esc(tc.get('type', ''))})</span>
{tag_badges}
</summary>
<div style="padding: 0.5rem 1rem;">
{f'<p class="muted" style="margin:0.5rem 0">{_esc(purpose)}</p>' if purpose else ''}
{setup_html}
{f'<h5>验证步骤 ({len(steps)})</h5>'}
<table class="data-table"><thead><tr><th>#</th><th>名称</th><th>操作</th><th>断言</th></tr></thead>
<tbody>{step_rows}</tbody></table>
{teardown_html}
</div>
</details>"""

        return f"""<section id="tests">
<h2>测试用例 ({len(test_cases)} 个流程)</h2>
{sections}
</section>"""

    def _impact_section(self, file_to_tests: dict[str, list[dict]]) -> str:
        """Code change impact analysis: given a file, find affected test cases."""
        if not file_to_tests:
            return """<section id="impact"><h2>变更影响分析</h2>
<p>未找到文件到测试用例的映射关系。请先运行 <code>testnexus analyze</code> 和 <code>testnexus generate</code>。</p></section>"""

        # Build a reverse mapping: for each page file, list affected tests
        # Deduplicate by test case ID
        impact_data = {}
        for fpath, tests in sorted(file_to_tests.items()):
            # Shorten path for display
            short_path = fpath.split("/src/")[-1] if "/src/" in fpath else fpath.split("/")[-1]
            seen_ids = set()
            unique_tests = []
            for t in tests:
                if t["id"] not in seen_ids:
                    seen_ids.add(t["id"])
                    unique_tests.append(t)
            impact_data[fpath] = {
                "short": short_path,
                "count": len(unique_tests),
                "tests": unique_tests[:10],  # Show first 10
                "total": len(unique_tests),
            }

        # Sort by count descending
        sorted_impact = sorted(impact_data.values(), key=lambda x: -x["count"])

        rows = ""
        for item in sorted_impact[:50]:
            test_list = ""
            for t in item["tests"]:
                stype = _scenario_color(t.get("status", ""))
                test_list += f'<span class="badge badge-{stype}" style="font-size:0.7rem;margin:1px">{_esc(t["name"][:40])}</span> '
            if item["total"] > 10:
                test_list += f'<span class="muted">... 还有 {item["total"] - 10} 条</span>'

            rows += f"""<tr>
<td><code title="{_esc(item['short'])}">{_esc(item['short'][:60])}</code></td>
<td style="text-align:center"><strong>{item['count']}</strong></td>
<td>{test_list}</td>
</tr>"""

        return f"""<section id="impact">
<h2>变更影响分析</h2>
<p class="muted" style="margin-bottom:1rem">输入变更的文件路径（支持模糊匹配），查看受影响的测试用例。</p>
<div class="filter-bar">
<input type="text" id="impact-search" placeholder="输入文件路径，如 pages/sign-in..." class="search-input">
</div>
<table class="data-table">
<thead><tr><th>文件路径</th><th>影响用例数</th><th>受影响的测试用例</th></tr></thead>
<tbody id="impact-tbody">{rows}</tbody>
</table>
</section>"""

    def _baseline_section(self, baselines: list[dict]) -> str:
        if not baselines:
            return """<section id="baselines"><h2>基线</h2>
<p>未记录基线。请运行 <code>testnexus record --base-url &lt;url&gt; --baseline &lt;name&gt;</code> 捕获基线。</p></section>"""

        sections = ""
        for bl in baselines:
            rows = ""
            for rec in bl.get("records", []):
                status_color = "#10b981" if 200 <= rec.get("status_code", 0) < 400 else "#ef4444"
                rows += f"""<tr>
<td><code>{_esc(rec['test_case_id'])}</code></td>
<td><span class="status-dot" style="background:{status_color}"></span> {rec.get('status_code', 0)}</td>
<td>{rec.get('duration', 0)}ms</td>
<td class="muted">{_esc(rec.get('timestamp', ''))}</td>
</tr>"""
            sections += f"""<details class="ctrl-group">
<summary><strong>{_esc(bl['name'])}</strong> <span class="muted">({bl['test_count']} 个测试, {_esc(bl['source'])}, {_esc(bl['created_at'])})</span></summary>
<table class="data-table"><thead><tr><th>测试用例</th><th>状态</th><th>耗时</th><th>时间戳</th></tr></thead>
<tbody>{rows}</tbody></table>
</details>"""

        return f"""<section id="baselines">
<h2>基线 ({len(baselines)})</h2>
{sections}
</section>"""

    def _footer(self) -> str:
        return '<footer>由 <a href="https://github.com">TestNexus</a> 生成</footer>'

    def _javascript(self) -> str:
        return """
// Module detail toggle
function toggleModuleDetail(name) {
  var detail = document.getElementById('mod-detail-' + name);
  if (detail) {
    if (detail.style.display === 'none') {
      detail.style.display = 'block';
      detail.scrollIntoView({behavior: 'smooth', block: 'start'});
    } else {
      detail.style.display = 'none';
    }
  }
}

// Test table: search, filter, pagination
(function() {
  var allTests = window.__TEST_DATA__ || [];
  var PAGE_SIZE = 50;
  var currentPage = 0;
  var filtered = allTests.slice();

  var typeFilter = 'all';
  var statusFilter = 'all';
  var moduleFilter = 'all';
  var searchText = '';

  var moduleTests = window.__MODULE_TESTS__ || {};
  // Build reverse map: test id -> module
  var testToModule = {};
  for (var mod in moduleTests) {
    if (moduleTests.hasOwnProperty(mod)) {
      var arr = moduleTests[mod];
      for (var i = 0; i < arr.length; i++) {
        testToModule[arr[i].id] = mod;
      }
    }
  }

  function renderTable() {
    var container = document.getElementById('test-table-body');
    if (!container) return;

    var start = currentPage * PAGE_SIZE;
    var end = Math.min(start + PAGE_SIZE, filtered.length);
    var page = filtered.slice(start, end);

    var html = '<table class="data-table"><thead><tr>';
    html += '<th>ID</th><th>名称</th><th>类型</th><th>场景</th><th>步骤</th><th>API</th>';
    html += '</tr></thead><tbody>';

    for (var i = 0; i < page.length; i++) {
      var tc = page[i];
      var journey = tc.journey || {};
      var steps = journey.steps || [];
      var stepCount = steps.length;
      var apiInfo = '';
      for (var j = 0; j < steps.length; j++) {
        var action = steps[j].action || {};
        if (action.method) {
          apiInfo = '<span class="method-' + action.method + '">' + esc(action.method) + '</span> <code>' + esc(action.url || '') + '</code>';
          break;
        }
      }
      var modName = testToModule[tc.id] || '-';
      var statusColor = scenarioColor(tc.status || '');

      html += '<tr>';
      html += '<td class="muted">' + esc(tc.id.substring(0, 16)) + '...</td>';
      html += '<td><strong>' + esc(tc.name) + '</strong><br><span class="muted" style="font-size:0.75rem">' + esc(modName) + '</span></td>';
      html += '<td>' + esc(tc.type) + '</td>';
      html += '<td><span class="badge badge-' + statusColor + '">' + esc(tc.status) + '</span></td>';
      html += '<td>' + stepCount + '</td>';
      html += '<td>' + apiInfo + '</td>';
      html += '</tr>';
    }

    html += '</tbody></table>';

    // Pagination
    var totalPages = Math.ceil(filtered.length / PAGE_SIZE);
    if (totalPages > 1) {
      html += '<div class="pagination">';
      html += '<button onclick="window.__testPage__(0)" ' + (currentPage === 0 ? 'disabled' : '') + '>&laquo; 首页</button>';
      html += '<button onclick="window.__testPage__(' + (currentPage - 1) + ')" ' + (currentPage === 0 ? 'disabled' : '') + '>‹ 上一页</button>';
      html += '<span class="page-info">第 ' + (currentPage + 1) + '/' + totalPages + ' 页，共 ' + filtered.length + ' 条</span>';
      html += '<button onclick="window.__testPage__(' + (currentPage + 1) + ')" ' + (currentPage >= totalPages - 1 ? 'disabled' : '') + '>下一页 ›</button>';
      html += '<button onclick="window.__testPage__(' + (totalPages - 1) + ')" ' + (currentPage >= totalPages - 1 ? 'disabled' : '') + '>末页 &raquo;</button>';
      html += '</div>';
    }

    container.innerHTML = html;

    var countEl = document.getElementById('result-count');
    if (countEl) {
      countEl.textContent = '显示 ' + filtered.length + '/' + allTests.length + ' 条';
    }
  }

  function applyFilters() {
    filtered = [];
    for (var i = 0; i < allTests.length; i++) {
      var tc = allTests[i];
      if (statusFilter !== 'all' && (tc.status || '') !== statusFilter) continue;
      if (typeFilter !== 'all' && (tc.type || '') !== typeFilter) continue;
      if (moduleFilter !== 'all') {
        var mod = testToModule[tc.id] || '';
        if (mod !== moduleFilter) continue;
      }
      if (searchText) {
        var lower = searchText.toLowerCase();
        var match = false;
        if ((tc.name || '').toLowerCase().indexOf(lower) >= 0) match = true;
        if ((tc.id || '').toLowerCase().indexOf(lower) >= 0) match = true;
        if ((tc.file_path || '').toLowerCase().indexOf(lower) >= 0) match = true;
        if ((tc.action_type || '').toLowerCase().indexOf(lower) >= 0) match = true;
        var journey = tc.journey || {};
        var steps = journey.steps || [];
        for (var j = 0; j < steps.length; j++) {
          var action = steps[j].action || {};
          if ((action.url || '').toLowerCase().indexOf(lower) >= 0) match = true;
        }
        if (!match) continue;
      }
      filtered.push(tc);
    }
    currentPage = 0;
    renderTable();
  }

  function esc(s) {
    var div = document.createElement('div');
    div.textContent = s || '';
    return div.innerHTML;
  }

  function scenarioColor(status) {
    var s = (status || '').toLowerCase();
    if (s === 'normal') return 'green';
    if (s === 'error') return 'yellow';
    if (s === 'boundary') return 'purple';
    if (s === 'missing') return 'blue';
    return 'blue';
  }

  // Bind filter buttons
  document.querySelectorAll('.filter-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var filterType = this.getAttribute('data-filter');
      var value = this.getAttribute('data-value');

      // Toggle active state within same group
      var siblings = document.querySelectorAll('.filter-btn[data-filter="' + filterType + '"]');
      siblings.forEach(function(s) { s.classList.remove('active'); });
      this.classList.add('active');

      if (filterType === 'status') statusFilter = value;
      else if (filterType === 'type') typeFilter = value;
      else if (filterType === 'module') moduleFilter = value;
      applyFilters();
    });
  });

  // Bind search
  var searchInput = document.getElementById('test-search');
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      searchText = this.value.trim();
      applyFilters();
    });
  }

  // Bind pagination
  window.__testPage__ = function(page) {
    currentPage = page;
    renderTable();
    document.getElementById('test-table').scrollIntoView({behavior: 'smooth'});
  };

  // Initial render
  renderTable();
})();

// Impact analysis search
(function() {
  var impactInput = document.getElementById('impact-search');
  if (!impactInput) return;
  impactInput.addEventListener('input', function() {
    var query = this.value.trim().toLowerCase();
    var tbody = document.getElementById('impact-tbody');
    if (!tbody) return;
    var rows = tbody.querySelectorAll('tr');
    for (var i = 0; i < rows.length; i++) {
      var code = rows[i].querySelector('code');
      var text = (code ? code.textContent : '').toLowerCase();
      rows[i].style.display = (!query || text.indexOf(query) >= 0) ? '' : 'none';
    }
  });
})();

// Module row click (legacy)
document.querySelectorAll('.module-row').forEach(function(row) {
  row.style.cursor = 'pointer';
  row.addEventListener('click', function() {
    var name = this.getAttribute('data-module');
    var detail = document.getElementById('detail-' + name);
    if (detail) {
      detail.style.display = detail.style.display === 'none' ? 'block' : 'none';
    }
  });
});
"""

    def _css(self) -> str:
        return """
:root {
  --bg: #0f172a;
  --bg-card: #1e293b;
  --bg-hover: #334155;
  --text: #e2e8f0;
  --text-muted: #94a3b8;
  --border: #334155;
  --accent: #6366f1;
  --get: #22c55e;
  --post: #3b82f6;
  --put: #f59e0b;
  --delete: #ef4444;
  --patch: #a855f7;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

header {
  background: linear-gradient(135deg, #1e1b4b, #0f172a);
  border-bottom: 1px solid var(--border);
  padding: 1.5rem 2rem;
}
.header-inner { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; }
header h1 { font-size: 1.5rem; font-weight: 700; }
.header-meta { display: flex; gap: 1rem; align-items: center; font-size: 0.85rem; color: var(--text-muted); }

.nav-bar {
  display: flex; gap: 1.5rem; margin-top: 1rem; padding-top: 1rem;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}
.nav-bar a {
  color: var(--text-muted); font-size: 0.9rem; padding: 0.25rem 0;
  border-bottom: 2px solid transparent; transition: all 0.15s;
}
.nav-bar a:hover { color: var(--text); border-bottom-color: var(--accent); text-decoration: none; }

.container { max-width: 1400px; margin: 0 auto; padding: 1.5rem 2rem; }

section { margin-bottom: 3rem; }
section h2 {
  font-size: 1.35rem; font-weight: 600; margin-bottom: 1rem;
  padding-bottom: 0.5rem; border-bottom: 1px solid var(--border);
}
section h3 { font-size: 1.1rem; margin: 1.5rem 0 0.75rem; color: var(--text-muted); }
section h4 { font-size: 1rem; margin: 1rem 0 0.5rem; }
section h5 { font-size: 0.9rem; margin: 0.75rem 0 0.5rem; color: var(--text-muted); }
section h6 { font-size: 0.85rem; margin: 0.5rem 0 0.25rem; }

.badge {
  display: inline-block; padding: 0.15rem 0.6rem; border-radius: 9999px;
  font-size: 0.75rem; font-weight: 500;
}
.badge-blue { background: #312e81; color: #a5b4fc; }
.badge-green { background: #052e16; color: #86efac; }
.badge-yellow { background: #422006; color: #fde68a; }
.badge-purple { background: #3b0764; color: #d8b4fe; }
.badge-red { background: #450a0a; color: #fca5a5; }

.stats-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1rem; margin-bottom: 1.5rem;
}
.stat-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 0.5rem; padding: 1rem; text-align: center;
}
.stat-value { display: block; font-size: 2rem; font-weight: 700; color: var(--accent); }
.stat-label { font-size: 0.85rem; color: var(--text-muted); }

.rel-list { list-style: none; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem; }
.rel-list li { display: flex; justify-content: space-between; padding: 0.4rem 0.75rem; background: var(--bg-card); border-radius: 0.35rem; font-size: 0.9rem; }
.rel-list li code { color: var(--accent); }

.data-table {
  width: 100%; border-collapse: collapse; font-size: 0.9rem;
  background: var(--bg-card); border-radius: 0.5rem; overflow: hidden;
}
.data-table thead { background: #0f172a; }
.data-table th { text-align: left; padding: 0.6rem 0.75rem; font-weight: 500; color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
.data-table td { padding: 0.5rem 0.75rem; border-top: 1px solid var(--border); vertical-align: top; }
.data-table tbody tr:hover { background: var(--bg-hover); }

/* Module grid */
.module-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 0.75rem; margin-bottom: 1rem;
}
.module-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 0.5rem; padding: 0.75rem 1rem; cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.module-card:hover { background: var(--bg-hover); border-color: var(--accent); }
.module-card.module-complete { border-color: #22c55e; }
.module-card-header {
  display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.25rem;
}
.module-card-badges { display: flex; gap: 0.3rem; flex-wrap: wrap; }
.module-card-meta { display: flex; gap: 1rem; font-size: 0.85rem; color: var(--text-muted); }
.module-desc { font-size: 0.85rem; color: var(--text-muted); margin-top: 0.25rem; }

/* Module detail */
.module-detail {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 0.5rem; padding: 1rem; margin-top: 0.75rem;
}
.module-detail ul { list-style: none; }
.module-detail li { padding: 0.25rem 0; font-size: 0.9rem; }
.module-detail li code { font-size: 0.85rem; color: var(--accent); }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
.detail-panel { background: #0f172a; border-radius: 0.5rem; padding: 0.75rem; }
.detail-panel h5 { margin-top: 0; }
.detail-panel ul { list-style: none; }
.detail-panel li { padding: 0.2rem 0; font-size: 0.85rem; }

/* Filter bar */
.filter-bar {
  display: flex; gap: 0.75rem; align-items: center; margin-bottom: 0.75rem;
}
.search-input {
  flex: 1; padding: 0.5rem 0.75rem; background: var(--bg-card);
  border: 1px solid var(--border); border-radius: 0.35rem;
  color: var(--text); font-size: 0.9rem;
}
.search-input:focus { outline: none; border-color: var(--accent); }
.filter-group {
  display: flex; gap: 0.4rem; align-items: center; margin-bottom: 0.5rem;
  flex-wrap: wrap;
}
.filter-btn {
  padding: 0.2rem 0.6rem; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 9999px; color: var(--text-muted); font-size: 0.75rem;
  cursor: pointer; transition: all 0.15s;
}
.filter-btn:hover { background: var(--bg-hover); color: var(--text); }
.filter-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

/* Pagination */
.pagination {
  display: flex; gap: 0.5rem; align-items: center; justify-content: center;
  padding: 0.75rem; margin-top: 0.5rem;
}
.pagination button {
  padding: 0.3rem 0.75rem; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 0.25rem; color: var(--text); font-size: 0.85rem; cursor: pointer;
}
.pagination button:hover:not(:disabled) { background: var(--bg-hover); }
.pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
.pagination .page-info { font-size: 0.85rem; color: var(--text-muted); padding: 0 0.5rem; }

.test-table-wrapper { margin-top: 0.5rem; }

.method-GET, .get { color: var(--get); }
.method-POST, .post { color: var(--post); }
.method-PUT, .put { color: var(--put); }
.method-DELETE, .delete { color: var(--delete); }
.method-PATCH, .patch { color: var(--patch); }

.conf-badge { padding: 0.15rem 0.5rem; border-radius: 9999px; font-size: 0.8rem; font-weight: 600; color: #fff; }
.conf-exact { color: var(--get); font-weight: 600; }
.conf-high { color: var(--post); }
.conf-medium { color: var(--put); }
.conf-low { color: var(--delete); }

.status-dot { display: inline-block; width: 0.5rem; height: 0.5rem; border-radius: 50%; }

.ctrl-group { background: var(--bg-card); border: 1px solid var(--border); border-radius: 0.5rem; margin-bottom: 0.5rem; }
.ctrl-group summary { padding: 0.75rem 1rem; cursor: pointer; user-select: none; }
.ctrl-group summary:hover { background: var(--bg-hover); }
.ctrl-group table { margin: 0; border-radius: 0; }
.ctrl-group td { font-size: 0.85rem; }

.muted { color: var(--text-muted); font-size: 0.85rem; }

.info-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 0.5rem; padding: 0.75rem 1rem; margin-bottom: 1rem;
  font-size: 0.9rem;
}

footer {
  text-align: center; padding: 2rem; color: var(--text-muted);
  border-top: 1px solid var(--border); font-size: 0.85rem;
}

@media (max-width: 768px) {
  .container { padding: 1rem; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  .module-grid { grid-template-columns: 1fr; }
  .data-table { font-size: 0.8rem; }
  .data-table th, .data-table td { padding: 0.4rem 0.5rem; }
  .filter-bar { flex-direction: column; }
}
"""


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _esc(s: str) -> str:
    return html.escape(str(s))


def _http_color(method: str) -> str:
    m = method.upper().strip()
    if m == "GET": return "GET"
    if m == "POST": return "POST"
    if m == "PUT": return "PUT"
    if m == "DELETE": return "DELETE"
    if m == "PATCH": return "PATCH"
    return "GET"


def _src_color(source: str) -> str:
    s = source.lower()
    if "frontend" in s and "backend" in s: return "green"
    if "frontend" in s: return "blue"
    if "backend" in s: return "purple"
    return "yellow"


def _conf_color(conf: float) -> str:
    if conf >= 1.0: return "#22c55e"
    if conf >= 0.9: return "#3b82f6"
    if conf >= 0.7: return "#f59e0b"
    if conf >= 0.5: return "#f97316"
    return "#ef4444"


def _type_label(ttype: str) -> str:
    """Return Chinese label for test type."""
    labels = {"api": "API 测试", "e2e": "E2E 测试", "unit": "单元测试", "integration": "集成测试"}
    return labels.get(ttype.lower(), ttype)


def _action_display(tc: dict) -> str:
    """Generate HTML display for a test case's action."""
    action_type = tc.get("action_type", "")
    action_def = tc.get("action_def", {})

    if action_type == "api_request":
        method = action_def.get("method", "")
        url = action_def.get("url", "")
        return (f'<span class="method-{_http_color(method)}">{_esc(method)}</span> '
                f'<code>{_esc(url)}</code>')
    elif action_type == "page_action":
        component = action_def.get("component", "")
        steps = action_def.get("steps", [])
        step_desc = "; ".join(s.get("description", "") for s in steps[:2])
        return f'<span class="muted">{_esc(component)}</span> — {_esc(step_desc)}'
    elif action_type == "page_navigation":
        url = action_def.get("url", "")
        return f'<span class="muted">导航至</span> <code>{_esc(url)}</code>'
    return f'<span class="muted">{_esc(action_type or tc.get("type", ""))}</span>'


def _action_type_color(atype: str) -> str:
    """Return badge color class for action type."""
    t = atype.lower()
    if t in ("fetch", "api_request"):
        return "blue"
    if t in ("click",):
        return "purple"
    if t in ("submit",):
        return "yellow"
    return "green"


def _scenario_color(status: str) -> str:
    """Return badge color class for test scenario status."""
    s = status.lower()
    if s == "normal": return "green"
    if s == "error": return "yellow"
    if s == "boundary": return "purple"
    if s == "missing": return "blue"
    if s == "invalid": return "yellow"
    return "blue"

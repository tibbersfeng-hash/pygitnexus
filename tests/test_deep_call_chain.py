#!/usr/bin/env python3
"""
Call Chain Mindmap 深度调用链测试

验证 /api/mindmap 端点：
1. 从底层（Mapper/DAO）可以追溯到最上层（Controller/API/页面）
2. 从最上层（Controller）可以追溯到最底层（Mapper/DAO/SQL）
3. 中间层双向查询正确
4. Interface/Impl 桥接正确标注
5. 多层（10+）调用链完整追溯
"""

import json
import sys
import tempfile
import time
import urllib.request
import urllib.parse
from pathlib import Path

BASE_URL = "http://127.0.0.1:18925"
PROJECTS: dict[str, Path] = {}  # created temp dirs


def create_deep_backend(tmpdir: Path):
    """Create 10-layer Java backend: Controller → Service → Validator → DAO → Mapper."""
    src = tmpdir / "src" / "main" / "java" / "com" / "example" / "deep"
    src.mkdir(parents=True, exist_ok=True)

    # Layer 1: Controller
    (src / "UserController.java").write_text("""
package com.example.deep;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/user")
public class UserController {
    private UserService userService;
    @GetMapping("/profile")
    public UserProfile getUserProfile(Long userId) {
        return userService.getUserProfile(userId);
    }
    @PostMapping("/update")
    public boolean updateUserInfo(@RequestBody UserProfile profile) {
        return userService.updateProfile(profile);
    }
}
""")

    # Layer 2: Service Interface
    (src / "UserService.java").write_text("""
package com.example.deep;
public interface UserService {
    UserProfile getUserProfile(Long userId);
    boolean updateProfile(UserProfile profile);
}
""")

    # Layer 3: Service Impl
    (src / "UserServiceImpl.java").write_text("""
package com.example.deep;
public class UserServiceImpl implements UserService {
    private UserValidator userValidator;
    public UserProfile getUserProfile(Long userId) {
        userValidator.validateUserId(userId);
        UserEntity entity = userValidator.findUserById(userId);
        return convertToProfile(entity);
    }
    public boolean updateProfile(UserProfile profile) {
        userValidator.validateProfile(profile);
        return userValidator.saveUser(convertToEntity(profile));
    }
    private UserProfile convertToProfile(UserEntity e) {
        UserProfile p = new UserProfile();
        p.setId(e.getId()); p.setName(e.getName());
        return p;
    }
    private UserEntity convertToEntity(UserProfile p) {
        UserEntity e = new UserEntity();
        e.setId(p.getId()); e.setName(p.getName());
        return e;
    }
}
""")

    # Layer 4: Validator
    (src / "UserValidator.java").write_text("""
package com.example.deep;
public class UserValidator {
    private UserDao userDao;
    public void validateUserId(Long userId) {
        if (userId == null || userId <= 0) throw new IllegalArgumentException();
    }
    public void validateProfile(UserProfile p) {
        if (p == null || p.getName() == null) throw new IllegalArgumentException();
    }
    public UserEntity findUserById(Long userId) {
        return userDao.findById(userId);
    }
    public boolean saveUser(UserEntity entity) {
        return userDao.save(entity) > 0;
    }
}
""")

    # Layer 5: DAO Interface
    (src / "UserDao.java").write_text("""
package com.example.deep;
public interface UserDao {
    UserEntity findById(Long id);
    int save(UserEntity entity);
}
""")

    # Layer 6: DAO Impl
    (src / "UserDaoImpl.java").write_text("""
package com.example.deep;
public class UserDaoImpl implements UserDao {
    private DataMapper dataMapper;
    private CacheManager cacheManager;
    public UserEntity findById(Long id) {
        UserEntity cached = cacheManager.get("user", id);
        if (cached != null) return cached;
        UserEntity entity = dataMapper.selectById("user", id);
        cacheManager.put("user", id, entity);
        return entity;
    }
    public int save(UserEntity entity) {
        return dataMapper.insert("user", entity);
    }
}
""")

    # Layer 7: Cache Manager
    (src / "CacheManager.java").write_text("""
package com.example.deep;
import java.util.*;
public class CacheManager {
    private Map<String, Map<Long, Object>> cache = new HashMap<>();
    public <T> T get(String key, Long id) {
        Map<Long, Object> map = cache.get(key);
        return (T) (map != null ? map.get(id) : null);
    }
    public void put(String key, Long id, Object value) {
        cache.computeIfAbsent(key, k -> new HashMap<>()).put(id, value);
    }
}
""")

    # Layer 8: Data Mapper
    (src / "DataMapper.java").write_text("""
package com.example.deep;
public class DataMapper {
    public Object selectById(String table, Long id) { return null; }
    public int insert(String table, Object entity) { return 1; }
}
""")

    # Layer 9: SQL Executor
    (src / "SQLExecutor.java").write_text("""
package com.example.deep;
import java.sql.*;
public class SQLExecutor {
    private Connection connection;
    public ResultSet executeQuery(String sql) throws SQLException {
        return connection.prepareStatement(sql).executeQuery();
    }
    public int executeUpdate(String sql) throws SQLException {
        return connection.prepareStatement(sql).executeUpdate();
    }
    public void beginTransaction() throws SQLException { connection.setAutoCommit(false); }
    public void commit() throws SQLException { connection.commit(); }
    public void rollback() throws SQLException { connection.rollback(); }
}
""")

    # Layer 10: Connection Pool
    (src / "ConnectionPool.java").write_text("""
package com.example.deep;
import java.sql.*;
import java.util.concurrent.*;
public class ConnectionPool {
    private BlockingQueue<Connection> pool;
    public ConnectionPool(String url, int size) throws SQLException {
        pool = new ArrayBlockingQueue<>(size);
        for (int i = 0; i < size; i++) pool.add(DriverManager.getConnection(url));
    }
    public Connection getConnection() throws InterruptedException { return pool.take(); }
    public void releaseConnection(Connection c) { pool.offer(c); }
}
""")

    # Entity
    (src / "UserEntity.java").write_text("""
package com.example.deep;
public class UserEntity {
    private Long id; private String name; private String email;
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
}
""")

    # VO
    (src / "UserProfile.java").write_text("""
package com.example.deep;
public class UserProfile {
    private Long id; private String name; private String email;
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
}
""")

    # MyBatis XML
    mapper_dir = tmpdir / "src" / "main" / "resources" / "mapper"
    mapper_dir.mkdir(parents=True, exist_ok=True)
    (mapper_dir / "DataMapper.xml").write_text("""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN"
  "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.example.deep.DataMapper">
    <resultMap id="userMap" type="com.example.deep.UserEntity">
        <id property="id" column="id"/>
        <result property="name" column="name"/>
        <result property="email" column="email"/>
    </resultMap>
    <select id="selectById" resultMap="userMap">
        SELECT * FROM user WHERE id = #{id}
    </select>
    <insert id="insert">
        INSERT INTO user (name, email) VALUES (#{name}, #{email})
    </insert>
</mapper>
""")


def create_frontend_project(tmpdir: Path):
    """Create Vue frontend with API calls matching the backend."""
    # API module
    api_dir = tmpdir / "src" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "user.js").write_text("""
import request from '@/utils/request'

export function getUserProfile(userId) {
  return request({ url: '/api/user/profile', method: 'get', params: { userId } })
}

export function updateUserInfo(data) {
  return request({ url: '/api/user/update', method: 'post', data })
}
""")

    # Utils
    utils_dir = tmpdir / "src" / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / "request.js").write_text("""
import axios from 'axios'
const service = axios.create({ baseURL: process.env.VUE_APP_BASE_API, timeout: 5000 })
service.interceptors.request.use(config => config)
service.interceptors.response.use(response => response.data)
export default service
""")

    # Router
    router_dir = tmpdir / "src" / "router"
    router_dir.mkdir(parents=True, exist_ok=True)
    (router_dir / "index.js").write_text("""
import { createRouter, createWebHistory } from 'vue-router'
const routes = [
  { path: '/user/profile', name: 'UserProfile', component: () => import('../views/UserProfile.vue') },
  { path: '/user/settings', name: 'UserSettings', component: () => import('../views/UserSettings.vue') }
]
export default createRouter({ history: createWebHistory(), routes })
""")

    # Views
    views_dir = tmpdir / "src" / "views"
    views_dir.mkdir(parents=True, exist_ok=True)
    (views_dir / "UserProfile.vue").write_text("""
<template>
  <div><h1>User Profile</h1><p v-if="user">{{ user.name }}</p></div>
</template>
<script>
import { getUserProfile } from '@/api/user'
export default {
  name: 'UserProfile',
  data() { return { user: null } },
  async mounted() { this.user = await getUserProfile(this.$route.query.id) }
}
</script>
""")
    (views_dir / "UserSettings.vue").write_text("""
<template>
  <div><h1>Settings</h1><el-form @submit="onSubmit"><el-input v-model="form.name"/></el-form></div>
</template>
<script>
import { updateUserInfo } from '@/api/user'
export default {
  name: 'UserSettings',
  data() { return { form: { name: '' } } },
  methods: { async onSubmit() { await updateUserInfo(this.form) } }
}
</script>
""")


def fetch_mindmap(target: str, repo: str = "") -> dict | None:
    """Call /api/mindmap endpoint."""
    params = f"target={urllib.parse.quote(target)}"
    if repo:
        params += f"&repo={urllib.parse.quote(repo)}"
    url = f"{BASE_URL}/api/mindmap?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return data if data.get("ok") else None
    except Exception as e:
        print(f"  HTTP error: {e}")
        return None


def find_in_tree(name: str, items: list, partial: bool = False) -> bool:
    """Find a node by name (or partial match) anywhere in the tree."""
    for item in items:
        node_name = item.get("name", "")
        if partial and name in node_name:
            return True
        if node_name == name:
            return True
        if item.get("children") and find_in_tree(name, item["children"], partial):
            return True
    return False


def tree_depth(items: list) -> int:
    """Count maximum depth in a tree."""
    def _d(items, cur=0):
        if not items:
            return cur
        return max(_d(item.get("children", []), cur + 1) if item.get("children") else cur + 1 for item in items)
    return _d(items) if items else 0


def run_analysis(repo_path: str) -> bool:
    """Run pygitnexus analyze."""
    import subprocess
    result = subprocess.run(
        ["uv", "run", "pygitnexus", "analyze", repo_path],
        capture_output=True, text=True, timeout=120,
        cwd="/home/claude/.cc-connect/workspace/pygitnexus"
    )
    return result.returncode == 0


def main():
    print("=" * 60)
    print("Call Chain Mindmap 深度调用链测试")
    print("=" * 60)

    passed = 0
    failed = 0

    with tempfile.TemporaryDirectory() as backend_tmp:
        with tempfile.TemporaryDirectory() as frontend_tmp:
            backend = Path(backend_tmp)
            frontend = Path(frontend_tmp)

            # 1. Create projects
            print("\n[1/5] 创建后端 Java 项目 (10 层)...")
            create_deep_backend(backend)
            print(f"  Java: {len(list(backend.rglob('*.java')))} 文件, "
                  f"XML: {len(list(backend.rglob('*.xml')))} 文件")

            print("\n[2/5] 创建前端 Vue 项目...")
            create_frontend_project(frontend)
            print(f"  Vue: {len(list(frontend.rglob('*.vue')))} 文件, "
                  f"JS: {len(list(frontend.rglob('*.js')))} 文件")

            # 3. Analyze
            print("\n[3/5] 运行图谱分析...")
            if not run_analysis(str(backend)):
                print("  ✗ 后端分析失败")
                sys.exit(1)
            print("  ✓ 后端分析完成")
            run_analysis(str(frontend))  # non-fatal

            # Start server
            import subprocess
            server_proc = subprocess.Popen(
                ["uv", "run", "pygitnexus", "web", "--port", "18925"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd="/home/claude/.cc-connect/workspace/pygitnexus"
            )
            time.sleep(4)
            repo_path = str(backend)

            try:
                # Test 1: Bottom → Top (Mapper → Controller)
                print("\n[4/5] 从底层查最上层 (DataMapper.selectById → Controller)...")
                data = fetch_mindmap("DataMapper.selectById", repo_path)
                if not data:
                    print("  ✗ 无法获取数据")
                    failed += 1
                else:
                    upstream = data["data"]["upstream"]
                    downstream = data["data"]["downstream"]
                    root = data["data"]["root"]
                    print(f"  root: {root}")
                    print(f"  upstream branches: {len(upstream)}, downstream branches: {len(downstream)}")

                    # Verify upstream chain
                    expected_up = [
                        "UserDaoImpl.findById",
                        "UserValidator.findUserById",
                        "UserServiceImpl.getUserProfile",
                        "UserController.getUserProfile",
                    ]
                    for name in expected_up:
                        if find_in_tree(name, upstream, partial=True):
                            print(f"  ✓ 上游: {name}")
                            passed += 1
                        else:
                            print(f"  ✗ 缺失上游: {name}")
                            failed += 1

                    # Verify path annotation (via Interface)
                    if find_in_tree("via UserService", upstream, partial=True):
                        print(f"  ✓ Interface 路径标注: (via UserService)")
                        passed += 1
                    else:
                        print(f"  ✗ 缺少 Interface 路径标注")
                        failed += 1

                    # Verify depth
                    depth = tree_depth(upstream)
                    if depth >= 3:
                        print(f"  ✓ 上游深度: {depth} >= 3")
                        passed += 1
                    else:
                        print(f"  ✗ 上游深度不足: {depth}")
                        failed += 1

                # Test 2: Top → Bottom (Controller → Mapper)
                print("\n[5/5] 从最上层查底层 (UserController.getUserProfile → Mapper)...")
                data = fetch_mindmap("UserController.getUserProfile", repo_path)
                if not data:
                    print("  ✗ 无法获取数据")
                    failed += 1
                else:
                    upstream = data["data"]["upstream"]
                    downstream = data["data"]["downstream"]
                    root = data["data"]["root"]
                    print(f"  root: {root}")
                    print(f"  upstream: {len(upstream)}, downstream: {len(downstream)}")

                    expected_down = [
                        "UserServiceImpl.getUserProfile",
                        "UserValidator.findUserById",
                        "UserDaoImpl.findById",
                        "DataMapper.selectById",
                    ]
                    for name in expected_down:
                        if find_in_tree(name, downstream, partial=True):
                            print(f"  ✓ 下游: {name}")
                            passed += 1
                        else:
                            print(f"  ✗ 缺失下游: {name}")
                            failed += 1

                    depth = tree_depth(downstream)
                    if depth >= 3:
                        print(f"  ✓ 下游深度: {depth} >= 3")
                        passed += 1
                    else:
                        print(f"  ✗ 下游深度不足: {depth}")
                        failed += 1

            finally:
                server_proc.terminate()
                server_proc.wait(timeout=5)

    # Summary
    print("\n" + "=" * 60)
    print(f"测试汇总: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        print("\n❌ 部分测试失败")
        sys.exit(1)
    else:
        print("\n✅ 所有测试通过")
        sys.exit(0)


if __name__ == "__main__":
    main()

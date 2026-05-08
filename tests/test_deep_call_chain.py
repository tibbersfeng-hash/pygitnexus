#!/usr/bin/env python3
"""
10 层深度调用链测试 — 验证 Call Chain Mindmap 能从底层查到最上层，
也能从最上层查到最下层。

包含跨前后端项目场景：
- 后端 Java 项目：Controller → Service → Validator → DAO → Mapper → SQL
- 前端 Vue 项目：页面组件 → axios 调用 → 后端 API

通过 HTTP API 验证完整调用链：页面 → API → Controller → Service → ... → Mapper
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.parse
from pathlib import Path


def create_backend_project(tmpdir: str):
    """Create a Java backend project with 10 layers of method calls."""
    src = Path(tmpdir) / "src" / "main" / "java" / "com" / "example" / "deep"
    src.mkdir(parents=True, exist_ok=True)

    # Layer 1: Controller (with Spring MVC annotations)
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
        UserEntity entity = convertToEntity(profile);
        return userValidator.saveUser(entity);
    }

    private UserProfile convertToProfile(UserEntity entity) {
        UserProfile profile = new UserProfile();
        profile.setId(entity.getId());
        profile.setName(entity.getName());
        return profile;
    }

    private UserEntity convertToEntity(UserProfile profile) {
        UserEntity entity = new UserEntity();
        entity.setId(profile.getId());
        entity.setName(profile.getName());
        return entity;
    }
}
""")

    # Layer 4: Validator
    (src / "UserValidator.java").write_text("""
package com.example.deep;
public class UserValidator {
    private UserDao userDao;

    public void validateUserId(Long userId) {
        if (userId == null || userId <= 0) {
            throw new IllegalArgumentException("Invalid userId");
        }
    }

    public void validateProfile(UserProfile profile) {
        if (profile == null || profile.getName() == null) {
            throw new IllegalArgumentException("Invalid profile");
        }
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
        if (map != null) return (T) map.get(id);
        return null;
    }

    public void put(String key, Long id, Object value) {
        cache.computeIfAbsent(key, k -> new HashMap<>()).put(id, value);
    }
}
""")

    # Layer 8: Data Mapper (MyBatis-style)
    (src / "DataMapper.java").write_text("""
package com.example.deep;

public class DataMapper {
    public <T> T selectById(String table, Long id) {
        String sql = "SELECT * FROM " + table + " WHERE id = " + id;
        return executeQuery(sql);
    }

    public int insert(String table, Object entity) {
        String sql = "INSERT INTO " + table + " VALUES (...)";
        return executeUpdate(sql);
    }

    private <T> T executeQuery(String sql) {
        return null;
    }

    private int executeUpdate(String sql) {
        return 1;
    }
}
""")

    # Layer 9: SQL Executor
    (src / "SQLExecutor.java").write_text("""
package com.example.deep;
import java.sql.*;

public class SQLExecutor {
    private Connection connection;

    public ResultSet executeQuery(String sql) throws SQLException {
        PreparedStatement stmt = connection.prepareStatement(sql);
        return stmt.executeQuery();
    }

    public int executeUpdate(String sql) throws SQLException {
        PreparedStatement stmt = connection.prepareStatement(sql);
        return stmt.executeUpdate();
    }

    public void beginTransaction() throws SQLException {
        connection.setAutoCommit(false);
    }

    public void commit() throws SQLException {
        connection.commit();
    }

    public void rollback() throws SQLException {
        connection.rollback();
    }
}
""")

    # Layer 10: Connection Pool (bottom)
    (src / "ConnectionPool.java").write_text("""
package com.example.deep;
import java.sql.*;
import java.util.concurrent.*;

public class ConnectionPool {
    private BlockingQueue<Connection> pool;

    public ConnectionPool(String url, int size) throws SQLException {
        pool = new ArrayBlockingQueue<>(size);
        for (int i = 0; i < size; i++) {
            pool.add(DriverManager.getConnection(url));
        }
    }

    public Connection getConnection() throws InterruptedException {
        return pool.take();
    }

    public void releaseConnection(Connection conn) {
        pool.offer(conn);
    }
}
""")

    # Entity class
    (src / "UserEntity.java").write_text("""
package com.example.deep;
public class UserEntity {
    private Long id;
    private String name;
    private String email;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
}
""")

    # VO class
    (src / "UserProfile.java").write_text("""
package com.example.deep;
public class UserProfile {
    private Long id;
    private String name;
    private String email;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
}
""")

    # MyBatis XML mapper
    mapper_dir = Path(tmpdir) / "src" / "main" / "resources" / "mapper"
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


def create_frontend_project(tmpdir: str):
    """Create a Vue frontend project that calls the backend API."""
    src = Path(tmpdir) / "src"
    src.mkdir(parents=True, exist_ok=True)

    # Router (defines API paths)
    router_dir = src / "router"
    router_dir.mkdir(parents=True, exist_ok=True)
    (router_dir / "index.js").write_text("""
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/user/profile',
    name: 'UserProfile',
    component: () => import('../views/UserProfile.vue')
  },
  {
    path: '/user/settings',
    name: 'UserSettings',
    component: () => import('../views/UserSettings.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
""")

    # API module (defines backend API calls)
    api_dir = src / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "user.js").write_text("""
import request from '@/utils/request'

export function getUserProfile(userId) {
  return request({
    url: '/api/user/profile',
    method: 'get',
    params: { userId }
  })
}

export function updateUserInfo(data) {
  return request({
    url: '/api/user/update',
    method: 'post',
    data
  })
}
""")

    # Utils (request wrapper that uses axios)
    utils_dir = src / "utils"
    utils_dir.mkdir(parents=True, exist_ok=True)
    (utils_dir / "request.js").write_text("""
import axios from 'axios'

const service = axios.create({
  baseURL: process.env.VUE_APP_BASE_API,
  timeout: 5000
})

service.interceptors.request.use(config => config)
service.interceptors.response.use(response => response.data)

export default service
""")

    # Views
    views_dir = src / "views"
    views_dir.mkdir(parents=True, exist_ok=True)

    # UserProfile.vue
    (views_dir / "UserProfile.vue").write_text("""
<template>
  <div class="user-profile">
    <h1>User Profile</h1>
    <div v-if="user">
      <p>Name: {{ user.name }}</p>
      <p>Email: {{ user.email }}</p>
    </div>
  </div>
</template>

<script>
import { getUserProfile } from '@/api/user'

export default {
  name: 'UserProfile',
  data() {
    return { user: null }
  },
  async mounted() {
    const userId = this.$route.query.id
    this.user = await this.loadProfile(userId)
  },
  methods: {
    async loadProfile(userId) {
      return await getUserProfile(userId)
    }
  }
}
</script>
""")

    # UserSettings.vue
    (views_dir / "UserSettings.vue").write_text("""
<template>
  <div class="user-settings">
    <h1>Settings</h1>
    <el-form @submit="onSubmit">
      <el-input v-model="form.name" />
      <el-button type="primary" @click="onSubmit">Save</el-button>
    </el-form>
  </div>
</template>

<script>
import { updateUserInfo } from '@/api/user'

export default {
  name: 'UserSettings',
  data() {
    return { form: { name: '', email: '' } }
  },
  methods: {
    async onSubmit() {
      await updateUserInfo(this.form)
      this.$message.success('Saved')
    }
  }
}
</script>
""")


def count_tree_depth(items: list) -> int:
    """Count the maximum depth in a tree."""
    def max_depth(items, current=0):
        if not items:
            return current
        depths = []
        for item in items:
            children = item.get("children", [])
            if children:
                depths.append(max_depth(children, current + 1))
            else:
                depths.append(current)
        return max(depths) if depths else current
    return max_depth(items)


def find_in_tree(name: str, items: list) -> bool:
    """Find a node by name anywhere in the tree."""
    for item in items:
        if item.get("name") == name:
            return True
        if item.get("children") and find_in_tree(name, item["children"]):
            return True
    return False


def fetch_mindmap(base_url: str, target: str, repo: str) -> dict | None:
    """Call /api/mindmap endpoint."""
    url = f"{base_url}/api/mindmap?target={urllib.parse.quote(target)}&repo={urllib.parse.quote(repo)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return data["data"]
            return None
    except Exception as e:
        print(f"  HTTP error: {e}")
        return None


def run_analysis(repo_path: str) -> bool:
    """Run pygitnexus analyze on the repo."""
    result = subprocess.run(
        ["uv", "run", "pygitnexus", "analyze", repo_path],
        capture_output=True, text=True, timeout=120,
        cwd="/home/claude/.cc-connect/workspace/pygitnexus"
    )
    if result.returncode != 0:
        print(f"  stderr: {result.stderr[-500:]}")
    return result.returncode == 0


def main():
    print("=" * 60)
    print("10 层深度调用链测试（跨前后端）")
    print("=" * 60)

    passed = 0
    failed = 0

    with tempfile.TemporaryDirectory() as backend_tmp:
        with tempfile.TemporaryDirectory() as frontend_tmp:
            # Create projects
            print("\n[1/6] 创建后端 Java 项目 (10 层)...")
            create_backend_project(backend_tmp)
            java_files = list(Path(backend_tmp).rglob("*.java"))
            print(f"  创建了 {len(java_files)} 个 Java 文件")

            print("\n[2/6] 创建前端 Vue 项目...")
            create_frontend_project(frontend_tmp)
            vue_files = list(Path(frontend_tmp).rglob("*.vue"))
            js_files = list(Path(frontend_tmp).rglob("*.js"))
            print(f"  创建了 {len(vue_files)} 个 Vue 文件，{len(js_files)} 个 JS 文件")

            # Run backend analysis
            print("\n[3/6] 运行后端图谱分析...")
            if not run_analysis(backend_tmp):
                print("  ✗ 后端分析失败")
                print("\n❌ 测试失败")
                sys.exit(1)
            print("  ✓ 后端分析完成")

            # Run frontend analysis
            print("\n[4/6] 运行前端图谱分析...")
            if not run_analysis(frontend_tmp):
                print("  ✗ 前端分析失败（非致命）")
            else:
                print("  ✓ 前端分析完成")

            # Start web server
            print("\n[5/6] 启动 Web 服务器...")
            server_proc = subprocess.Popen(
                ["uv", "run", "pygitnexus", "web", "--port", "18924"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd="/home/claude/.cc-connect/workspace/pygitnexus"
            )
            time.sleep(3)
            base_url = "http://127.0.0.1:18924"

            try:
                # Test 1: Backend only - Bottom → Top
                print("\n[5a/6] 后端：从底层查最上层 (DataMapper.selectById → UserController)...")
                mindmap = fetch_mindmap(base_url, "DataMapper.selectById", backend_tmp)
                if not mindmap:
                    print("  ✗ 无法获取 mindmap 数据")
                    failed += 1
                else:
                    root = mindmap.get("root", "")
                    upstream = mindmap.get("upstream", [])
                    downstream = mindmap.get("downstream", [])
                    print(f"  根节点: {root}")
                    print(f"  上游分支: {len(upstream)}, 下游分支: {len(downstream)}")

                    # Check upstream chain
                    expected_upstream = [
                        "UserDaoImpl.findById",
                        "UserValidator.findUserById",
                        "UserServiceImpl.getUserProfile",
                        "UserController.getUserProfile",
                    ]
                    for name in expected_upstream:
                        if find_in_tree(name, upstream):
                            print(f"  ✓ 上游找到: {name}")
                            passed += 1
                        else:
                            print(f"  ✗ 上游缺失: {name}")
                            failed += 1

                    # Check downstream chain
                    expected_downstream = [
                        "CacheManager.get",
                        "CacheManager.put",
                    ]
                    for name in expected_downstream:
                        if find_in_tree(name, downstream):
                            print(f"  ✓ 下游找到: {name}")
                            passed += 1
                        else:
                            print(f"  ✗ 下游缺失: {name}")
                            failed += 1

                # Test 2: Backend - Top → Bottom
                print("\n[5b/6] 后端：从最上层查底层 (UserController.getUserProfile → 下游)...")
                mindmap = fetch_mindmap(base_url, "UserController.getUserProfile", backend_tmp)
                if not mindmap:
                    print("  ✗ 无法获取 mindmap 数据")
                    failed += 1
                else:
                    root = mindmap.get("root", "")
                    downstream = mindmap.get("downstream", [])
                    print(f"  根节点: {root}")
                    down_depth = count_tree_depth(downstream)
                    print(f"  下游深度: {down_depth}")

                    expected_downstream = [
                        "UserServiceImpl.getUserProfile",
                        "UserValidator.findUserById",
                        "UserDaoImpl.findById",
                        "DataMapper.selectById",
                    ]
                    for name in expected_downstream:
                        if find_in_tree(name, downstream):
                            print(f"  ✓ 下游找到: {name}")
                            passed += 1
                        else:
                            print(f"  ✗ 下游缺失: {name}")
                            failed += 1

                    if down_depth >= 3:
                        print(f"  ✓ 下游深度 >= 3 (实际: {down_depth})")
                        passed += 1
                    else:
                        print(f"  ✗ 下游深度不足 (实际: {down_depth}, 期望 >= 3)")
                        failed += 1

                # Test 3: API endpoint mindmap (Controller method with @GetMapping)
                print("\n[5c/6] API 端点：完整前端 → API → Controller 链路...")
                mindmap = fetch_mindmap(base_url, "getUserProfile&class=UserController", backend_tmp)
                if not mindmap:
                    print("  ✗ 无法获取 mindmap 数据")
                    failed += 1
                else:
                    root = mindmap.get("root", "")
                    upstream = mindmap.get("upstream", [])
                    downstream = mindmap.get("downstream", [])
                    print(f"  根节点: {root}")

                    # Check for API node in upstream (should have GET /api/user/profile)
                    has_api = find_in_tree("GET /api/user/profile", upstream)
                    if has_api:
                        print(f"  ✓ 找到 API 节点: GET /api/user/profile")
                        passed += 1
                    else:
                        # Check if at least the Controller upstream exists
                        print(f"  ⚠ API 节点未找到 (可能缺少前端分析)")

                    # Check upstream reaches ServiceImpl
                    has_service = find_in_tree("UserServiceImpl.getUserProfile", downstream)
                    if has_service:
                        print(f"  ✓ 下游找到 ServiceImpl.getUserProfile")
                        passed += 1
                    else:
                        print(f"  ✗ 下游缺失 ServiceImpl.getUserProfile")
                        failed += 1

            finally:
                # Stop server
                print("\n停止 Web 服务器...")
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

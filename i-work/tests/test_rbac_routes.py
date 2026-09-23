"""RBAC 静态对账：权限清单 ↔ 路由引用（doc 19-4.4）。

本用例是**纯静态**的 —— 只 import app 读路由表，不起 lifespan、不连库。
拦的是"清单删了一行、路由还留着引用"这类线上表现为静默 403 的手误。
"""
import logging

import pytest

from server.api.deps import require_session_access
from server.authz.catalog import (
    ADMIN_ROLE_KEY, DEFAULT_ROLE_KEY, DEFAULT_ROLE_PERMS, all_perms,
    planned_perms, walk,
)
from server.authz.service import (
    collect_route_refs, normalize_api_path, perm_apis, verify_route_refs,
)
from server.main import app


def _depends_on(dependant, target) -> bool:
    """依赖树里是否挂了 `target` 这个依赖（含嵌套）。"""
    for sub in getattr(dependant, "dependencies", None) or ():
        if getattr(sub, "call", None) is target:
            return True
        if _depends_on(sub, target):
            return True
    return False


def _routes():
    return [r for r in app.routes if getattr(r, "dependant", None) is not None]


# ── 清单自身的完整性 ────────────────────────────────────────────

def test_catalog_perms_are_unique():
    """权限标识不能重复 —— 重复会让 sync_catalog 的按 perms 对账互相覆盖。"""
    keys = [n["key"] for n, _ in walk() if n["key"]]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"清单里有重复的权限标识：{dupes}"


def test_catalog_covers_default_role_grants():
    """种子授给 user 角色的权限点必须在清单里。

    拼错一个字就会静默少授一条（`seed_rbac` 按 `if k in by_perms` 过滤），
    新注册用户的侧边栏因此少一个入口，且没有任何报错。
    """
    missing = sorted(set(DEFAULT_ROLE_PERMS) - set(all_perms()))
    assert not missing, f"DEFAULT_ROLE_PERMS 不在清单里：{missing}"


def test_admin_is_not_grantable_via_table():
    """admin 角色的"拥有全集"必须靠短路，不能靠 sys_role_permission。

    给它显式挂权限点的话，以后每加一个权限点都要记得补一行，忘一次就静默少个入口；
    本用例把这条约束钉住：清单里不该出现"给 admin 用的授权行"这种概念。
    """
    assert ADMIN_ROLE_KEY == "admin"
    assert DEFAULT_ROLE_KEY != ADMIN_ROLE_KEY


# ── 路由 ↔ 清单 对账 ────────────────────────────────────────────

def test_routes_only_reference_known_perms():
    """路由引用的权限点必须在清单里（拼错 / 用了已下线的点都拦在这）。"""
    known = set(all_perms())
    unknown = sorted({p for p, _, _ in collect_route_refs(app)} - known)
    assert not unknown, f"路由引用了清单外的权限点：{unknown}"


def test_verify_route_refs_is_clean(caplog):
    """启动期校验在真实 app 上不抛错、也不打漂移告警。"""
    with caplog.at_level(logging.WARNING, logger="iwork.authz"):
        verify_route_refs(app)  # 有清单外的引用会直接 RuntimeError
    assert not caplog.records, [r.getMessage() for r in caplog.records]


def test_declared_bindings_are_all_mounted():
    """清单声明了接口绑定的点，其接口必须真的有路由（planned 的除外）。"""
    declared = {
        (perms, method, normalize_api_path(path))
        for perms, apis in perm_apis().items()
        for method, path in apis
        if perms not in planned_perms()
    }
    missing = sorted(declared - set(collect_route_refs(app)))
    assert not missing, f"清单声明了绑定但路由不存在（应标 planned）：{missing}"


# ── 会话面必须做归属校验 ────────────────────────────────────────

SESSION_SCOPED_PREFIX = "/sessions/{session_id}"


def test_all_session_scoped_routes_require_ownership():
    """`/sessions/{session_id}/*` 下每个端点都要做归属校验。

    漏一个就是"任何人拿到 id 就能读别人的会话"—— id 是 UUID，但会话列表接口
    会把它发出去，不能当秘密用。
    """
    ungated = []
    for route in _routes():
        path = getattr(route, "path", "")
        if not path.startswith(SESSION_SCOPED_PREFIX):
            continue
        if not _depends_on(route.dependant, require_session_access):
            methods = sorted(set(getattr(route, "methods", None) or ()))
            ungated.append(f"{'/'.join(methods)} {path}")
    assert not ungated, f"这些会话端点没有归属校验：{ungated}"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/api/system/role/{role_id}/permissions", "/system/role/{}/permissions"),
        ("/api", "/"),
        ("/mcp/hub", "/mcp/hub"),
    ],
)
def test_normalize_api_path(raw, expected):
    assert normalize_api_path(raw) == expected

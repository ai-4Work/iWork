"""A 模块：产物提取与归一化（artifact.py）组件测试。"""

from server.engine.artifact import (
    extract_artifact,
    normalize_artifact,
    _basename,
    _bash_target_file,
)


# ── extract_artifact ──

def test_extract_artifact_file_tools():
    assert extract_artifact("read_file", {"file_path": "src/schema.sql"}) == "schema.sql"
    assert extract_artifact("write_file", {"file_path": "out/top50.sql"}) == "top50.sql"
    assert extract_artifact("edit_file", {"file_path": "src/a.py"}) == "a.py"


def test_extract_artifact_bash_f():
    assert extract_artifact("bash", {"command": "psql -f top50_v3.sql"}) == "top50_v3.sql"


def test_extract_artifact_no_document():
    assert extract_artifact("web_search", {"query": "x"}) == ""
    assert extract_artifact("read_file", {"path": "x"}) == ""  # key 应为 file_path，不是 path
    assert extract_artifact(None, None) == ""
    assert extract_artifact("bash", {"command": "ls"}) == ""  # 无 -f


# ── _bash_target_file ──

def test_bash_target_file_edges():
    assert _bash_target_file("psql -f top50_v3.sql") == "top50_v3.sql"
    assert _bash_target_file("ls") == ""          # 无 -f
    assert _bash_target_file("psql -f") == ""     # -f 在末尾
    assert _bash_target_file("") == ""


# ── _basename ──

def test_basename_normalization():
    assert _basename("src\\schema.sql") == "schema.sql"  # 反斜杠转正斜杠
    assert _basename("a/b/") == "b"                      # 去尾斜杠
    assert _basename("top50_v3.sql") == "top50_v3.sql"


# ── normalize_artifact ──

def test_normalize_artifact():
    assert normalize_artifact("top50_v3.sql") == "top50"   # 去 _v3 + .sql
    assert normalize_artifact("schema.sql") == "schema"    # 去 .sql
    assert normalize_artifact("report_v2.csv") == "report" # 去 _v2 + 转小写
    assert normalize_artifact("") == ""


def test_normalize_artifact_version_suffix_case_sensitive():
    # 版本后缀正则 _v\d+ 大小写敏感：大写 _V2 不被剥离，仅转小写
    assert normalize_artifact("REPORT_V2.CSV") == "report_v2"

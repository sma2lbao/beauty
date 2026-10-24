"""R-24:各服务 migrations/script.py.mako 必须与 common.alembic_kit 的 canonical 一致。

Mako 的 include 指令不被 alembic 的 TemplateLookup 支持(R-24 已实测),共享模板
采用"canonical 副本 + 测试锁死"策略,防止各服务模板悄悄漂移。
"""

from __future__ import annotations

from common.alembic_kit import script_template_path


def test_service_script_templates_match_canonical() -> None:
    canonical = script_template_path()
    repo_root = canonical.parents[4]  # <repo>/packages/common/common/alembic_kit
    copies = sorted(repo_root.glob("services/*/migrations/script.py.mako"))
    assert len(copies) == 5, f"发现 {len(copies)} 个服务模板,预期 5 个"
    expected = canonical.read_bytes()
    drifted = [
        str(path.relative_to(repo_root))
        for path in copies
        if path.read_bytes() != expected
    ]
    assert not drifted, f"script.py.mako 与 canonical 不一致: {drifted}"

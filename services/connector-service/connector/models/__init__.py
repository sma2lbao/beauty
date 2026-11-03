"""ORM 模型:import 本包即注册全部表(迁移与 create_all 都依赖这一点)。"""

from connector.models import connections, oauth, tenant_apps  # noqa: F401

__all__ = ["connections", "oauth", "tenant_apps"]

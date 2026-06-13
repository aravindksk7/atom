# Stub — replaced by Task 1
from dataclasses import dataclass, field


@dataclass
class EnvironmentConfig:
    name: str = ""
    db_host: str = ""
    db_port: int = 1433
    db_name: str = ""
    db_user: str = ""
    db_password: str = ""
    db_driver: str = "ODBC Driver 17 for SQL Server"
    db_pool_size: int = 5
    db_pool_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 3600
    db_connect_timeout: int = 15
    automic_url: str = ""
    automic_user: str = ""
    automic_password: str = ""
    automic_timeout: int = 30
    automic_max_retries: int = 3
    bo_url: str = ""
    bo_user: str = ""
    bo_password: str = ""
    bo_timeout: int = 60

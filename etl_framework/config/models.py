from pydantic import BaseModel, field_validator, ConfigDict


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = ""
    db_host: str
    db_port: int = 1433
    db_name: str = ""
    db_user: str = ""
    db_password: str
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

    @field_validator("db_port")
    @classmethod
    def validate_db_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"must be 1-65535, got {v}")
        return v

    @field_validator("db_pool_size")
    @classmethod
    def validate_pool_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"must be >= 1, got {v}")
        return v

    @field_validator("db_pool_overflow")
    @classmethod
    def validate_pool_overflow(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v

    @field_validator("automic_max_retries")
    @classmethod
    def validate_max_retries(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v

    @field_validator("bo_timeout")
    @classmethod
    def validate_bo_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v

    @field_validator("automic_timeout", "db_connect_timeout", "db_pool_timeout")
    @classmethod
    def validate_positive_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v

    @field_validator("db_pool_recycle")
    @classmethod
    def validate_pool_recycle(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be >= 0, got {v}")
        return v

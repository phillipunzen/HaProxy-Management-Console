from pydantic import Field,model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

class Settings(BaseSettings):
    db_host: str = 'db'
    db_port: int = 3306
    db_name: str = 'haproxy_control'
    db_user: str = 'haproxy'
    db_password: str
    encryption_key: str
    session_secret: str
    admin_username: str = 'admin'
    admin_password: str = Field(min_length=10, max_length=200)
    cookie_secure: bool = True
    app_origin: str = 'https://localhost'
    metrics_interval: int = Field(default=30,ge=10,le=3600)
    metrics_raw_hours: int = Field(default=2,ge=1,le=24)
    metrics_fine_days: int = Field(default=1,ge=1,le=30)
    metrics_retention_days: int = Field(default=7,ge=1,le=365)
    agent_ca_file: str | None = None
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @model_validator(mode='after')
    def metrics_retention_order(self):
        if self.metrics_fine_days>self.metrics_retention_days:
            raise ValueError('METRICS_FINE_DAYS darf METRICS_RETENTION_DAYS nicht überschreiten.')
        return self

    @property
    def database_url(self):
        return URL.create('mysql+pymysql', username=self.db_user, password=self.db_password,
                          host=self.db_host, port=self.db_port, database=self.db_name,
                          query={'charset': 'utf8mb4'})

settings = Settings()

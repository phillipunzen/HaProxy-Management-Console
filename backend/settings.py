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
    admin_password: str
    cookie_secure: bool = True
    app_origin: str = 'https://localhost'
    metrics_interval: int = 30
    agent_ca_file: str | None = None
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @property
    def database_url(self):
        return URL.create('mysql+pymysql', username=self.db_user, password=self.db_password,
                          host=self.db_host, port=self.db_port, database=self.db_name,
                          query={'charset': 'utf8mb4'})

settings = Settings()

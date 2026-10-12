from urllib.parse import urlsplit
from pydantic import Field,model_validator,field_validator
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
    agent_download_origin: str | None = None
    metrics_interval: int = Field(default=30,ge=10,le=3600)
    metrics_raw_hours: int = Field(default=2,ge=1,le=24)
    metrics_fine_days: int = Field(default=1,ge=1,le=30)
    metrics_retention_days: int = Field(default=7,ge=1,le=365)
    agent_ca_file: str | None = None
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @field_validator('agent_download_origin', mode='before')
    @classmethod
    def download_origin_ok(cls, value):
        if value is None or value == '':return None
        if not isinstance(value,str) or len(value)>500 or any(ord(c)<=32 or ord(c)>=127 for c in value):
            raise ValueError('AGENT_DOWNLOAD_ORIGIN muss eine HTTP(S)-Adresse ohne Leerzeichen sein.')
        parsed=urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path not in ('','/') or parsed.query or parsed.fragment or any(c in value for c in ('\\', '"', "'")):
            raise ValueError('AGENT_DOWNLOAD_ORIGIN ohne Zugangsdaten, Pfad, Query oder Fragment angeben.')
        try:parsed.port
        except ValueError:raise ValueError('Ungültiger Port in AGENT_DOWNLOAD_ORIGIN.')
        return value.rstrip('/')

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

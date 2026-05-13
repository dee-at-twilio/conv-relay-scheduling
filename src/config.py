from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    domain: str  # bare domain, e.g. 9b81-136-226-166-181.ngrok-free.app

    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    twilio_flex_workflow_sid: str = ""

    openai_api_key: str
    openai_model: str = "gpt-4o"

    db: str = ""
    db_path: str = "scheduling.db"

    @property
    def resolved_db_path(self) -> str:
        return f"{self.db}.db" if self.db else self.db_path

    tts_voice: str = "en-US-Chirp3-HD-Aoede"
    tts_language: str = "en-US"
    transcription_language: str = "en-US"

    @property
    def http_url(self) -> str:
        return f"https://{self.domain}"

    @property
    def ws_url(self) -> str:
        return f"wss://{self.domain}/ws"


config = AppConfig()

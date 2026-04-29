from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Server
    base_url: str

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    twilio_flex_workflow_sid: str = ""

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o"

    # Airtable
    airtable_api_key: str = ""
    airtable_base_id: str = ""

    # Speech
    tts_voice: str = "en-US-Journey-O"
    tts_language: str = "en-US"
    transcription_language: str = "en-US"

    @property
    def ws_url(self) -> str:
        base = self.base_url.rstrip("/")
        return base.replace("https://", "wss://").replace("http://", "ws://") + "/ws"


config = AppConfig()

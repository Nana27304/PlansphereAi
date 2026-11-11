from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PlanSphere AI"
    environment: str = "development"
    debug: bool = False
    dev_show_otp: bool = True
    secret_key: str = "CHANGE-ME-TO-A-LONG-RANDOM-SECRET-IN-PRODUCTION"
    access_token_minutes: int = 720
    database_url: str = "sqlite:///./plansphere.db"
    storage_dir: str = "./storage"
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    frontend_url: str = "http://localhost:5173"

    # AI and online research
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    openai_advanced_model: str = ""
    ai_max_output_tokens: int = 1800
    tavily_api_key: str = ""

    # Email OTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True

    # SMS OTP
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_messaging_service_sid: str = ""

    # Public developer details
    developer_name: str = "Godelone_ EmmaG"
    developer_title: str = "Founder and Developer"
    company_name: str = "Godelone Intellectual Design Comp.LTD"
    support_email: str = "gyasie898@gmail.com"
    support_phone_primary: str = "+233257075782"
    support_phone_secondary: str = "+233597517363"
    developer_location: str = "Kumasi, Ghana"
    developer_github: str = "https://github.com/Nana27304"
    platform_ip_owner: str = "Godelone_ EmmaG (individual developer ownership)"

    # Legal document versions. Bump these when policy text changes.
    terms_version: str = "1.1-2026-11-10"
    privacy_version: str = "1.1-2026-11-10"

    # Flutterwave v3. Keep payment activation disabled until the merchant
    # account, currencies, payment channels and webhook have been approved.
    flutterwave_secret_key: str = ""
    flutterwave_webhook_hash: str = ""
    flutterwave_api_base: str = "https://api.flutterwave.com/v3"
    flutterwave_ghs_enabled: bool = False
    flutterwave_usd_enabled: bool = False
    flutterwave_ghs_bank_transfer_enabled: bool = False

    # Enter the local GHS Advanced price once chosen. No exchange rate is guessed.
    advanced_price_usd: str = "10.00"
    advanced_price_ghs: str = ""

    # Professional is free and must never be sent to paid checkout.
    professional_price_usd: str = "0.00"
    professional_price_ghs: str = "0.00"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]


settings = Settings()

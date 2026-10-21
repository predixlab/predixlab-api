from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr, field_validator
from typing import List, Optional

class Settings(BaseSettings):
    # Database
    mongodb_url: str = "mongodb://localhost:27017"
    database_name: str = "predixlab"
    market_overviews_collection: str = "market_overviews"
    alpharank_collection: str = "alpharank"
    alpharank_generations_collection: str = "alpharank_generations"

    # JWT
    secret_key: str = "your-secret-key-here"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Email verification-code throttling (shared through MongoDB)
    otp_request_email_limit: int = Field(default=5, ge=1)
    otp_request_ip_limit: int = Field(default=20, ge=1)
    otp_request_window_seconds: int = Field(default=3600, ge=1)
    otp_verify_email_limit: int = Field(default=10, ge=1)
    otp_verify_ip_limit: int = Field(default=30, ge=1)
    otp_verify_window_seconds: int = Field(default=900, ge=1)
    otp_resend_cooldown_seconds: int = Field(default=60, ge=1)

    # Set to zero when requests do not pass through a trusted ingress.
    trusted_proxy_hops: int = Field(default=1, ge=0)
    trust_x_real_ip: bool = False

    # OAuth session signing
    session_secret_key: SecretStr = Field(
        default=SecretStr(""), min_length=32, validate_default=True
    )

    # Email
    acs_connection_string: str = ""
    acs_sender: str = ""
    # Delivery backend: "acs" sends via Azure Communication Services,
    # "console" logs the message instead (useful for local development).
    email_backend: str = "acs"

    # OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_callback_url: str = "http://localhost:8000/api/v1/auth/google/callback"

    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_oauth_callback_url: str = "http://localhost:8000/api/v1/auth/microsoft/callback"

    # OAuth redirect
    # Frontend page that will receive query params from OAuth callbacks, e.g.
    # http://localhost:3000/auth/callback
    oauth_redirect_url: str = "http://localhost:3000/auth/callback"

    # App settings
    app_name: str = "PredixLab"
    app_version: str = "1.0.0"
    public_api_url: str = "http://localhost:8000"
    user_notice_version: str = "2026-08-09"
    debug: bool = False
    enable_api_docs: Optional[bool] = None
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:8080"]

    # File paths
    predictions_dir: str = "data/predictions"
    picks_dir: str = "data/picks"
    predixlab_data_dir: Optional[str] = None
    us_stock_data_dir: str = "data/stock_data/us_data"
    us_reference_data_dir: Optional[str] = None
    us_reference_symbols: str = "SPY,QQQ,IWM,DIA"
    us_index_path: str = "data/instruments/us.txt"
    us_calendar_path: str = "data/calendar/us.txt"

    # Membership settings
    free_user_days_limit: int = 10  # Non-members can only view data older than 10 days

    # Referral program
    referral_program_enabled: bool = True
    referral_frontend_url: str = "http://localhost:3000/register"
    referral_code_length: int = 8
    referral_inviter_reward_days: int = 15
    referral_invitee_reward_days: int = 7
    referral_max_rewarded_referrals: int = 20

    # Stripe settings
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_monthly_price_id: str = ""
    stripe_yearly_price_id: str = ""
    stripe_trial_period_days: int = 30

    def is_index_symbol(self, symbol: str) -> bool:
        """Classify symbols using the configured reference list, ignoring case."""
        return symbol.upper() in {
            entry.strip().upper()
            for entry in self.us_reference_symbols.split(",")
            if entry.strip()
        }

    @field_validator("session_secret_key")
    @classmethod
    def validate_session_secret_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().strip()) < 32:
            raise ValueError(
                "SESSION_SECRET_KEY must contain at least 32 characters "
                "excluding surrounding whitespace"
            )
        return value

    class Config:
        env_file = ".env"
        hide_input_in_errors = True


settings = Settings()

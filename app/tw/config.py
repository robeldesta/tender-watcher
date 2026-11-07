import os


class Settings:
    def __init__(self, env=os.environ):
        self.database_url = env.get("DATABASE_URL", "sqlite:///./tw.db")
        self.secret = env.get("SECRET_KEY", "")
        self.data_dir = env.get("DATA_DIR", "./data")
        self.board_json = env.get("BOARD_JSON", "")  # keep the static board in step until the app serves it
        self.site_url = env.get("SITE_URL", "https://tenders.sonador.et").rstrip("/")
        self.anthropic_key = env.get("ANTHROPIC_API_KEY", "")
        self.secure_cookies = env.get("SECURE_COOKIES", "1") == "1"
        if not self.secret:
            raise RuntimeError("SECRET_KEY is required")

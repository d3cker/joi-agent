import os

import uvicorn

from .config import Settings
from .config_store import ConfigStore
from .app import create_app, supervised_restart_scheduler
from .security import uvicorn_security_options


def main() -> None:
    if os.getenv("JOI_CONFIG_DISABLED") == "1":
        store = None
        settings = Settings.from_env()
    else:
        store = ConfigStore.bootstrap()
        settings = store.load_settings()
    security_options = uvicorn_security_options(settings)
    uvicorn.run(
        create_app(
            settings,
            config_store=store,
            restart_scheduler=supervised_restart_scheduler(),
        ),
        host=settings.host,
        port=settings.port,
        log_level="info",
        **security_options,
    )


if __name__ == "__main__":
    main()

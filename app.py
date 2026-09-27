"""Run the dashboard locally: ``python app.py`` then open http://127.0.0.1:8050."""

from gnss_check.app import app, main, server  # noqa: F401

if __name__ == "__main__":
    main()

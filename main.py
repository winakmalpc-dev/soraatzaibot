"""
Entry point for Pella.app and other hosts that expect `main.py`.
This simply calls the bot's main() in `app.py`.
"""
from app import main


if __name__ == "__main__":
    main()

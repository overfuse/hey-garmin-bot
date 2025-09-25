from dotenv import load_dotenv, find_dotenv
import sys
from pathlib import Path


def pytest_sessionstart(session):
    # Загружаем переменные окружения из ближайшего .env (корень проекта)
    load_dotenv(dotenv_path=find_dotenv(), override=False)
    # Добавляем корень проекта в sys.path для импортов модулей (chatgpt, garmin и т.д.)
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))



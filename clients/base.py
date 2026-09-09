"""Base class for agents backed by the bounded Gemini provider adapter."""

from abc import abstractmethod

from server.config import settings
from server.integrations.llm import GeminiProvider


class Agent:
    def __init__(self, model_name: str | None = None, provider: GeminiProvider | None = None):
        self.provider = provider or GeminiProvider()
        self.model_name = model_name or settings.GOOGLE_MODEL

    @abstractmethod
    def run():
        pass

from abc import abstractmethod

from google.genai import Client

from server.config import settings


class Agent:
    def __init__(self, model_name: str | None = None):
        self.client = self._init_client()
        self.model_name = model_name or settings.GOOGLE_MODEL

    def _init_client(self):
        settings.configure_google_application_credentials()
        return Client(
            vertexai=True,
            project=settings.GOOGLE_PROJECT_ID,
            location=settings.GOOGLE_LOCATION,
        )

    @abstractmethod
    def run():
        pass
        """
        def run(self, input_query: str, system_prompt: str= None, tools: Optional[List] = None) -> str:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=input_query,
                    config=GenerateContentConfig(
                        tools=tools or [],
                        temperature=0,
                        system_instruction=system_prompt,
                    ),
                )
                return getattr(response, 'text', str(response))
            except Exception as e:
                print(f"[Agent Error] {e}")
                return f"Error occurred: {e}"
        """

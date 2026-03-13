from typing import List, Optional
from google import genai
from google.genai import Client
from google.genai.types import GenerateContentConfig
from abc import abstractmethod

# model names
# model_name="gemini-2.5-pro-preview-03-25"

class Agent:
    def __init__(self, model_name: str = "gemini-2.0-flash-lite"):
        self.client = self._init_client()
        self.model_name = model_name

    def _init_client(self):
        return Client(
            vertexai=True,
            project="agents-456517",
            location="us-central1",
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

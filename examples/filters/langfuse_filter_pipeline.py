"""
title: Langfuse Filter Pipeline
author: open-webui
date: 2024-05-30
version: 1.2
license: MIT
description: A filter pipeline that uses Langfuse.
requirements: langfuse
"""

from typing import List, Optional
from schemas import OpenAIChatMessage
import os
import uuid

from utils.pipelines.main import get_last_user_message, get_last_assistant_message
from pydantic import BaseModel
from langfuse import Langfuse
from langfuse.api.resources.commons.errors.unauthorized_error import UnauthorizedError


class Pipeline:
    class Valves(BaseModel):
        pipelines: List[str] = []
        priority: int = 0
        secret_key: str
        public_key: str
        host: str

    def __init__(self):
        self.type = "filter"
        self.name = "Langfuse Filter"
        self.valves = self.Valves(
            **{
                "pipelines": ["*"],
                "secret_key": os.getenv("LANGFUSE_SECRET_KEY", "your-secret-key-here"),
                "public_key": os.getenv("LANGFUSE_PUBLIC_KEY", "your-public-key-here"),
                "host": os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            }
        )
        self.langfuse = None
        self.chat_generations = {}

    async def on_startup(self):
        print(f"on_startup:{__name__}")
        self.set_langfuse()

    async def on_shutdown(self):
        print(f"on_shutdown:{__name__}")
        self.langfuse.flush()

    async def on_valves_updated(self):
        self.set_langfuse()

    def set_langfuse(self):
        try:
            self.langfuse = Langfuse(
                secret_key=self.valves.secret_key,
                public_key=self.valves.public_key,
                host=self.valves.host,
                debug=False,
            )
            self.langfuse.auth_check()
        except UnauthorizedError:
            print(
                "Langfuse credentials incorrect. Please re-enter your Langfuse credentials in the pipeline settings."
            )
        except Exception as e:
            print(f"Langfuse error: {e} Please re-enter your Langfuse credentials in the pipeline settings.")

    async def inlet(self, body: dict, user: Optional[dict] = None) -> dict:
    print(f"inlet:{__name__}")
    print(f"Received body: {body}")
    print(f"User: {user}")

    # Check for presence of required keys and generate chat_id if missing
    if "chat_id" not in body:
        unique_id = f"SYSTEM MESSAGE {uuid.uuid4()}"
        body["chat_id"] = unique_id
        print(f"chat_id was missing, set to: {unique_id}")

    required_keys = ["model", "messages"]
    missing_keys = [key for key in required_keys if key not in body]
    
    if missing_keys:
        error_message = f"Error: Missing keys in the request body: {', '.join(missing_keys)}"
        print(error_message)
        raise ValueError(error_message)

    # Calculate the length of each message and store the lengths instead of the actual messages
    message_lengths = [{"role": msg["role"], "length": len(msg["content"])} for msg in body["messages"]]
    
    trace = self.langfuse.trace(
        name=f"filter:{__name__}",
        input={"message_lengths": message_lengths},
        user_id=user["email"],
        metadata={"user_name": user["name"], "user_id": user["id"]},
        session_id=body["chat_id"],
    )

    generation = trace.generation(
        name=body["chat_id"],
        model=body["model"],
        input={"message_lengths": message_lengths},
        metadata={"interface": "open-webui"},
    )

    self.chat_generations[body["chat_id"]] = generation
    print(trace.get_trace_url())

    return body

async def outlet(self, body: dict, user: Optional[dict] = None) -> dict:
    print(f"outlet:{__name__}")
    if body["chat_id"] not in self.chat_generations:
        return body

    generation = self.chat_generations[body["chat_id"]]

    # Get the last user message and assistant message but track only their lengths
    user_message_length = len(get_last_user_message(body["messages"])["content"])
    generated_message_length = len(get_last_assistant_message(body["messages"])["content"])

    # End the generation by sending only the lengths of the messages
    generation.end(
        output={"length": generated_message_length},
        metadata={"interface": "open-webui", "user_message_length": user_message_length},
    )

    return body

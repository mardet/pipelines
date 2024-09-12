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
import json

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

    def repeat_sentence(self, num_chars):
        sentence = "This is an english sentence that keeps the same number of characters of the input and output sentences generated but it just repeats this sentence as many times as needed. This is in order to anonymize the conversation and still being able to calculate an aproximate amount of tokens in Langfuse that is very similar to the ones used. "
         # Repeat the sentence until you have enough characters
        repeated_sentence = sentence * (num_chars // len(sentence) + 1)
        # Take the first num_chars characters
        output = repeated_sentence[:num_chars]
        # Start iterating from num_chars to the end of repeated_sentence
        for i in range(num_chars, len(repeated_sentence)):
            char = repeated_sentence[i]
            output += char
            # Stop when the current character is a space or punctuation
            if char in " .,!?;:":
                break
        
        return output
        

    async def inlet(self, body: dict, user: Optional[dict] = None) -> dict:
        print(f"inlet:{__name__}")
        print(f"User: {user}")
        
        # Check for presence of required keys and generate chat_id if missing
        if "chat_id" not in body:
            unique_id = f"random trace id {uuid.uuid4()}"
            body["chat_id"] = unique_id
            print(f"chat_id was missing, set to: {unique_id}")
        if "session_id" not in body:
            unique_id = f"random session id {uuid.uuid4()}"
            body["session_id"] = unique_id
            print(f"session_id was missing, set to: {unique_id}")
        if "id" not in body:
            unique_id = f"random id {uuid.uuid4()}"
            body["id"] = unique_id
            print(f"id was missing, set to: {unique_id}")
        
        chat_id = body["chat_id"]
        print(f"using chat_id: {chat_id}")
        required_keys = ["model", "messages"]
        missing_keys = [key for key in required_keys if key not in body]
        
        if missing_keys:
            error_message = f"Error: Missing keys in the request body: {', '.join(missing_keys)}"
            print(error_message)
            raise ValueError(error_message)
    
        # Calculate the length of each message and store the lengths instead of the actual messages
        messages_lengths = [{"role": msg["role"], "length": len(msg["content"])} for msg in body["messages"]]
        anonymized_messages = [{"role": msg["role"], "content": self.repeat_sentence(len(msg["content"]))} for msg in body["messages"]]
        input_messages_length = sum(msg["length"] for msg in messages_lengths)

        trace = self.langfuse.trace(
            name=f"filter:{__name__}",
            input=anonymized_messages,
            user_id=user["email"],
            metadata={"user_name": user["name"], "user_id": user["id"], "messages_lengths": messages_lengths},
            id=body["chat_id"],
            session_id=body["session_id"],
        )
    
        generation = trace.generation(
            name="AI response",
            model=body["model"].split(".")[1],
            input=anonymized_messages,
            id=body["id"],
            metadata={"interface": "open-webui", "input_messages_length": input_messages_length, "generation_id": body["id"]},
           
        )
    
        self.chat_generations[body["id"]] = generation
        print(trace.get_trace_url())
        # since in the case of generating the title the outlet is not called this is an hack
        # to have also the output in langfuse with an approximation of 30 characters
        isChatTitle = False
        search_title_prompt = "Create a concise, 3-5 word title with an emoji as a title for the prompt in the given language."
        if search_title_prompt in json.dumps(body["messages"]):
            isChatTitle = True
        if isChatTitle:
            print("This is a chat title generation. Trying to send the output estimation to Langfuse.")
            generation.end(
                output=self.repeat_sentence(30),
                id=body["id"],
                metadata={"interface": "open-webui-chat-title", "output_message_length": 30},
            )
        return body
    
    async def outlet(self, body: dict, user: Optional[dict] = None) -> dict:
        print(f"outlet:{__name__}")
        if body["id"] not in self.chat_generations:
            print(f"outlet function called but id not present")
            return body

        generation = self.chat_generations[body["id"]]

        # Get the last user message and assistant message directly
        user_message = get_last_user_message(body["messages"])
        generated_message = get_last_assistant_message(body["messages"])
        # Calculate the length of the messages
        user_message_length = len(user_message)
        output_message_length = len(generated_message)
        anonymized_output=self.repeat_sentence(output_message_length)
        # End the generation by sending only the lengths of the messages
        generation.end(
            output=anonymized_output,
            metadata={"interface": "open-webui", "output_message_length": output_message_length},
            
        )

        return body
